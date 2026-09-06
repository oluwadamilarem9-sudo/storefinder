"""
Discovery engine.

Other modules call discover_candidates() and receive a simple list.
They do not need to know which public source found each domain.

A cycle is not complete just because a source returned hostnames.
Keep looking until unused websites are available or every source
has been tried.
"""

from __future__ import annotations

from collections.abc import Callable

from config import MAX_CANDIDATES_PER_SOURCE
from discovery.discovery_sources import (
    DiscoveredCandidate,
    active_sources,
    fallback_sources,
)
from utils.normalization import normalize_domain

LogFn = Callable[[str], None]


def discover_candidates(
    limit_per_source: int | None = None,
    on_log: LogFn | None = None,
    exclude_domains: set[str] | None = None,
    min_unused: int = 1,
) -> list[DiscoveredCandidate]:
    """
    Ask public sources for candidate domains until unused websites
    are found or every available source has been tried.
    """
    cap = limit_per_source or MAX_CANDIDATES_PER_SOURCE
    exclude = {normalize_domain(item) for item in (exclude_domains or set()) if item}
    discovered: list[DiscoveredCandidate] = []

    def log(message: str) -> None:
        print(message)
        if on_log:
            on_log(message)

    log("Searching public sources for Shopify hostnames...")
    discovered.extend(_run_sources(active_sources(), cap, log, exclude))

    unused = _unused_count(discovered, exclude)
    if unused < min_unused:
        log("Primary sources had no unused websites. Trying public fallback indexes...")
        discovered.extend(_run_sources(fallback_sources(), cap, log, exclude))
        unused = _unused_count(discovered, exclude)

    if unused < min_unused:
        log("Every public source was empty, blocked, or already checked.")
    else:
        log(f"Discovery found {len(discovered)} candidate hostname(s), {unused} unused.")
    return discovered


def _unused_count(candidates: list[DiscoveredCandidate], exclude: set[str]) -> int:
    seen: set[str] = set()
    unused = 0
    for item in candidates:
        domain = normalize_domain(item.domain)
        if not domain or domain in exclude or domain in seen:
            continue
        seen.add(domain)
        unused += 1
    return unused


def _run_sources(sources, cap: int, log: LogFn, exclude: set[str]) -> list[DiscoveredCandidate]:
    found: list[DiscoveredCandidate] = []
    for source in sources:
        log(f"  Source: {source.name} ({source.tier})")
        try:
            batch = source.discover(limit=max(cap, 100))
        except Exception as exc:
            log(f"  Skipping {source.name} ({exc}).")
            continue
        log(f"  Found {len(batch)} candidate(s) from {source.name}.")
        found.extend(batch)
        unused = _unused_count(found, exclude)
        if unused >= cap:
            log(f"  Have {unused} unused candidate(s), enough to start checking websites.")
            break
    return found
