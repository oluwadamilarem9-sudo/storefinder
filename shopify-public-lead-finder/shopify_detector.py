"""
Detect whether a public webpage looks like a Shopify store.

This only looks at publicly visible HTML and HTTP headers.
It never tries to log in or access private admin pages.

Results:
- YES      strong public Shopify evidence
- NO       page loaded and no Shopify evidence was found
- UNKNOWN  not enough public evidence to be sure
"""

from __future__ import annotations

import re

# Strong public signals. One of these is usually enough.
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
    r"x-shopid",
)

# Weaker signals. These alone are not enough to say YES.
WEAK_PATTERNS = (
    r"shopify",
    r"myshopify",
    r"shopify_pay",
)


def _header_looks_like_shopify(headers: dict | None) -> bool:
    """Check publicly visible response headers for Shopify clues."""
    if not headers:
        return False

    lowered = {str(key).lower(): str(value).lower() for key, value in headers.items()}
    header_text = " ".join(f"{key} {value}" for key, value in lowered.items())

    shopify_header_names = (
        "x-shopid",
        "x-shopify-stage",
        "x-sorting-hat-shopid",
        "x-shopify-shop-id",
    )
    if any(name in lowered for name in shopify_header_names):
        return True

    server = lowered.get("server", "")
    if "shopify" in server:
        return True

    return "shopify" in header_text and "x-shopid" in header_text


def detect_shopify(html: str, headers: dict | None = None) -> str:
    """
    Return YES, NO, or UNKNOWN from public page content.

    UNKNOWN is used when the page is empty or the clues are too weak
    to claim the site is definitely a Shopify store.
    """
    if html is None:
        return "UNKNOWN"

    page_text = html.strip()
    if not page_text:
        return "UNKNOWN"

    if _header_looks_like_shopify(headers):
        return "YES"

    for pattern in STRONG_PATTERNS:
        if re.search(pattern, page_text, flags=re.IGNORECASE):
            return "YES"

    weak_hits = 0
    for pattern in WEAK_PATTERNS:
        if re.search(pattern, page_text, flags=re.IGNORECASE):
            weak_hits += 1

    # A single mention of the word "shopify" is not enough.
    if weak_hits >= 2:
        return "UNKNOWN"

    return "NO"
