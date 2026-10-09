"""
Shared discovery cycle used by the terminal app and the Streamlit app.

This file does not reimplement discovery, detection, freshness, or
contact finding. It only runs those existing modules in order.
"""

from __future__ import annotations

import time
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
    watched_password_domains,
    clear_password_watch,
)
from detection.shopify_detector import detect_shopify
from detection.storefront_profile import inspect_storefront, is_password_page, published_custom_domain
from discovery.candidate_manager import prepare_candidates
from discovery.discovery_sources import DiscoveredCandidate
from discovery.domain_discovery import discover_candidates
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
        log("Discovery engine v16")
        log("This run checks each shop on its own domain. A myshopify.com name is not checked and is not saved.")
        log("A password page is checked again on a later run. It is not published until the shop opens.")
        log("This run checks every website you asked for. Saving a lead does not stop it early.")
        if db_path is not None:
            log("This run uses only the signed-in project's history.")
        log("Finished shops from earlier runs are excluded and will not be checked again.")
        log("Shops still behind a password page are watched and checked again.")
        if config.TARGET_COUNTRIES:
            labels = ", ".join(country_label(code) for code in config.TARGET_COUNTRIES)
            log(f"Country filter: {labels}.")
            log(
                "Unknown published country: "
                + ("keep" if config.KEEP_UNKNOWN_COUNTRY else "reject")
                + ". Country comes from public store pages only."
            )
        else:
            log("Country filter: any published country. Shops that do not publish a country are still saved.")
        log("A public email is stored when the shop displays one.")

        all_watching = watched_password_domains(connection)
        watching = [
            domain
            for domain in all_watching
            if not str(domain).endswith(".myshopify.com") and not str(domain).endswith(".shopify.com")
        ]
        myshopify_watches = len(all_watching) - len(watching)
        seen_this_run: set[str] = set(known_domains(connection)) - set(watching)
        stats["skipped_seen"] = len(seen_this_run)
        if seen_this_run:
            log(
                f"Skipping {len(seen_this_run)} store(s) already used in earlier runs."
            )
        max_domains = config.MAX_DOMAINS_PER_CYCLE
        stats["requested"] = max_domains
        log(f"Websites to check this run: {max_domains}.")
        if myshopify_watches:
            log(
                f"Leaving {myshopify_watches} myshopify.com name(s) off this run. "
                "Only a shop's own domain is checked."
            )
        rate_streak = 0

        def check_one(candidate) -> bool:
            nonlocal rate_streak
            if stats["checked"] >= max_domains or stats.get("slowed_down"):
                return False
            seen_this_run.add(candidate.domain)
            stats["checked"] += 1
            try:
                counted = _process_candidate(connection, candidate, stats["checked"], max_domains, stats, log)
            except Exception as exc:
                log(f"  Error on {candidate.domain}: {exc}")
                mark_processed(connection, candidate.domain, "UNKNOWN", candidate.source)
                counted = True
            if counted:
                rate_streak = 0
                return stats["checked"] < max_domains
            stats["checked"] -= 1
            rate_streak += 1
            if rate_streak >= 8:
                stats["slowed_down"] = True
                log("Storefronts asked us to slow down. Remaining shops stay for a later run.")
                return False
            return True

        if watching:
            log(f"Rechecking {len(watching)} shop(s) still behind a password page.")
            for domain in watching:
                candidate = DiscoveredCandidate(
                    domain=domain,
                    source="password_watch",
                    source_url="",
                    discovered_at=now_iso(),
                    evidence="Checked again after an earlier password page.",
                )
                if not check_one(candidate):
                    break

        while stats["checked"] < max_domains and not stats.get("slowed_down"):
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
                if not check_one(candidate):
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


def _fetch_storefront(domain: str):
    homepage = fetch_public(website_url(domain), allow_cross_domain_redirect=True)
    if homepage.error == "429":
        time.sleep(8)
        homepage = fetch_public(website_url(domain), allow_cross_domain_redirect=True)
    return homepage


def _process_candidate(connection, candidate, index: int, total: int, stats: dict, log: LogFn) -> bool:
    domain = candidate.domain
    log(f"[{index}/{total}] Checking {domain}")

    homepage = _fetch_storefront(domain)

    if not homepage.ok:
        if homepage.error == "429":
            log("  Status: slow down (429). This shop was not checked and will be tried later.")
            return False
        log(f"  Status: skipped ({homepage.error})")
        mark_processed(connection, domain, "UNKNOWN", candidate.source)
        return True

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
        return True

    shopify_status = detect_shopify(homepage.text, homepage.headers)
    log(f"  Shopify: {shopify_status}")

    if shopify_status != "SHOPIFY":
        mark_processed(connection, final_domain, shopify_status, candidate.source)
        if final_domain != domain:
            mark_processed(connection, domain, shopify_status, candidate.source)
        log("  Status: discarded")
        return True

    custom_domain = published_custom_domain(final_domain, homepage.text)
    if custom_domain and custom_domain != final_domain:
        log(f"  Custom domain: {custom_domain}")
        custom_home = fetch_public(
            website_url(custom_domain),
            allow_cross_domain_redirect=True,
        )
        if custom_home.ok:
            homepage = custom_home
            final_domain = normalize_domain(custom_home.final_url or custom_domain)
        else:
            final_domain = custom_domain
    if str(final_domain).endswith(".myshopify.com") and not is_password_page(homepage.text):
        _reject(
            connection,
            stats,
            domain,
            final_domain,
            candidate,
            contacts={},
            reason="no_custom_domain",
            evidence="The shop has not published a domain of its own. A myshopify.com name is not saved.",
            log=log,
            status="rejected (no custom domain)",
        )
        return True

    stats["shopify"] += 1
    if is_password_page(homepage.text):
        _reject(
            connection,
            stats,
            domain,
            final_domain,
            candidate,
            contacts={},
            reason="password_page",
            evidence="The public page is a password wall or an opening-soon page.",
            log=log,
            status="watching (password page, checked again next run)",
        )
        return True

    profile = inspect_storefront(final_domain, homepage.text)
    log(f"  Catalog: {profile.evidence()}")
    if profile.password_page:
        _reject(
            connection,
            stats,
            domain,
            final_domain,
            candidate,
            contacts={},
            reason="password_page",
            evidence=profile.evidence(),
            log=log,
            status="watching (password page, checked again next run)",
        )
        return True
    if not profile.product_names:
        stats["low"] += 1
        _reject(
            connection,
            stats,
            domain,
            final_domain,
            candidate,
            contacts={},
            reason="no_public_products",
            evidence=profile.evidence(),
            log=log,
            status="rejected (no public products)",
        )
        return True

    contacts = find_public_contacts(final_domain, homepage.text)
    published_country = contacts.get("country") or ""
    country_code = normalize_country(published_country)
    log(
        f"  Country: {published_country or 'not published'}"
        + (f" ({country_code})" if country_code else "")
    )
    if config.TARGET_COUNTRIES:
        allowed, country_reason = country_allows(
            published_country,
            config.TARGET_COUNTRIES,
            False,
        )
        if not allowed:
            if country_reason == "country_unknown":
                status = "rejected (no published country)"
            else:
                status = "rejected (country outside selected list)"
            _reject(
                connection,
                stats,
                domain,
                final_domain,
                candidate,
                contacts=contacts,
                reason=country_reason,
                evidence=(
                    "Public country "
                    f"{published_country or 'was not published'} "
                    "and did not match the selected countries."
                ),
                log=log,
                status=status,
            )
            return True

    record = {
        **contacts,
        "domain": final_domain,
        "shopify_status": "SHOPIFY",
        "discovery_source": candidate.source,
        "source_url": candidate.source_url,
        "discovered_at": candidate.discovered_at or now_iso(),
        "freshness_score": 80,
        "freshness_level": "HIGH",
        "freshness_evidence": profile.evidence(),
    }
    stats["high"] += 1
    if (record.get("public_email") or "").strip():
        stats["emails"] += 1
        log(f"  Email: {record['public_email']}")
    else:
        log("  Email: not published on the store")
    upsert_lead(connection, record)
    clear_password_watch(connection, final_domain)
    if domain != final_domain:
        clear_password_watch(connection, domain)
    stats["this_run_lead_domains"].append(final_domain)
    log("  Status: saved for download")

    if final_domain != domain:
        mark_processed(connection, domain, "SHOPIFY", candidate.source)
    return True


def _reject(connection, stats, domain, final_domain, candidate, contacts, reason, evidence, log, status) -> None:
    record = {
        **contacts,
        "domain": final_domain,
        "shopify_status": "SHOPIFY",
        "discovery_source": candidate.source,
        "source_url": candidate.source_url,
        "discovered_at": candidate.discovered_at or now_iso(),
        "freshness_score": 0,
        "freshness_level": "LOW",
        "freshness_evidence": evidence,
        "reject_reason": reason,
    }
    upsert_rejected(connection, record)
    stats["this_run_rejected_domains"].append(final_domain)
    log(f"  Status: {status}")
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
    elif stats.get("slowed_down"):
        log("Storefronts asked us to slow down before the requested number of websites was checked.")
    else:
        log(
            "Public sources ran out before the requested number of websites was checked."
        )
    log(f"Shopify stores: {stats['shopify']}")
    log(f"Operating shops saved: {stats['high']}")
    log(f"Shops with no public products: {stats['low']}")
    log(f"Public business emails: {stats['emails']}")
    log(f"Duplicates / already seen: {stats['duplicates']}")
    log(f"Excluded from earlier runs: {stats['skipped_seen']}")

    if saved > 0:
        log(f"Scraped {saved} live shop(s). Download them from Newly scraped stores.")
        this_run = stats.get("this_run_lead_domains") or []
        for index, domain in enumerate(this_run, start=1):
            log(f"{index}. https://{domain}")
    else:
        log("Discovery did not complete: no live shop with a public product catalog was saved.")

    log(f"Qualifying leads: {config.OUTPUT_FILE}")
    log(f"Rejected candidates: {config.REJECTED_FILE}")
    log("========================================")
