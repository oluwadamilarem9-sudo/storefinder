"""
Detect Shopify from publicly visible HTML and HTTP headers.

This file only inspects public pages. It does not log in, open /admin,
or read private Shopify registration data.

Results:
- SHOPIFY      strong public evidence
- NOT_SHOPIFY  page loaded and no Shopify evidence was found
- UNKNOWN      not enough public evidence to be sure
"""

from __future__ import annotations

import re

STRONG_PATTERNS = (
    r"cdn\.shopify\.com",
    r"cdn\.shopifycdn\.net",
    r"checkout\.shopify\.com",
    r"[\w-]+\.myshopify\.com",
    r"window\.Shopify",
    r"Shopify\.theme",
    r"Shopify\.shop",
    r"shopify-section",
    r"/cdn/shop/",
    r"shopify-checkout-api-token",
    r"shopify-digital-wallet",
    r"Powered by Shopify",
)

WEAK_PATTERNS = (
    r"shopify",
    r"myshopify",
    r"shopify_pay",
)

SHOPIFY_HEADER_NAMES = (
    "x-shopid",
    "x-shopify-stage",
    "x-sorting-hat-shopid",
    "x-shopify-shop-id",
)


def detect_shopify(html: str | None, headers: dict | None = None) -> str:
    """Return SHOPIFY, NOT_SHOPIFY, or UNKNOWN from public page content."""
    if html is None:
        return "UNKNOWN"

    page_text = html.strip()
    if not page_text:
        return "UNKNOWN"

    if _header_looks_like_shopify(headers):
        return "SHOPIFY"

    for pattern in STRONG_PATTERNS:
        if re.search(pattern, page_text, flags=re.IGNORECASE):
            return "SHOPIFY"

    weak_hits = sum(
        1 for pattern in WEAK_PATTERNS if re.search(pattern, page_text, flags=re.IGNORECASE)
    )
    if weak_hits >= 2:
        return "UNKNOWN"

    return "NOT_SHOPIFY"


def _header_looks_like_shopify(headers: dict | None) -> bool:
    if not headers:
        return False

    lowered = {str(key).lower(): str(value).lower() for key, value in headers.items()}
    if any(name in lowered for name in SHOPIFY_HEADER_NAMES):
        return True

    server = lowered.get("server", "")
    return "shopify" in server
