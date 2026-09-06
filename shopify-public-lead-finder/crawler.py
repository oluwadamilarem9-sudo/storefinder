"""
Polite public-page crawler.

Rules:
- only request publicly accessible pages
- stay on the submitted website
- respect robots.txt when it can be read
- use timeouts, retries, and delays
- never try to bypass blocks, logins, or CAPTCHAs
"""

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
from threading import Lock

import requests

from extractor import (
    candidate_paths_for_site,
    extract_country,
    extract_emails,
    extract_social_links,
    extract_store_name,
    find_internal_page_links,
    merge_page_hints,
    parse_html,
)
from shopify_detector import detect_shopify
from utils import (
    MAX_RETRIES,
    REQUEST_TIMEOUT_SECONDS,
    USER_AGENT,
    is_allowed_by_robots,
    normalize_url,
    polite_pause,
    same_domain,
)

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
    }
)

# Only one HTTP request runs at a time, even if extra pages are queued together.
_REQUEST_LOCK = Lock()


def fetch_public_page(url: str) -> tuple[str, dict | None, str]:
    """
    Download one public page.

    Returns (html, headers, error_status).
    error_status is empty when the page was fetched.
    """
    if not is_allowed_by_robots(url):
        return "", None, "blocked_or_unavailable"

    last_error = "error"
    for attempt in range(MAX_RETRIES + 1):
        try:
            with _REQUEST_LOCK:
                polite_pause()
                response = SESSION.get(
                    url,
                    timeout=REQUEST_TIMEOUT_SECONDS,
                    allow_redirects=True,
                )

            # Stop if the site moved us onto a different domain.
            if not same_domain(url, response.url):
                return "", None, "blocked_or_unavailable"

            if response.status_code in (401, 403, 407, 429, 503):
                return "", dict(response.headers), "blocked_or_unavailable"

            if response.status_code >= 400:
                last_error = "error"
                continue

            content_type = response.headers.get("Content-Type", "")
            if "text/html" not in content_type.lower() and "text/plain" not in content_type.lower():
                return "", dict(response.headers), "error"

            return response.text, dict(response.headers), ""

        except requests.Timeout:
            last_error = "timeout"
        except requests.RequestException:
            last_error = "blocked_or_unavailable"

        if attempt < MAX_RETRIES:
            continue

    return "", None, last_error


def _empty_result(website: str, shopify_status: str, status: str) -> dict[str, str]:
    return {
        "store_name": "",
        "website": website,
        "shopify_status": shopify_status,
        "email": "",
        "contact_page": "",
        "about_page": "",
        "instagram": "",
        "facebook": "",
        "tiktok": "",
        "linkedin": "",
        "country": "",
        "status": status,
    }


def collect_lead(raw_url: str) -> dict[str, str]:
    """Visit public pages for one website and collect visible lead fields."""
    website = normalize_url(raw_url)
    if not website:
        return _empty_result(raw_url, "UNKNOWN", "error")

    homepage_html, headers, fetch_status = fetch_public_page(website)
    if fetch_status:
        return _empty_result(website, "UNKNOWN", fetch_status)

    soup = parse_html(homepage_html)
    shopify_status = detect_shopify(homepage_html, headers)

    store_name = extract_store_name(soup)
    social = extract_social_links(soup, website)
    country = extract_country(soup)
    emails = extract_emails(homepage_html)
    page_hints = find_internal_page_links(soup, website)

    # If the public homepage is clearly not Shopify, stop here.
    if shopify_status == "NO":
        return {
            **_empty_result(website, shopify_status, "not_shopify"),
            "store_name": store_name,
            "instagram": social["instagram"],
            "facebook": social["facebook"],
            "tiktok": social["tiktok"],
            "linkedin": social["linkedin"],
            "country": country,
        }

    extra_urls = _choose_extra_pages(website, page_hints)
    extra_pages = _fetch_pages_politely(extra_urls)

    for page_url, page_html in extra_pages:
        page_soup = parse_html(page_html)
        emails.extend(extract_emails(page_html))
        extra_social = extract_social_links(page_soup, website)
        extra_country = extract_country(page_soup)
        extra_hints = find_internal_page_links(page_soup, website)

        for key in ("instagram", "facebook", "tiktok", "linkedin"):
            if not social[key] and extra_social[key]:
                social[key] = extra_social[key]
        if not country and extra_country:
            country = extra_country
        page_hints = merge_page_hints(page_hints, extra_hints)
        if not store_name:
            store_name = extract_store_name(page_soup)

    unique_emails = _unique(emails)
    email = unique_emails[0] if unique_emails else ""
    status = _final_status(shopify_status, email)

    return {
        "store_name": store_name,
        "website": website,
        "shopify_status": shopify_status,
        "email": email,
        "contact_page": page_hints.get("contact_page", ""),
        "about_page": page_hints.get("about_page", ""),
        "instagram": social["instagram"],
        "facebook": social["facebook"],
        "tiktok": social["tiktok"],
        "linkedin": social["linkedin"],
        "country": country,
        "status": status,
    }


def _choose_extra_pages(website: str, page_hints: dict[str, str]) -> list[str]:
    """
    Build a short same-domain list of public pages to check.

    Homepage links come first. Common Shopify paths are added only
    when a matching page was not already found.
    """
    chosen: list[str] = []
    for key in ("contact_page", "about_page"):
        url = page_hints.get(key, "")
        if url and url not in chosen and same_domain(website, url):
            chosen.append(url)

    for url in candidate_paths_for_site(website):
        if url not in chosen:
            chosen.append(url)

    # Keep the list small so we stay polite.
    return chosen[:6]


def _fetch_pages_politely(urls: list[str]) -> list[tuple[str, str]]:
    """
    Fetch a few public pages.

    Sites are processed one at a time in main.py. This small pool only
    downloads extra pages for the current website.
    """
    results: list[tuple[str, str]] = []
    if not urls:
        return results

    # Two workers is enough. More would be harder on small shops.
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = {pool.submit(fetch_public_page, url): url for url in urls}
        for future in as_completed(futures):
            url = futures[future]
            try:
                html, _headers, error_status = future.result()
            except Exception:
                continue
            if not error_status and html:
                results.append((url, html))
    return results


def _unique(values: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for value in values:
        if value not in seen:
            seen.add(value)
            unique.append(value)
    return unique


def _final_status(shopify_status: str, email: str) -> str:
    if email:
        return "success"
    if shopify_status == "YES":
        return "shopify_detected"
    return "no_public_email"
