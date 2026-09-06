"""
Normalize domains and filter placeholder emails.

example.com
https://example.com
https://example.com/
https://www.example.com
are all treated as example.com
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from urllib.parse import urljoin, urlparse

IGNORED_EMAILS = {
    "example@example.com",
    "test@example.com",
    "email@example.com",
    "name@example.com",
    "your@email.com",
    "you@example.com",
    "user@example.com",
    "support@example.invalid",
}

IGNORED_EMAIL_DOMAINS = {
    "example.com",
    "example.org",
    "example.net",
    "example.invalid",
    "test.com",
    "email.com",
    "domain.com",
    "sentry.io",
    "wixpress.com",
    "shopify.com",
}

IGNORED_LOCAL_PARTS = {
    "example",
    "test",
    "dummy",
    "placeholder",
    "noreply",
    "no-reply",
    "donotreply",
    "webpack",
    "user",
}

# Prefer generic business inboxes when several public emails exist.
PREFERRED_LOCAL_PARTS = (
    "hello",
    "contact",
    "info",
    "support",
    "sales",
    "team",
    "shop",
    "store",
    "help",
)

BLOCKED_DISCOVERY_HOSTS = {
    "shopify.com",
    "www.shopify.com",
    "myshopify.com",
    "cdn.shopify.com",
    "cdn.shopifycdn.net",
    "checkout.shopify.com",
    "accounts.shopify.com",
    "partners.shopify.com",
    "admin.shopify.com",
    "community.shopify.com",
}

MYSHOPIFY_NAME = re.compile(r"^[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com$")
EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,24}")
HEX_ONLY_SHOP = re.compile(r"^[a-f0-9]{5,12}\.myshopify\.com$")

# Hostname tokens that usually mean a demo, test, or template shop.
JUNK_DOMAIN_TOKENS = (
    "example",
    "demo",
    "test",
    "testing",
    "development",
    "dev-store",
    "devstore",
    "preview",
    "staging",
    "stage",
    "sandbox",
    "dummy",
    "sample",
    "template",
    "localhost",
    "shopify-test",
)

BLOCKED_STORE_HOSTS = {
    "example.myshopify.com",
    "www1.myshopify.com",
    "shopify.myshopify.com",
}


def now_iso() -> str:
    """UTC timestamp in a simple ISO format."""
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def normalize_domain(value: str) -> str:
    """Return a bare hostname with no scheme, path, or www."""
    text = (value or "").strip().lower()
    if not text or text.startswith("#"):
        return ""

    if "://" not in text:
        text = "https://" + text

    parsed = urlparse(text)
    host = parsed.netloc or parsed.path.split("/")[0]
    host = host.split("@")[-1]
    host = host.split(":")[0].strip().lower()
    if host.startswith("www."):
        host = host[4:]
    if host.endswith("."):
        host = host[:-1]
    if "." not in host:
        return ""
    return host


def website_url(domain: str) -> str:
    """Build a public homepage URL from a normalized domain."""
    clean = normalize_domain(domain)
    return f"https://{clean}" if clean else ""


def same_domain(base: str, candidate: str) -> bool:
    """True when both values belong to the same website."""
    return normalize_domain(base) == normalize_domain(candidate)


def absolute_url(base_url: str, href: str) -> str:
    """Turn a relative link into an absolute URL."""
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return href
    return urljoin(base_url if base_url.endswith("/") else base_url + "/", href)


def is_usable_shop_domain(domain: str) -> bool:
    """Drop Shopify platform hosts and empty values."""
    host = normalize_domain(domain)
    if not host or host in BLOCKED_DISCOVERY_HOSTS:
        return False
    if host.endswith(".shopify.com") or host.endswith(".shopifycdn.net"):
        return False
    if host.startswith("checkout.") or host.startswith("pay."):
        return False
    if host.endswith(".myshopify.com"):
        return bool(MYSHOPIFY_NAME.match(host))
    return True


def is_junk_store_domain(domain: str) -> bool:
    """
    Filter obvious demo / test / example shops.

    This is only one signal. Freshness scoring still has to decide
    whether a remaining domain looks recently launched.
    """
    host = normalize_domain(domain)
    if not host or host in BLOCKED_STORE_HOSTS:
        return True
    if HEX_ONLY_SHOP.match(host):
        return True
    slug = host.split(".myshopify.com")[0] if host.endswith(".myshopify.com") else host
    parts = set(re.split(r"[-.]", host))
    if any(token in parts for token in JUNK_DOMAIN_TOKENS):
        return True
    return any(token in slug for token in ("dev-store", "devstore", "shopify-test"))


def is_placeholder_email(email: str) -> bool:
    """Ignore sample or non-contact addresses."""
    cleaned = (email or "").strip().lower().rstrip(".,;:)>")
    if cleaned in IGNORED_EMAILS:
        return True
    if "@" not in cleaned:
        return True

    local, domain = cleaned.rsplit("@", 1)
    if domain in IGNORED_EMAIL_DOMAINS:
        return True
    if local in IGNORED_LOCAL_PARTS:
        return True
    if domain.endswith((".png", ".jpg", ".jpeg", ".gif", ".svg", ".js", ".css", ".woff")):
        return True
    return False


def prefer_business_email(emails: list[str]) -> str:
    """
    Keep the first real public email, preferring hello@ / contact@ / info@.

    This never invents an address. It only ranks addresses already found.
    """
    cleaned: list[str] = []
    seen: set[str] = set()
    for email in emails:
        item = email.strip().lower().rstrip(".,;:)>")
        if not item or item in seen or is_placeholder_email(item):
            continue
        seen.add(item)
        cleaned.append(item)

    if not cleaned:
        return ""

    def rank(address: str) -> tuple[int, str]:
        local = address.split("@", 1)[0]
        try:
            return (PREFERRED_LOCAL_PARTS.index(local), address)
        except ValueError:
            return (len(PREFERRED_LOCAL_PARTS) + 1, address)

    cleaned.sort(key=rank)
    return cleaned[0]


def parse_loose_date(value: str) -> datetime | None:
    """Parse a few public date formats. Return None instead of guessing."""
    text = (value or "").strip()
    if not text:
        return None

    formats = (
        "%Y-%m-%dT%H:%M:%S%z",
        "%Y-%m-%dT%H:%M:%SZ",
        "%Y-%m-%dT%H:%M:%S",
        "%Y-%m-%d",
        "%Y%m%d%H%M%S",
        "%Y%m%d",
    )
    cleaned = text.replace("Z", "+00:00")
    for fmt in formats:
        try:
            parsed = datetime.strptime(text if "Z" not in fmt else text.replace("+00:00", "Z"), fmt)
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=timezone.utc)
            return parsed
        except ValueError:
            continue

    try:
        return datetime.fromisoformat(cleaned)
    except ValueError:
        return None
