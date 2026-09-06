"""
Respect robots.txt where it can be read.

If robots.txt cannot be downloaded, we still use delays and skip blocks.
We never try to evade a robots rule.
"""

from __future__ import annotations

from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser
from urllib.request import Request, urlopen

from config import REQUEST_TIMEOUT, USER_AGENT
from utils.normalization import website_url

_CACHE: dict[str, RobotFileParser | None] = {}


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
    if origin in _CACHE:
        return _CACHE[origin]

    robots_url = origin + "/robots.txt"
    parser = RobotFileParser()
    parser.set_url(robots_url)
    try:
        request = Request(robots_url, headers={"User-Agent": USER_AGENT})
        with urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            raw = response.read(200_000).decode("utf-8", errors="ignore")
        parser.parse(raw.splitlines())
        _CACHE[origin] = parser
        return parser
    except Exception:
        _CACHE[origin] = None
        return None
