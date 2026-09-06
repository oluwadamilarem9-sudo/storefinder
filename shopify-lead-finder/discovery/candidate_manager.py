"""
Prepare discovered domains before they are visited.

This module:
- normalizes domains
- removes duplicates
- drops obvious demo / test / example shops
- skips domains the database already checked
"""

from __future__ import annotations

from config import RECHECK_AFTER_DAYS
from database.database import already_processed, is_new_domain
from discovery.discovery_sources import DiscoveredCandidate
from utils.normalization import is_junk_store_domain, is_usable_shop_domain, normalize_domain


def prepare_candidates(
    candidates: list[DiscoveredCandidate],
    connection,
) -> tuple[list[DiscoveredCandidate], int, int]:
    """
    Deduplicate and drop domains already used in an earlier cycle, or junk.

    Returns (ready_candidates, duplicates_skipped, junk_skipped).
    """
    unique: dict[str, DiscoveredCandidate] = {}
    duplicates = 0
    junk = 0

    for item in candidates:
        domain = normalize_domain(item.domain)
        if not is_usable_shop_domain(domain) or is_junk_store_domain(domain):
            junk += 1
            continue
        item.domain = domain
        if domain in unique:
            duplicates += 1
            _merge(unique[domain], item)
            continue
        unique[domain] = item

    ready: list[DiscoveredCandidate] = []
    for domain, item in unique.items():
        if already_processed(connection, domain, RECHECK_AFTER_DAYS):
            duplicates += 1
            continue
        item.extra["is_new_to_database"] = is_new_domain(connection, domain)
        ready.append(item)

    return ready, duplicates, junk


def _merge(existing: DiscoveredCandidate, incoming: DiscoveredCandidate) -> None:
    for key, value in incoming.extra.items():
        if value and not existing.extra.get(key):
            existing.extra[key] = value
    if incoming.source and incoming.source not in existing.source:
        existing.source = f"{existing.source},{incoming.source}"
    if incoming.evidence and incoming.evidence not in existing.evidence:
        existing.evidence = f"{existing.evidence} {incoming.evidence}".strip()
    if incoming.source_url and not existing.source_url:
        existing.source_url = incoming.source_url
