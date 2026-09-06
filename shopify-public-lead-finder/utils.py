"""
Shared helpers for Shopify Public Lead Finder.

These functions keep crawling polite and consistent:
- normalize and de-duplicate URLs
- stay on the same website
- check robots.txt before requesting a page
- filter out placeholder emails
"""

from __future__ import annotations

import re
import time
from urllib.parse import urljoin, urlparse, urlunparse
from urllib.robotparser import RobotFileParser

# Identify this tool clearly. Do not disguise the crawler as a browser
# in order to bypass security or access controls.
USER_AGENT = (
    "ShopifyPublicLeadFinder/1.0 "
    "(+https://localhost; public-contact research; respects robots.txt)"
)

# Pause between requests so we do not hammer a website.
REQUEST_DELAY_SECONDS = 1.5
REQUEST_TIMEOUT_SECONDS = 15
MAX_RETRIES = 2

# Words that often appear in public contact / about links.
PAGE_HINT_WORDS = (
    "contact",
    "contact-us",
    "contactus",
    "about",
    "about-us",
    "aboutus",
    "support",
    "help",
)

# Common public Shopify page paths. Only used if robots.txt allows them.
COMMON_SHOPIFY_PATHS = (
    "/pages/contact",
    "/pages/contact-us",
    "/pages/about",
    "/pages/about-us",
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
)

# Emails that are examples, not real business contacts.
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
}

_robot_cache: dict[str, RobotFileParser | None] = {}


def normalize_url(raw_url: str) -> str:
    """
    Turn messy user input into one consistent website address.

    example.com
    https://example.com
    https://example.com/
    https://www.example.com
    """
    text = (raw_url or "").strip()
    if not text or text.startswith("#"):
        return ""

    if not re.match(r"^https?://", text, flags=re.IGNORECASE):
        text = "https://" + text

    parsed = urlparse(text)
    if not parsed.netloc:
        return ""

    host = parsed.netloc.lower()
    if host.startswith("www."):
        host = host[4:]

    # Keep only scheme + host. Paths are ignored so the same store
    # is not processed twice with different homepage paths.
    return urlunparse(("https", host, "", "", "", "")).rstrip("/")


def same_domain(base_url: str, candidate_url: str) -> bool:
    """Return True when both URLs belong to the same website."""
    base_host = urlparse(normalize_url(base_url)).netloc
    candidate_host = urlparse(urljoin(base_url, candidate_url)).netloc.lower()
    if candidate_host.startswith("www."):
        candidate_host = candidate_host[4:]
    return bool(base_host) and base_host == candidate_host


def absolute_url(base_url: str, href: str) -> str:
    """Convert a relative link into a full URL."""
    if not href:
        return ""
    href = href.strip()
    if href.startswith(("mailto:", "tel:", "javascript:", "#")):
        return href
    return urljoin(base_url if base_url.endswith("/") else base_url + "/", href)


def load_robot_parser(base_url: str) -> RobotFileParser | None:
    """
    Download and cache robots.txt for a website.

    If robots.txt cannot be read, we treat the site as allowed
    for public pages but still use delays and timeouts.
    """
    root = normalize_url(base_url)
    if not root:
        return None
    if root in _robot_cache:
        return _robot_cache[root]

    robots_url = root + "/robots.txt"
    parser = RobotFileParser()
    try:
        parser.set_url(robots_url)
        parser.read()
        _robot_cache[root] = parser
        return parser
    except Exception:
        _robot_cache[root] = None
        return None


def is_allowed_by_robots(url: str, user_agent: str = USER_AGENT) -> bool:
    """Return True if robots.txt allows this public URL."""
    parser = load_robot_parser(url)
    if parser is None:
        return True
    try:
        return parser.can_fetch(user_agent, url)
    except Exception:
        return True


def polite_pause() -> None:
    """Wait a short time between requests."""
    time.sleep(REQUEST_DELAY_SECONDS)


def is_placeholder_email(email: str) -> bool:
    """Ignore sample / fake emails that are not real contacts."""
    cleaned = email.strip().lower()
    if cleaned in IGNORED_EMAILS:
        return True

    domain = cleaned.split("@")[-1]
    if domain in IGNORED_EMAIL_DOMAINS:
        return True

    ignored_local_parts = {
        "example",
        "test",
        "dummy",
        "placeholder",
        "noreply",
        "no-reply",
        "donotreply",
    }
    local_part = cleaned.split("@")[0]
    return local_part in ignored_local_parts


def first_nonempty(*values: str) -> str:
    """Return the first value that is not blank."""
    for value in values:
        if value and value.strip():
            return value.strip()
    return ""
