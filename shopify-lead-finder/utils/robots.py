"""
Respect robots.txt where it can be read.

If robots.txt cannot be downloaded, we still use delays and skip blocks.
We never try to evade a robots rule.
"""

from __future__ import annotations

from threading import Lock, Thread
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from urllib.request import Request, urlopen

from config import USER_AGENT
from utils.normalization import website_url

_CACHE: dict[str, RobotFileParser | None] = {}
_CACHE_LOCK = Lock()
_ROBOTS_TIMEOUT = 3


def robots_allowed(url: str, user_agent: str = USER_AGENT) -> bool:
    """Return True when robots.txt allows this public URL."""
    parser = _parser_for(url)
    if parser is None:
        return True
    try:
        # Check the path only. Query strings such as q=%.myshopify.com
        # can confuse the robots parser and falsely deny a public URL.
        parsed = urlparse(url)
        clean = f"{parsed.scheme}://{parsed.netloc}{parsed.path or '/'}"
        return parser.can_fetch(user_agent, clean)
    except Exception:
        return True


def _parser_for(url: str) -> RobotFileParser | None:
    parsed = urlparse(url)
    origin = website_url(parsed.netloc)
    if not origin:
        return None
    with _CACHE_LOCK:
        if origin in _CACHE:
            return _CACHE[origin]

    robots_url = origin + "/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    raw = _read_robots(robots_url)
    if raw is None:
        stored = None
    else:
        try:
            parser.parse(raw.splitlines())
            stored = parser
        except Exception:
            stored = None
    with _CACHE_LOCK:
        _CACHE[origin] = stored
    return stored


def _read_robots(robots_url: str) -> str | None:
    """Read robots.txt, and give up if the site does not answer."""
    box: dict[str, str] = {}

    def download() -> None:
        try:
            request = Request(robots_url, headers={"User-Agent": USER_AGENT})
            with urlopen(request, timeout=_ROBOTS_TIMEOUT) as response:
                box["raw"] = response.read(200_000).decode("utf-8", errors="ignore")
        except Exception:
            return

    thread = Thread(target=download, daemon=True)
    thread.start()
    thread.join(_ROBOTS_TIMEOUT + 1)
    return box.get("raw")
