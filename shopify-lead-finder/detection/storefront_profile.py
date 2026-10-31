"""
Public facts from a Shopify storefront.

This reads the homepage and the public products catalog. It does not
open admin, guess an email, or invent sales numbers.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field

from utils.http import fetch_public
from utils.normalization import is_junk_store_domain, is_usable_shop_domain, normalize_domain, website_url

PASSWORD_MARKERS = (
    "password-page",
    "shopify-section-password",
    "storefront-password",
    "form-password",
    "this store is password protected",
    "enter using password",
)

JUNK_PRODUCT_TITLES = {
    "example product",
    "sample product",
    "test product",
    "product title",
}

# Strong markers → confirmed. Weaker markers → uncertain. Never invent missing tech.
TRACKING_MARKERS: tuple[tuple[str, str, tuple[str, ...]], ...] = (
    ("Meta Pixel", "confirmed", ("fbevents.js", "connect.facebook.net/en_US/fbevents", "fbq(")),
    ("Meta Pixel", "uncertain", ("facebook.com/tr?", "connect.facebook.net")),
    (
        "Google Tag Manager",
        "confirmed",
        ("googletagmanager.com/gtm.js", "GTM-", "googletagmanager.com/ns.html"),
    ),
    (
        "Google Analytics",
        "confirmed",
        (
            "googletagmanager.com/gtag/js",
            "google-analytics.com/analytics.js",
            "google-analytics.com/ga.js",
            "gtag('config'",
            'gtag("config"',
        ),
    ),
    ("Google Analytics", "uncertain", ("www.google-analytics.com", "ga('create'")),
    ("TikTok Pixel", "confirmed", ("analytics.tiktok.com", "ttq.load", "ttq.page")),
    ("TikTok Pixel", "uncertain", ("tiktok.com/i18n/pixel",)),
    ("Klaviyo", "confirmed", ("static.klaviyo.com", "klaviyo.com/media/js", "_learnq")),
)

# Backward-compatible alias used by older callers/tests.
PIXEL_MARKERS = tuple(
    (name, markers)
    for name, confidence, markers in TRACKING_MARKERS
    if confidence == "confirmed"
)

CANONICAL_TAG = re.compile(r"<link[^>]+rel=[\"']canonical[\"'][^>]*>", re.I)
OG_URL_TAG = re.compile(r"<meta[^>]+property=[\"']og:url[\"'][^>]*>", re.I)
HREF_ATTR = re.compile(r"href=[\"']([^\"']+)", re.I)
CONTENT_ATTR = re.compile(r"content=[\"']([^\"']+)", re.I)
PRODUCT_NAME = re.compile(
    r'"@type"\s*:\s*"Product"[\s\S]{0,500}?"name"\s*:\s*"([^"\\]{2,120})"',
    re.I,
)
THEME_NAME = re.compile(r'Shopify\.theme\s*=\s*\{[^}]{0,400}?"name"\s*:\s*"([^"]+)"', re.I)
THEME_SCHEMA = re.compile(
    r'"theme_store_id"\s*:\s*\d+[^}]{0,200}?"name"\s*:\s*"([^"]+)"|'
    r'"name"\s*:\s*"([^"]+)"[^}]{0,200}?"theme_store_id"\s*:\s*\d+',
    re.I,
)
CURRENCY = re.compile(
    r'(?:"currency(?:Code)?"\s*:\s*"([A-Z]{3})"|Shopify\.currency\s*=\s*\{\s*"active"\s*:\s*"([A-Z]{3})")',
    re.I,
)


@dataclass
class TechDetection:
    name: str
    confidence: str  # confirmed | uncertain


@dataclass
class CatalogProduct:
    """One public catalog product. Empty fields mean unpublished, not invented."""

    title: str
    handle: str = ""
    product_url: str = ""
    price: str = ""
    currency: str = ""
    image_url: str = ""
    category: str = ""
    shopify_product_id: str = ""

    def as_dict(self) -> dict:
        return {
            "title": self.title,
            "handle": self.handle,
            "product_url": self.product_url,
            "price": self.price,
            "currency": self.currency,
            "image_url": self.image_url,
            "category": self.category,
            "shopify_product_id": self.shopify_product_id,
        }


@dataclass
class StorefrontProfile:
    password_page: bool = False
    product_names: list[str] = field(default_factory=list)
    products: list[CatalogProduct] = field(default_factory=list)
    currency: str = ""
    theme_name: str = ""
    theme_confidence: str = ""  # confirmed when a public theme name is found; else empty
    has_shipping_policy: bool = False
    has_refund_policy: bool = False
    tracking: list[TechDetection] = field(default_factory=list)

    @property
    def product_count(self) -> int:
        return len(self.products) if self.products else len(self.product_names)

    @property
    def pixels(self) -> list[str]:
        """Confirmed tracking names (legacy helper)."""
        return [item.name for item in self.tracking if item.confidence == "confirmed"]

    def evidence(self) -> str:
        if self.password_page:
            return "The public page is a password wall or an opening-soon page."
        if not self.product_names:
            return "The public catalog did not list a real product."
        parts = [f"Public catalog lists {self.product_count} product(s)."]
        if self.product_count == 1:
            parts.append("One-product shop.")
        if self.currency:
            parts.append(f"Currency {self.currency}.")
        if self.theme_name:
            parts.append(f"Theme {self.theme_name} ({self.theme_confidence or 'confirmed'}).")
        elif self.theme_confidence == "":
            parts.append("Theme not publicly identified.")
        if self.has_shipping_policy:
            parts.append("Shipping policy is public.")
        if self.has_refund_policy:
            parts.append("Refund policy is public.")
        if self.tracking:
            labeled = [
                f"{item.name} [{item.confidence}]" for item in self.tracking
            ]
            parts.append("Public tracking: " + ", ".join(labeled) + ".")
        else:
            parts.append("No public tracking pixels identified.")
        return " ".join(parts)


def serialize_tracking(items: list[TechDetection] | list[dict] | None) -> str:
    rows: list[dict] = []
    for item in items or []:
        if isinstance(item, TechDetection):
            name, confidence = item.name, item.confidence
        elif isinstance(item, dict):
            name, confidence = str(item.get("name") or ""), str(item.get("confidence") or "")
        else:
            continue
        if not name or confidence not in {"confirmed", "uncertain"}:
            continue
        rows.append({"name": name, "confidence": confidence})
    return json.dumps(rows, separators=(",", ":"))


def parse_tracking(raw) -> list[dict]:
    if raw is None or raw == "":
        return []
    if isinstance(raw, list):
        return [
            {"name": str(item.get("name") or ""), "confidence": str(item.get("confidence") or "")}
            for item in raw
            if isinstance(item, dict) and item.get("name")
        ]
    text = str(raw).strip()
    if not text:
        return []
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        # Legacy comma-separated confirmed names.
        return [
            {"name": part.strip(), "confidence": "confirmed"}
            for part in text.split(",")
            if part.strip()
        ]
    if not isinstance(payload, list):
        return []
    return parse_tracking(payload)


def published_custom_domain(domain: str, homepage_html: str) -> str:
    """
    Return the shop's own public domain when it is not a myshopify.com name.

    The domain is taken from the store's meta.json, canonical URL, or og:url.
    The myshopify.com slug is never turned into a guessed .com name.
    """
    hosts = _domains_from_html(homepage_html)
    if not _first_custom_host(hosts):
        hosts.append(_domain_from_meta(domain))
    return _first_custom_host(hosts)


def _first_custom_host(hosts: list[str]) -> str:
    for host in hosts:
        if not host or host.endswith(".myshopify.com") or host.endswith(".shopify.com"):
            continue
        if is_usable_shop_domain(host) and not is_junk_store_domain(host):
            return host
    return ""


def _domain_from_meta(domain: str) -> str:
    result = fetch_public(
        website_url(domain) + "/meta.json",
        accept="application/json",
        max_bytes=100_000,
        timeout=5,
    )
    if not result.ok:
        return ""
    try:
        payload = json.loads(result.text)
    except json.JSONDecodeError:
        return ""
    if not isinstance(payload, dict):
        return ""
    return normalize_domain(str(payload.get("domain") or payload.get("url") or ""))


def _domains_from_html(html: str) -> list[str]:
    hosts: list[str] = []
    for tag in CANONICAL_TAG.findall(html or ""):
        match = HREF_ATTR.search(tag)
        if match:
            hosts.append(normalize_domain(match.group(1)))
    for tag in OG_URL_TAG.findall(html or ""):
        match = CONTENT_ATTR.search(tag)
        if match:
            hosts.append(normalize_domain(match.group(1)))
    return hosts


def inspect_storefront(domain: str, homepage_html: str) -> StorefrontProfile:
    """Collect public shop facts. Product names come from the catalog or the homepage."""
    profile = StorefrontProfile(password_page=is_password_page(homepage_html))
    profile.currency = _currency(homepage_html)
    theme_name, theme_confidence = _theme_detection(homepage_html)
    profile.theme_name = theme_name
    profile.theme_confidence = theme_confidence
    profile.has_shipping_policy = _has_policy(homepage_html, "shipping")
    profile.has_refund_policy = _has_policy(homepage_html, "refund")
    profile.tracking = _detect_tracking(homepage_html)
    if profile.password_page:
        return profile
    products = fetch_catalog_products(domain, currency=profile.currency)
    if products:
        profile.products = products
        profile.product_names = [item.title for item in products]
    else:
        names = _products_from_html(homepage_html)
        profile.product_names = names
        profile.products = [
            CatalogProduct(title=title, currency=profile.currency) for title in names
        ]
    return profile


def fetch_catalog_products(domain: str, *, currency: str = "", limit: int = 50) -> list[CatalogProduct]:
    """
    Read public /products.json. Missing price/image/category stay empty.
    Never invents amounts or categories.
    """
    result = fetch_public(
        website_url(domain) + f"/products.json?limit={max(1, min(int(limit), 50))}",
        accept="application/json",
        max_bytes=400_000,
        timeout=6,
    )
    if not result.ok:
        return []
    try:
        payload = json.loads(result.text)
    except json.JSONDecodeError:
        return []
    rows = payload.get("products") if isinstance(payload, dict) else None
    if not isinstance(rows, list):
        return []
    store_currency = (currency or "").strip().upper()
    found: list[CatalogProduct] = []
    seen_titles: set[str] = set()
    for row in rows:
        if not isinstance(row, dict):
            continue
        title = " ".join(str(row.get("title") or "").split())
        key = title.lower()
        if len(title) < 2 or key in JUNK_PRODUCT_TITLES or key in seen_titles:
            continue
        seen_titles.add(key)
        handle = str(row.get("handle") or "").strip()
        product_id = str(row.get("id") or "").strip()
        product_url = ""
        if handle:
            product_url = f"{website_url(domain)}/products/{handle}"
        price = _first_variant_price(row)
        image_url = _first_image_url(row)
        category = " ".join(str(row.get("product_type") or "").split())
        found.append(
            CatalogProduct(
                title=title,
                handle=handle,
                product_url=product_url,
                price=price,
                currency=store_currency if price else "",
                image_url=image_url,
                category=category,
                shopify_product_id=product_id,
            )
        )
        if len(found) >= limit:
            break
    return found


def _first_variant_price(row: dict) -> str:
    variants = row.get("variants")
    if not isinstance(variants, list):
        return ""
    for variant in variants:
        if not isinstance(variant, dict):
            continue
        raw = variant.get("price")
        if raw is None:
            continue
        text = str(raw).strip()
        if text:
            return text
    return ""


def _first_image_url(row: dict) -> str:
    image = row.get("image")
    if isinstance(image, dict):
        src = str(image.get("src") or "").strip()
        if src:
            return src
    images = row.get("images")
    if isinstance(images, list):
        for item in images:
            if isinstance(item, dict):
                src = str(item.get("src") or "").strip()
                if src:
                    return src
            elif isinstance(item, str) and item.strip():
                return item.strip()
    return ""


def is_password_page(html: str) -> bool:
    text = (html or "").lower()
    if any(marker in text for marker in PASSWORD_MARKERS):
        return True
    return "opening soon" in text and "/products/" not in text


def _products_from_catalog(domain: str) -> list[str]:
    return [item.title for item in fetch_catalog_products(domain)]


def _products_from_html(html: str) -> list[str]:
    return _clean_titles(PRODUCT_NAME.findall(html or ""))


def _clean_titles(titles) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for title in titles:
        text = " ".join(str(title).split())
        key = text.lower()
        if len(text) < 2 or key in JUNK_PRODUCT_TITLES or key in seen:
            continue
        seen.add(key)
        found.append(text)
        if len(found) >= 50:
            break
    return found


def _currency(html: str) -> str:
    match = CURRENCY.search(html or "")
    if not match:
        return ""
    return (match.group(1) or match.group(2) or "").upper()


def _theme_detection(html: str) -> tuple[str, str]:
    """Return (theme_name, confidence). Empty name means undetected, not invented."""
    match = THEME_NAME.search(html or "")
    if match:
        name = match.group(1).strip()
        if name:
            return name, "confirmed"
    schema = THEME_SCHEMA.search(html or "")
    if schema:
        name = (schema.group(1) or schema.group(2) or "").strip()
        if name:
            return name, "uncertain"
    return "", ""


def _theme_name(html: str) -> str:
    name, _confidence = _theme_detection(html)
    return name


def _has_policy(html: str, kind: str) -> bool:
    text = (html or "").lower()
    return f"/policies/{kind}-policy" in text or f"{kind} policy" in text


def _detect_tracking(html: str) -> list[TechDetection]:
    text = html or ""
    lowered = text.lower()
    best: dict[str, str] = {}
    rank = {"confirmed": 2, "uncertain": 1}
    for name, confidence, markers in TRACKING_MARKERS:
        hit = False
        for marker in markers:
            needle = marker.lower()
            if needle in lowered or marker in text:
                hit = True
                break
        if not hit:
            continue
        prior = best.get(name)
        if prior is None or rank[confidence] > rank.get(prior, 0):
            best[name] = confidence
    order = ["Meta Pixel", "Google Tag Manager", "Google Analytics", "TikTok Pixel", "Klaviyo"]
    return [
        TechDetection(name=name, confidence=best[name])
        for name in order
        if name in best
    ]


def _pixels(html: str) -> list[str]:
    return [item.name for item in _detect_tracking(html) if item.confidence == "confirmed"]
