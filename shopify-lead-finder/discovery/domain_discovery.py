"""
Discovery engine.

Other modules call discover_candidates() and receive a simple list.
They do not need to know which public source found each domain.

If every primary source is empty, public fallback indexes are tried
so a cycle does not finish without looking at any websites.
"""

from __future__ import annotations

from collections.abc import Callable

from config import MAX_CANDIDATES_PER_SOURCE
from discovery.discovery_sources import (
    DiscoveredCandidate,
    active_sources,
    fallback_sources,
)

LogFn = Callable[[str], None]


def discover_candidates(
    limit_per_source: int | None = None,
    on_log: LogFn | None = None,
) -> list[DiscoveredCandidate]:
    """
    Ask public sources for candidate domains until at least one is found
    or every available source has been tried.
    """
    cap = limit_per_source or MAX_CANDIDATES_PER_SOURCE
    discovered: list[DiscoveredCandidate] = []

    def log(message: str) -> None:
        print(message)
        if on_log:
            on_log(message)

    log("Searching public sources for Shopify hostnames...")
    discovered.extend(_run_sources(active_sources(), cap, log))

    if not discovered:
        log("Primary sources returned nothing. Trying public fallback indexes...")
        discovered.extend(_run_sources(fallback_sources(), cap, log))

    if not discovered:
        log("Every public source was empty or blocked. There are no websites to check.")
    else:
        log(f"Discovery found {len(discovered)} candidate hostname(s).")
    return discovered


def _run_sources(sources, cap: int, log: LogFn) -> list[DiscoveredCandidate]:
    found: list[DiscoveredCandidate] = []
    for source in sources:
        log(f"  Source: {source.name} ({source.tier})")
        try:
            batch = source.discover(limit=cap)
        except Exception as exc:
            log(f"  Skipping {source.name} ({exc}).")
            continue
        log(f"  Found {len(batch)} candidate(s) from {source.name}.")
        found.extend(batch)
        if len(found) >= cap:
            log(f"  Have {len(found)} candidates, enough to start checking websites.")
            break
    return found
