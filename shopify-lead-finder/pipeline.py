"""
Shared discovery cycle used by the terminal app and the Streamlit app.

This file does not reimplement discovery, detection, freshness, or
contact finding. It only runs those existing modules in order.
"""

from __future__ import annotations

from collections.abc import Callable

import config
from contact.public_contact_finder import find_public_contacts
from database.database import (
    connect,
    export_csv,
    export_rejected_csv,
    known_domains,
    mark_processed,
    upsert_lead,
    upsert_rejected,
)
from detection.shopify_detector import detect_shopify
from discovery.candidate_manager import prepare_candidates
from discovery.domain_discovery import discover_candidates
from discovery.freshness import meets_minimum, prove_new_store
from utils.countries import country_allows, country_label, normalize_country
from utils.http import fetch_public
from utils.normalization import is_junk_store_domain, is_usable_shop_domain, normalize_domain, now_iso, website_url

LogFn = Callable[[str], None]


def run_cycle(
    *,
    on_log: LogFn | None = None,
    max_domains: int | None = None,
    min_freshness: str | None = None,
    enable_secondary: bool | None = None,
    target_countries: list[str] | None = None,
    keep_unknown_country: bool | None = None,
    db_path=None,
    leads_csv=None,
    rejected_csv=None,
) -> dict:
    """
    Run one automatic discovery cycle.

    Optional overrides apply only for this run and are restored after.
    """
    previous = (
        config.MAX_DOMAINS_PER_CYCLE,
        config.MIN_FRESHNESS_LEVEL,
        config.ENABLE_SECONDARY_SOURCES,
        list(config.TARGET_COUNTRIES),
        config.KEEP_UNKNOWN_COUNTRY,
    )
    if max_domains is not None:
        config.MAX_DOMAINS_PER_CYCLE = max_domains
    if min_freshness is not None:
        config.MIN_FRESHNESS_LEVEL = min_freshness
    if enable_secondary is not None:
        config.ENABLE_SECONDARY_SOURCES = enable_secondary
    if target_countries is not None:
        config.TARGET_COUNTRIES = [item.upper() for item in target_countries if item]
    if keep_unknown_country is not None:
        config.KEEP_UNKNOWN_COUNTRY = keep_unknown_country

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
        "checked": 0,
        "complete": False,
        "skipped_seen": 0,
        "this_run_lead_domains": [],
        "this_run_rejected_domains": [],
    }

    connection = connect(db_path)
    try:
        log("========================================")
        log("SHOPIFY PUBLIC LEAD FINDER")
        log("========================================")
        log("Discovery engine v11")
        log(
            "A lead is a new store only: public proof it appeared within "
            f"{config.NEW_STORE_DAYS} days, plus a public email and a published country."
        )
        log("Found today is not launched today.")
        log("This run checks every website you asked for. Saving a lead does not stop it early.")
        if db_path is not None:
            log("This run uses only the signed-in project's history.")
        log("Stores from earlier runs in this project are excluded and will not be checked again.")
        if config.TARGET_COUNTRIES:
            labels = ", ".join(country_label(code) for code in config.TARGET_COUNTRIES)
            log(f"Country filter: {labels}.")
            log(
                "Unknown published country: "
                + ("keep" if config.KEEP_UNKNOWN_COUNTRY else "reject")
                + ". Country comes from public store pages only."
            )
        else:
            log("Country filter: any country the store publishes.")
        log("Stores with no public email are rejected.")

        seen_this_run: set[str] = set(known_domains(connection))
        stats["skipped_seen"] = len(seen_this_run)
        if seen_this_run:
            log(
                f"Skipping {len(seen_this_run)} store(s) already used in earlier runs."
            )
        max_domains = config.MAX_DOMAINS_PER_CYCLE
        stats["requested"] = max_domains
        log(f"Websites to check this run: {max_domains}.")

        while stats["checked"] < max_domains:
            remaining = max_domains - stats["checked"]
            raw_candidates = discover_candidates(
                limit_per_source=remaining,
                on_log=log,
                exclude_domains=seen_this_run,
                min_unused=remaining,
            )
            stats["candidates"] += len(raw_candidates)
            if not raw_candidates:
                log("No candidates came back from any public source this pass.")

            ready, duplicates, junk = prepare_candidates(raw_candidates, connection)
            stats["duplicates"] += duplicates + junk
            to_check = [
                item
                for item in ready
                if item.domain not in seen_this_run
            ][:remaining]

            log(f"Unused websites to check this pass: {len(to_check)}")
            if not to_check:
                log(
                    f"Public sources ran out after {stats['checked']} of {max_domains} websites."
                )
                break

            for candidate in to_check:
                seen_this_run.add(candidate.domain)
                stats["checked"] += 1
                index = stats["checked"]
                try:
                    _process_candidate(connection, candidate, index, max_domains, stats, log)
                except Exception as exc:
                    log(f"  Error on {candidate.domain}: {exc}")
                    mark_processed(connection, candidate.domain, "UNKNOWN", candidate.source)
                if stats["checked"] >= max_domains:
                    break

        stats["count_complete"] = stats["checked"] >= max_domains
        stats["complete"] = len(stats["this_run_lead_domains"]) > 0
        export_csv(connection, leads_csv)
        export_rejected_csv(connection, rejected_csv)
        _log_summary(connection, stats, log)
        return stats
    finally:
        connection.close()
        (
            config.MAX_DOMAINS_PER_CYCLE,
            config.MIN_FRESHNESS_LEVEL,
            config.ENABLE_SECONDARY_SOURCES,
            config.TARGET_COUNTRIES,
            config.KEEP_UNKNOWN_COUNTRY,
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
        stats["this_run_rejected_domains"].append(final_domain or domain)
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
    published_country = contacts.get("country") or ""
    country_code = normalize_country(published_country)
    log(
        f"  Country: {published_country or 'not published'}"
        + (f" ({country_code})" if country_code else "")
    )

    record = {
        **contacts,
        "domain": final_domain,
        "shopify_status": "SHOPIFY",
        "discovery_source": candidate.source,
        "source_url": candidate.source_url,
        "discovered_at": candidate.discovered_at or now_iso(),
        "freshness_score": 0,
        "freshness_level": "UNKNOWN",
        "freshness_evidence": "",
    }

    allowed, country_reason = country_allows(
        published_country,
        config.TARGET_COUNTRIES,
        config.KEEP_UNKNOWN_COUNTRY,
    )
    if not allowed:
        record["reject_reason"] = country_reason
        record["freshness_evidence"] = (
            "Public country "
            f"{published_country or 'was not published'} "
            "and did not match the selected countries."
        )
        upsert_rejected(connection, record)
        stats["this_run_rejected_domains"].append(final_domain)
        if country_reason == "country_unknown":
            log("  Status: rejected (no published country)")
        else:
            log("  Status: rejected (country outside selected list)")
        if final_domain != domain:
            mark_processed(connection, domain, "SHOPIFY", candidate.source)
        return

    if not (record.get("public_email") or "").strip():
        record["reject_reason"] = "no_public_email"
        record["freshness_evidence"] = "No public business email was displayed."
        upsert_rejected(connection, record)
        stats["this_run_rejected_domains"].append(final_domain)
        log("  Status: rejected (no public business email)")
        if final_domain != domain:
            mark_processed(connection, domain, "SHOPIFY", candidate.source)
        return

    newness = prove_new_store(final_domain, homepage.text)
    record["freshness_score"] = newness.freshness_score
    record["freshness_level"] = newness.freshness_level
    record["freshness_evidence"] = newness.evidence
    log(f"  New store check: {newness.freshness_level} ({newness.evidence})")

    if not newness.is_new:
        record["reject_reason"] = "not_a_new_store"
        upsert_rejected(connection, record)
        stats["this_run_rejected_domains"].append(final_domain)
        log("  Status: rejected (not a new store)")
        if final_domain != domain:
            mark_processed(connection, domain, "SHOPIFY", candidate.source)
        return

    if not meets_minimum(newness.freshness_level, config.MIN_FRESHNESS_LEVEL):
        record["reject_reason"] = "below_freshness_threshold"
        upsert_rejected(connection, record)
        stats["this_run_rejected_domains"].append(final_domain)
        log("  Status: rejected (older than the selected new-store window)")
        if final_domain != domain:
            mark_processed(connection, domain, "SHOPIFY", candidate.source)
        return

    if newness.freshness_level == "HIGH":
        stats["high"] += 1
    else:
        stats["medium"] += 1
    stats["emails"] += 1
    log(f"  Email: {record['public_email']}")
    upsert_lead(connection, record)
    stats["this_run_lead_domains"].append(final_domain)
    log("  Status: saved to leads.csv")

    if final_domain != domain:
        mark_processed(connection, domain, "SHOPIFY", candidate.source)


def _qualifying_count(stats: dict) -> int:
    return len(stats.get("this_run_lead_domains") or [])


def _log_summary(connection, stats: dict, log: LogFn) -> None:
    saved = _qualifying_count(stats)
    log("========================================")
    log(f"Candidates discovered: {stats['candidates']}")
    log(f"Websites checked this run: {stats['checked']} of {stats.get('requested') or stats['checked']}")
    if stats.get("count_complete"):
        log(f"Checked all {stats.get('requested') or stats['checked']} websites requested for this run.")
    else:
        log(
            "Public sources ran out before the requested number of websites was checked."
        )
    log(f"Shopify stores: {stats['shopify']}")
    log(f"High freshness: {stats['high']}")
    log(f"Medium freshness: {stats['medium']}")
    log(f"Low freshness: {stats['low']}")
    log(f"Public business emails: {stats['emails']}")
    log(f"Duplicates / already seen: {stats['duplicates']}")
    log(f"Excluded from earlier runs: {stats['skipped_seen']}")

    if saved > 0:
        log(f"Discovery found {saved} new store(s) with a public email and a published country.")
        this_run = stats.get("this_run_lead_domains") or []
        for index, domain in enumerate(this_run, start=1):
            log(f"{index}. https://{domain}")
    else:
        log("Discovery did not complete: no new store had a public email, a matching country, and public proof it appeared recently.")

    log(f"Qualifying leads: {config.OUTPUT_FILE}")
    log(f"Rejected candidates: {config.REJECTED_FILE}")
    log("========================================")
