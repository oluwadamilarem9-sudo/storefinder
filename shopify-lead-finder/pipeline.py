"""
Shared discovery cycle used by the terminal app and the Streamlit app.

This file does not reimplement discovery, detection, freshness, or
contact finding. It only runs those existing modules in order.
"""

from __future__ import annotations

from collections.abc import Callable

import config
from contact.public_contact_finder import find_public_contacts, maybe_rdap_created
from database.database import (
    connect,
    export_csv,
    export_rejected_csv,
    latest_leads,
    mark_processed,
    upsert_lead,
    upsert_rejected,
)
from detection.shopify_detector import detect_shopify
from discovery.candidate_manager import prepare_candidates
from discovery.domain_discovery import discover_candidates
from discovery.freshness import assess_freshness, lookup_earliest_certificate, meets_minimum
from utils.http import fetch_public
from utils.normalization import is_junk_store_domain, is_usable_shop_domain, normalize_domain, now_iso, website_url

LogFn = Callable[[str], None]


def run_cycle(
    *,
    on_log: LogFn | None = None,
    max_domains: int | None = None,
    min_freshness: str | None = None,
    enable_secondary: bool | None = None,
) -> dict:
    """
    Run one automatic discovery cycle.

    Optional overrides apply only for this run and are restored after.
    """
    previous = (
        config.MAX_DOMAINS_PER_CYCLE,
        config.MIN_FRESHNESS_LEVEL,
        config.ENABLE_SECONDARY_SOURCES,
    )
    if max_domains is not None:
        config.MAX_DOMAINS_PER_CYCLE = max_domains
    if min_freshness is not None:
        config.MIN_FRESHNESS_LEVEL = min_freshness
    if enable_secondary is not None:
        config.ENABLE_SECONDARY_SOURCES = enable_secondary

    def log(message: str) -> None:
        print(message)
        if on_log:
            on_log(message)

    stats = {
        "candidates": 0,
        "shopify": 0,
        "high": 0,
        "medium": 0,
        "low": 0,
        "emails": 0,
        "duplicates": 0,
    }

    connection = connect()
    try:
        log("========================================")
        log("SHOPIFY PUBLIC LEAD FINDER")
        log("========================================")
        log("Discovery engine v4")
        log("Discovery cycle started...")

        raw_candidates = discover_candidates(
            limit_per_source=config.MAX_DOMAINS_PER_CYCLE,
            on_log=log,
        )
        stats["candidates"] = len(raw_candidates)
        if not raw_candidates:
            log("No candidates came back from any public source this cycle.")
        ready, duplicates, junk = prepare_candidates(raw_candidates, connection)
        stats["duplicates"] = duplicates + junk
        to_check = ready[: config.MAX_DOMAINS_PER_CYCLE]

        log(f"Unique websites to check this cycle: {len(to_check)}")

        for index, candidate in enumerate(to_check, start=1):
            try:
                _process_candidate(connection, candidate, index, len(to_check), stats, log)
            except Exception as exc:
                log(f"  Error on {candidate.domain}: {exc}")
                mark_processed(connection, candidate.domain, "UNKNOWN", candidate.source)

        export_csv(connection)
        export_rejected_csv(connection)
        _log_summary(connection, stats, log)
        return stats
    finally:
        connection.close()
        (
            config.MAX_DOMAINS_PER_CYCLE,
            config.MIN_FRESHNESS_LEVEL,
            config.ENABLE_SECONDARY_SOURCES,
        ) = previous


def _process_candidate(connection, candidate, index: int, total: int, stats: dict, log: LogFn) -> None:
    domain = candidate.domain
    log(f"[{index}/{total}] Checking {domain}")

    homepage = fetch_public(
        website_url(domain),
        allow_cross_domain_redirect=True,
    )

    if not homepage.ok:
        log(f"  Status: skipped ({homepage.error})")
        mark_processed(connection, domain, "UNKNOWN", candidate.source)
        return

    final_domain = normalize_domain(homepage.final_url or domain)
    if not is_usable_shop_domain(final_domain) or is_junk_store_domain(final_domain):
        log("  Status: rejected (demo, test, or platform host)")
        upsert_rejected(
            connection,
            {
                "domain": final_domain or domain,
                "shopify_status": "UNKNOWN",
                "discovery_source": candidate.source,
                "source_url": candidate.source_url,
                "freshness_level": "LOW",
                "freshness_evidence": "Filtered as a demo, test, example, or platform host.",
                "reject_reason": "filtered_demo_or_test_store",
            },
        )
        return

    shopify_status = detect_shopify(homepage.text, homepage.headers)
    log(f"  Shopify: {shopify_status}")

    if shopify_status != "SHOPIFY":
        mark_processed(connection, final_domain, shopify_status, candidate.source)
        if final_domain != domain:
            mark_processed(connection, domain, shopify_status, candidate.source)
        log("  Status: discarded")
        return

    stats["shopify"] += 1
    contacts = find_public_contacts(final_domain, homepage.text)
    earliest_cert = ""
    rdap_created = ""
    if not config.FAST_MODE:
        earliest_cert = lookup_earliest_certificate(final_domain)
        rdap_created = maybe_rdap_created(final_domain)

    freshness = assess_freshness(
        {
            "source": candidate.source,
            "source_kind": candidate.extra.get("source_kind", ""),
            "discovered_at": candidate.discovered_at,
            "cert_not_before": candidate.extra.get("cert_not_before", ""),
            "earliest_cert": earliest_cert,
            "rdap_created": rdap_created,
            "homepage_html": homepage.text,
            "store_name": contacts.get("store_name", ""),
        }
    )
    log(f"  Freshness: {freshness.freshness_level} ({freshness.freshness_score})")

    if freshness.freshness_level == "HIGH":
        stats["high"] += 1
    elif freshness.freshness_level == "MEDIUM":
        stats["medium"] += 1
    else:
        stats["low"] += 1

    record = {
        **contacts,
        "domain": final_domain,
        "shopify_status": "SHOPIFY",
        "discovery_source": candidate.source,
        "source_url": candidate.source_url,
        "discovered_at": candidate.discovered_at or now_iso(),
        "freshness_score": freshness.freshness_score,
        "freshness_level": freshness.freshness_level,
        "freshness_evidence": f"{candidate.evidence} {freshness.freshness_evidence}".strip(),
    }

    if meets_minimum(freshness.freshness_level, config.MIN_FRESHNESS_LEVEL):
        if record.get("public_email"):
            stats["emails"] += 1
            log(f"  Email: {record['public_email']}")
        else:
            log("  Email: none publicly displayed")
        upsert_lead(connection, record)
        log("  Status: saved to leads.csv")
    else:
        record["reject_reason"] = "below_freshness_threshold"
        upsert_rejected(connection, record)
        log("  Status: rejected (freshness below threshold)")

    if final_domain != domain:
        mark_processed(connection, domain, "SHOPIFY", candidate.source)


def _log_summary(connection, stats: dict, log: LogFn) -> None:
    log("========================================")
    log(f"Candidates discovered: {stats['candidates']}")
    log(f"Shopify stores: {stats['shopify']}")
    log(f"High freshness: {stats['high']}")
    log(f"Medium freshness: {stats['medium']}")
    log(f"Low freshness: {stats['low']}")
    log(f"Public business emails: {stats['emails']}")
    log(f"Duplicates: {stats['duplicates']}")

    leads = latest_leads(connection, limit=5)
    if not leads:
        log("No HIGH/MEDIUM freshness leads saved yet.")
    else:
        log("Latest qualifying leads:")
        for index, lead in enumerate(leads, start=1):
            name = lead.get("store_name") or lead.get("domain")
            email = lead.get("public_email") or "no public email"
            log(f"{index}. {name} | https://{lead.get('domain', '')} | {email}")

    log(f"Qualifying leads: {config.OUTPUT_FILE}")
    log(f"Rejected candidates: {config.REJECTED_FILE}")
    log("========================================")
