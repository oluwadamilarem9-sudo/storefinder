"""
Polite HTTP helper for public pages and public data sources.

If a site blocks us, times out, or denies robots.txt, we record that
and move on. This module never uses proxies or CAPTCHA bypassing.
"""

from __future__ import annotations

import ssl
import time
from dataclasses import dataclass
from threading import Lock

import requests

import config
from config import USER_AGENT
from utils.normalization import normalize_domain, same_domain
from utils.robots import robots_allowed

SESSION = requests.Session()
SESSION.headers.update(
    {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/json,application/atom+xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en",
    }
)

_LOCK = Lock()


@dataclass
class FetchResult:
    url: str
    final_url: str = ""
    text: str = ""
    headers: dict | None = None
    status_code: int | None = None
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error and bool(self.text)


def fetch_public(
    url: str,
    *,
    check_robots: bool = True,
    allow_cross_domain_redirect: bool = False,
    accept: str | None = None,
    max_bytes: int | None = None,
    timeout: float | None = None,
) -> FetchResult:
    """
    Download one public URL.

    error can be:
    timeout, connection_error, 403, 404, 429, robots_denied,
    invalid_ssl, invalid_html, blocked_or_unavailable, error
    """
    if check_robots and not robots_allowed(url):
        return FetchResult(url=url, error="robots_denied")

    headers = {"Accept": accept} if accept else None
    last_error = "error"

    for attempt in range(config.MAX_RETRIES + 1):
        try:
            with _LOCK:
                time.sleep(config.REQUEST_DELAY)
                response = SESSION.get(
                    url,
                    timeout=timeout or config.REQUEST_TIMEOUT,
                    allow_redirects=True,
                    headers=headers,
                    stream=bool(max_bytes),
                )

            if not allow_cross_domain_redirect and not same_domain(url, response.url):
                return FetchResult(
                    url=url,
                    final_url=response.url,
                    status_code=response.status_code,
                    error="blocked_or_unavailable",
                )

            if response.status_code == 403:
                return FetchResult(url=url, final_url=response.url, status_code=403, error="403")
            if response.status_code == 404:
                return FetchResult(url=url, final_url=response.url, status_code=404, error="404")
            if response.status_code == 429:
                return FetchResult(url=url, final_url=response.url, status_code=429, error="429")
            if response.status_code in (401, 407, 503):
                return FetchResult(
                    url=url,
                    final_url=response.url,
                    status_code=response.status_code,
                    error="blocked_or_unavailable",
                )
            if response.status_code >= 400:
                last_error = f"{response.status_code}"
                continue

            if max_bytes:
                try:
                    raw = response.raw.read(max_bytes, decode_content=True)
                    text = raw.decode(response.encoding or "utf-8", errors="ignore")
                finally:
                    response.close()
            else:
                text = response.text

            if not text.strip():
                return FetchResult(
                    url=url,
                    final_url=response.url,
                    headers=dict(response.headers),
                    status_code=response.status_code,
                    error="invalid_html",
                )

            return FetchResult(
                url=url,
                final_url=response.url,
                text=text,
                headers=dict(response.headers),
                status_code=response.status_code,
            )

        except requests.Timeout:
            last_error = "timeout"
        except requests.exceptions.SSLError:
            last_error = "invalid_ssl"
        except ssl.SSLError:
            last_error = "invalid_ssl"
        except requests.ConnectionError:
            last_error = "connection_error"
        except requests.RequestException:
            last_error = "connection_error"
        except UnicodeError:
            last_error = "invalid_html"

        if attempt >= MAX_RETRIES:
            break

    return FetchResult(url=url, error=last_error)


def public_origin(url: str) -> str:
    """Normalized domain for the URL that finally loaded."""
    return normalize_domain(url)
