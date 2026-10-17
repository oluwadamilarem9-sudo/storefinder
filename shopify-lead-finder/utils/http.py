"""
Polite HTTP helper for public pages and public data sources.

If a site blocks us, times out, or denies robots.txt, we record that
and move on. This module never uses proxies or CAPTCHA bypassing.
"""

from __future__ import annotations

import socket
import ssl
import time
from dataclasses import dataclass
from threading import Event, Lock, Thread, local
from urllib.parse import urlparse

import requests

import config
from config import USER_AGENT
from utils.normalization import normalize_domain, same_domain
from utils.robots import robots_allowed

_SESSION_HEADERS = {
    "User-Agent": USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/json,application/atom+xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en",
}
_THREAD_STATE = local()
_HOST_LOCK = Lock()
_NEXT_REQUEST_AT: dict[str, float] = {}
_DNS_OK: dict[str, float] = {}
_DNS_LOCK = Lock()


def _session() -> requests.Session:
    """One connection pool per worker, so users do not share a single download lock."""
    session = getattr(_THREAD_STATE, "session", None)
    if session is None:
        session = requests.Session()
        session.headers.update(_SESSION_HEADERS)
        session.max_redirects = 4
        _THREAD_STATE.session = session
    return session


def _wait_for_host(url: str) -> None:
    """Pause only when this same website was just requested."""
    host = normalize_domain(url) or url
    delay = config.REQUEST_DELAY
    while True:
        with _HOST_LOCK:
            now = time.monotonic()
            ready_at = _NEXT_REQUEST_AT.get(host, 0.0)
            if now >= ready_at:
                _NEXT_REQUEST_AT[host] = now + delay
                return
            wait_for = ready_at - now
        time.sleep(wait_for)


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

    budget = float(timeout or config.REQUEST_TIMEOUT)
    headers = {"Accept": accept} if accept else None
    last_error = "error"
    limit = max_bytes or 500_000

    for attempt in range(config.MAX_RETRIES + 1):
        response = None
        try:
            if not _host_answers(url, 4.0):
                return FetchResult(url=url, error="timeout")
            deadline = time.monotonic() + max(budget, 1.0)
            _wait_for_host(url)
            response = _session().get(
                url,
                timeout=(3, min(5.0, budget)),
                allow_redirects=True,
                headers=headers,
                stream=True,
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

            raw = _read_body(response, limit, deadline)
            if not raw and time.monotonic() >= deadline:
                return FetchResult(url=url, final_url=response.url, error="timeout")
            text = raw.decode(response.encoding or "utf-8", errors="ignore")

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
        finally:
            if response is not None:
                response.close()

        if attempt >= config.MAX_RETRIES:
            break

    return FetchResult(url=url, error=last_error)


def _socket_of(response):
    try:
        sock = response.raw._connection.sock
        if sock is not None:
            return sock
    except Exception:
        pass
    try:
        return response.raw._fp.fp.raw._sock
    except Exception:
        return None


def _read_body(response, limit: int, deadline: float) -> bytes:
    """Read the public page, and stop when the time for this download is up.

    One read stops as soon as some bytes arrive. A site that trickles data
    cannot hold the run until the whole page finishes.
    """
    sock = _socket_of(response)
    if sock is not None:
        try:
            sock.settimeout(1.0)
        except Exception:
            pass
    try:
        response.raw._init_decoder()
    except Exception:
        pass
    decoder = getattr(response.raw, "_decoder", None)
    chunks: list[bytes] = []
    size = 0
    complete = False
    while size < limit and time.monotonic() < deadline:
        try:
            piece = response.raw._fp.read1(65536)
        except Exception:
            if time.monotonic() >= deadline:
                break
            time.sleep(0.05)
            continue
        if not piece:
            complete = True
            break
        if decoder is not None:
            try:
                piece = decoder.decompress(piece)
            except Exception:
                piece = b""
        if not piece:
            continue
        chunks.append(piece)
        size += len(piece)
    if decoder is not None and (complete or size >= limit):
        try:
            tail = decoder.flush()
        except Exception:
            tail = b""
        if tail:
            chunks.append(tail)
    if not complete and size < limit:
        return b""
    return b"".join(chunks)


def _host_answers(url: str, seconds: float) -> bool:
    """Stop a name lookup that never returns from holding the whole run."""
    host = urlparse(url).hostname
    if not host:
        return False
    with _DNS_LOCK:
        cached = _DNS_OK.get(host)
        if cached and time.monotonic() - cached < 600:
            return True
    done = Event()
    ok = {"value": False}

    def lookup() -> None:
        try:
            socket.getaddrinfo(host, 443, type=socket.SOCK_STREAM)
            ok["value"] = True
        except Exception:
            ok["value"] = False
        done.set()

    Thread(target=lookup, daemon=True).start()
    if not done.wait(max(seconds, 1.0)):
        return False
    if ok["value"]:
        with _DNS_LOCK:
            _DNS_OK[host] = time.monotonic()
    return ok["value"]


def public_origin(url: str) -> str:
    """Normalized domain for the URL that finally loaded."""
    return normalize_domain(url)
