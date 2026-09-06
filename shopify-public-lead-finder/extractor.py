"""
Extract only information that is already visible on a public webpage.

Nothing here guesses missing values. If a field is not on the page,
it stays empty.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from utils import (
    COMMON_SHOPIFY_PATHS,
    PAGE_HINT_WORDS,
    absolute_url,
    first_nonempty,
    is_placeholder_email,
    normalize_url,
    same_domain,
)

EMAIL_REGEX = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}")

SOCIAL_HOSTS = {
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "tiktok": ("tiktok.com",),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com", "youtu.be"),
}


def parse_html(html: str) -> BeautifulSoup:
    """Parse HTML into a BeautifulSoup document."""
    return BeautifulSoup(html or "", "html.parser")


def extract_emails(html: str) -> list[str]:
    """
    Find email addresses that are actually written on the page.

    This does not generate or guess addresses.
    """
    found: list[str] = []
    seen: set[str] = set()

    for match in EMAIL_REGEX.findall(html or ""):
        email = match.strip().rstrip(".,;:)")
        lowered = email.lower()
        if is_placeholder_email(lowered):
            continue
        if lowered in seen:
            continue
        seen.add(lowered)
        found.append(lowered)

    return found


def extract_store_name(soup: BeautifulSoup) -> str:
    """Use public title / meta tags. Do not invent a name."""
    og_site = soup.find("meta", attrs={"property": "og:site_name"})
    if og_site and og_site.get("content"):
        return og_site["content"].strip()

    og_title = soup.find("meta", attrs={"property": "og:title"})
    if og_title and og_title.get("content"):
        return _clean_title(og_title["content"])

    if soup.title and soup.title.string:
        return _clean_title(soup.title.string)

    heading = soup.find(["h1", "h2"])
    if heading and heading.get_text(strip=True):
        return heading.get_text(strip=True)

    return ""


def _clean_title(title: str) -> str:
    """Remove common title suffixes such as '| Shop' when present."""
    text = " ".join((title or "").split())
    parts = re.split(r"\s+[|\-–—]\s+", text)
    return parts[0].strip() if parts else text


def extract_social_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """Collect the first public social profile link for each platform."""
    found = {
        "instagram": "",
        "facebook": "",
        "tiktok": "",
        "linkedin": "",
        "youtube": "",
    }

    for tag in soup.find_all("a", href=True):
        href = absolute_url(base_url, tag["href"])
        host = urlparse(href).netloc.lower()
        if host.startswith("www."):
            host = host[4:]

        for platform, hosts in SOCIAL_HOSTS.items():
            if found[platform]:
                continue
            if any(host == item or host.endswith("." + item) for item in hosts):
                found[platform] = href

    return found


def extract_country(soup: BeautifulSoup) -> str:
    """
    Only keep a country if the page clearly publishes one.

    We do not guess from the domain name or language.
    """
    country_tag = soup.find(attrs={"itemprop": "addressCountry"})
    if country_tag:
        text = country_tag.get("content") or country_tag.get_text(strip=True)
        if text:
            return text.strip()

    for attrs in (
        {"property": "business:contact_data:country_name"},
        {"name": "geo.region"},
        {"name": "og:country-name"},
    ):
        meta = soup.find("meta", attrs=attrs)
        if meta and meta.get("content"):
            return meta["content"].strip()

    return ""


def find_internal_page_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    """
    Find public same-domain links that look like contact or about pages.
    """
    pages = {
        "contact_page": "",
        "about_page": "",
    }

    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue

        full_url = absolute_url(base_url, href)
        if not same_domain(base_url, full_url):
            continue

        path = urlparse(full_url).path.lower()
        link_text = tag.get_text(" ", strip=True).lower()
        haystack = f"{path} {link_text}"

        if not any(word in haystack for word in PAGE_HINT_WORDS):
            continue

        if not pages["contact_page"] and _looks_like_contact(haystack):
            pages["contact_page"] = full_url
        elif not pages["about_page"] and _looks_like_about(haystack):
            pages["about_page"] = full_url

    return pages


def candidate_paths_for_site(base_url: str) -> list[str]:
    """Build a short list of common public pages to try on the same site."""
    root = normalize_url(base_url)
    return [root + path for path in COMMON_SHOPIFY_PATHS]


def _looks_like_contact(text: str) -> bool:
    return any(word in text for word in ("contact", "support", "help"))


def _looks_like_about(text: str) -> bool:
    return "about" in text


def merge_page_hints(*page_maps: dict[str, str]) -> dict[str, str]:
    """Keep the first contact/about URL found across several pages."""
    merged = {"contact_page": "", "about_page": ""}
    for page_map in page_maps:
        merged["contact_page"] = first_nonempty(
            merged["contact_page"], page_map.get("contact_page", "")
        )
        merged["about_page"] = first_nonempty(
            merged["about_page"], page_map.get("about_page", "")
        )
    return merged
