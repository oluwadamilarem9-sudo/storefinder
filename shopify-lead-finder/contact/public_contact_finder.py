"""
Find publicly displayed business contact details.

This module only visits common public pages on the same website.
It never guesses email addresses and never opens private profiles.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from bs4 import BeautifulSoup

from config import FAST_MODE, MAX_EXTRA_PAGES
from utils.http import fetch_public
from utils.normalization import (
    EMAIL_REGEX,
    absolute_url,
    now_iso,
    prefer_business_email,
    same_domain,
    website_url,
)

CONTACT_PATHS = (
    "/contact",
    "/contact-us",
    "/about",
    "/about-us",
    "/pages/contact",
    "/pages/about",
    "/pages/contact-us",
)

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

SOCIAL_HOSTS = {
    "instagram": ("instagram.com",),
    "facebook": ("facebook.com", "fb.com", "fb.me"),
    "tiktok": ("tiktok.com",),
    "linkedin": ("linkedin.com",),
    "youtube": ("youtube.com", "youtu.be"),
}

PUBLIC_DATE_PATTERNS = (
    re.compile(r'"published_at"\s*:\s*"([^"]+)"'),
    re.compile(r'"created_at"\s*:\s*"([^"]+)"'),
    re.compile(r'datetime="(\d{4}-\d{2}-\d{2}[^"]*)"'),
)


def find_public_contacts(domain: str, homepage_html: str = "") -> dict:
    """
    Collect public store details from the homepage and a few public pages.
    """
    root = website_url(domain)
    soup = BeautifulSoup(homepage_html or "", "html.parser")

    store_name = extract_store_name(soup)
    social = extract_social_links(soup, root)
    country = extract_country(soup)
    emails = extract_emails(homepage_html)
    pages = find_internal_page_links(soup, root)
    public_dates = extract_public_dates(homepage_html)

    extra_urls = _choose_pages(root, pages)
    if emails and FAST_MODE:
        extra_urls = extra_urls[:1]
    for page_url in extra_urls:
        result = fetch_public(page_url, allow_cross_domain_redirect=False)
        if not result.ok:
            continue
        page_soup = BeautifulSoup(result.text, "html.parser")
        emails.extend(extract_emails(result.text))
        public_dates.extend(extract_public_dates(result.text))
        extra_social = extract_social_links(page_soup, root)
        extra_country = extract_country(page_soup)
        extra_pages = find_internal_page_links(page_soup, root)
        if not store_name:
            store_name = extract_store_name(page_soup)
        for key, value in extra_social.items():
            if value and not social.get(key):
                social[key] = value
        if extra_country and not country:
            country = extra_country
        if extra_pages.get("contact_page") and not pages.get("contact_page"):
            pages["contact_page"] = extra_pages["contact_page"]
        if extra_pages.get("about_page") and not pages.get("about_page"):
            pages["about_page"] = extra_pages["about_page"]

    return {
        "store_name": store_name,
        "domain": domain,
        "public_email": prefer_business_email(emails),
        "contact_page": pages.get("contact_page", ""),
        "about_page": pages.get("about_page", ""),
        "instagram": social.get("instagram", ""),
        "facebook": social.get("facebook", ""),
        "tiktok": social.get("tiktok", ""),
        "linkedin": social.get("linkedin", ""),
        "youtube": social.get("youtube", ""),
        "country": country,
        "newest_public_content": max(public_dates) if public_dates else "",
        "last_checked": now_iso(),
    }


def extract_emails(html: str) -> list[str]:
    """Return email addresses that are actually written on the page."""
    found: list[str] = []
    for match in EMAIL_REGEX.findall(html or ""):
        found.append(match)
    for mailto in re.findall(r"mailto:([^\"'\s\?]+)", html or "", flags=re.IGNORECASE):
        found.append(mailto)
    return found


def extract_store_name(soup: BeautifulSoup) -> str:
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


def extract_social_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    found = {key: "" for key in SOCIAL_HOSTS}
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
    """Keep a country only when the page clearly publishes one."""
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


def extract_public_dates(html: str) -> list[str]:
    """Collect timestamps that are already visible in public HTML/JSON."""
    dates: list[str] = []
    for pattern in PUBLIC_DATE_PATTERNS:
        dates.extend(pattern.findall(html or ""))
    return dates


def find_internal_page_links(soup: BeautifulSoup, base_url: str) -> dict[str, str]:
    pages = {"contact_page": "", "about_page": ""}
    for tag in soup.find_all("a", href=True):
        href = tag["href"].strip()
        if href.startswith(("mailto:", "tel:", "javascript:", "#")):
            continue
        full_url = absolute_url(base_url, href)
        if not same_domain(base_url, full_url):
            continue
        path = urlparse(full_url).path.lower()
        text = tag.get_text(" ", strip=True).lower()
        haystack = f"{path} {text}"
        if not any(word in haystack for word in PAGE_HINT_WORDS):
            continue
        if not pages["contact_page"] and any(word in haystack for word in ("contact", "support", "help")):
            pages["contact_page"] = full_url
        elif not pages["about_page"] and "about" in haystack:
            pages["about_page"] = full_url
    return pages


def maybe_rdap_created(domain: str) -> str:
    """
    Read a public RDAP registration date when one is freely available.

    This is skipped for myshopify.com hostnames and whenever RDAP blocks us.
    """
    if domain.endswith(".myshopify.com"):
        return ""
    result = fetch_public(
        f"https://rdap.org/domain/{domain}",
        accept="application/rdap+json, application/json",
        allow_cross_domain_redirect=True,
        max_bytes=300_000,
        check_robots=False,
    )
    if not result.ok:
        return ""
    match = re.search(
        r'"eventAction"\s*:\s*"registration".*?"eventDate"\s*:\s*"([^"]+)"',
        result.text,
        flags=re.IGNORECASE | re.DOTALL,
    )
    return match.group(1) if match else ""


def _choose_pages(root: str, pages: dict[str, str]) -> list[str]:
    chosen: list[str] = []
    for key in ("contact_page", "about_page"):
        url = pages.get(key, "")
        if url and url not in chosen and same_domain(root, url):
            chosen.append(url)
    if not chosen:
        for path in ("/pages/contact", "/contact"):
            url = root + path
            if url not in chosen:
                chosen.append(url)
    return chosen[:MAX_EXTRA_PAGES]


def _clean_title(title: str) -> str:
    text = " ".join((title or "").split())
    parts = re.split(r"\s+[|\-–—]\s+", text)
    return parts[0].strip() if parts else text
