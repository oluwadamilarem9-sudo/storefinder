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

PIXEL_MARKERS = (
    ("Meta", ("fbevents.js", "connect.facebook.net", "fbq(")),
    ("TikTok", ("analytics.tiktok.com", "ttq.load")),
    ("Google", ("googletagmanager.com", "gtag(")),
    ("Klaviyo", ("klaviyo.com", "_learnq")),
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
CURRENCY = re.compile(
    r'(?:"currency(?:Code)?"\s*:\s*"([A-Z]{3})"|Shopify\.currency\s*=\s*\{\s*"active"\s*:\s*"([A-Z]{3})")',
    re.I,
)


@dataclass
class StorefrontProfile:
    password_page: bool = False
    product_names: list[str] = field(default_factory=list)
    currency: str = ""
    theme_name: str = ""
    has_shipping_policy: bool = False
    has_refund_policy: bool = False
    pixels: list[str] = field(default_factory=list)

    @property
    def product_count(self) -> int:
        return len(self.product_names)

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
            parts.append(f"Theme {self.theme_name}.")
        if self.has_shipping_policy:
            parts.append("Shipping policy is public.")
        if self.has_refund_policy:
            parts.append("Refund policy is public.")
        if self.pixels:
            parts.append("Public pixels: " + ", ".join(self.pixels) + ".")
        return " ".join(parts)


def published_custom_domain(domain: str, homepage_html: str) -> str:
    """
    Return the shop's own public domain when it is not a myshopify.com name.

    The domain is taken from the store's meta.json, canonical URL, or og:url.
    The myshopify.com slug is never turned into a guessed .com name.
    """
    hosts = [_domain_from_meta(domain)]
    hosts.extend(_domains_from_html(homepage_html))
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
        timeout=12,
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
    profile.theme_name = _theme_name(homepage_html)
    profile.has_shipping_policy = _has_policy(homepage_html, "shipping")
    profile.has_refund_policy = _has_policy(homepage_html, "refund")
    profile.pixels = _pixels(homepage_html)
    if profile.password_page:
        return profile
    names = _products_from_catalog(domain)
    if not names:
        names = _products_from_html(homepage_html)
    profile.product_names = names
    return profile


def is_password_page(html: str) -> bool:
    text = (html or "").lower()
    if any(marker in text for marker in PASSWORD_MARKERS):
        return True
    return "opening soon" in text and "/products/" not in text


def _products_from_catalog(domain: str) -> list[str]:
    result = fetch_public(
        website_url(domain) + "/products.json?limit=50",
        accept="application/json",
        max_bytes=400_000,
        timeout=12,
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
    return _clean_titles(str(row.get("title") or "") for row in rows if isinstance(row, dict))


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


def _theme_name(html: str) -> str:
    match = THEME_NAME.search(html or "")
    return match.group(1).strip() if match else ""


def _has_policy(html: str, kind: str) -> bool:
    text = (html or "").lower()
    return f"/policies/{kind}-policy" in text or f"{kind} policy" in text


def _pixels(html: str) -> list[str]:
    text = html or ""
    found = [name for name, markers in PIXEL_MARKERS if any(marker in text for marker in markers)]
    return found
