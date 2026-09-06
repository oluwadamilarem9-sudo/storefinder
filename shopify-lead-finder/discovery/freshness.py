"""
Freshness scoring for discovered Shopify stores.

This module never invents a launch date.

- The day this tool first saw a domain is NOT a launch date.
- A Wayback or Common Crawl timestamp is NOT a launch date.
- A Hacker News post date is NOT a launch date.
- A TLS certificate date is when a hostname got a certificate,
  not proof the business launched that day.
- A public announcement date is recorded only when the source
  actually contains that announcement.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone

from config import (
    RDAP_HIGH_DAYS,
    RDAP_MEDIUM_DAYS,
    RECENT_CERT_DAYS,
    SOURCE_TIMEOUT,
)
from utils.http import fetch_public
from utils.normalization import parse_loose_date

LEVEL_RANK = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

LAUNCH_PHRASES = (
    "just launched",
    "newly launched",
    "officially launched",
    "we launched",
    "now open",
    "now live",
    "grand opening",
    "our new store",
    "new online store",
)

COPYRIGHT_YEAR = re.compile(r"(?:©|&copy;|copyright)\s*(?:20\d{2}\s*[-–—]\s*)?(20\d{2})", re.I)
ESTABLISHED_YEAR = re.compile(r"(?:since|est\.?|established)\s+(19\d{2}|20\d{2})", re.I)


@dataclass
class FreshnessResult:
    freshness_score: int
    freshness_level: str
    freshness_evidence: str


def meets_minimum(level: str, minimum: str) -> bool:
    return LEVEL_RANK.get(level, 0) >= LEVEL_RANK.get(minimum, 0)


def assess_freshness(signals: dict) -> FreshnessResult:
    """
    Score how much public evidence suggests a recent/new site.

    signals may include:
    source, source_kind, discovered_at, cert_not_before,
    earliest_cert, rdap_created, homepage_html, store_name,
    announcement_date, announcement_text
    """
    notes: list[str] = []
    score = 0
    level = "UNKNOWN"

    earliest_cert = parse_loose_date(str(signals.get("earliest_cert") or ""))
    cert_not_before = parse_loose_date(str(signals.get("cert_not_before") or ""))
    rdap_created = parse_loose_date(str(signals.get("rdap_created") or ""))
    announcement = parse_loose_date(str(signals.get("announcement_date") or ""))
    html = signals.get("homepage_html") or ""
    source_kind = (signals.get("source_kind") or "").lower()

    now = datetime.now(timezone.utc)

    if announcement:
        age = _age_days(now, announcement)
        notes.append(
            f"Public launch announcement dated {announcement.date().isoformat()} "
            f"({signals.get('announcement_text') or 'announcement text found'}). "
            "This date comes from the source, not from this tool."
        )
        if age <= RDAP_HIGH_DAYS:
            score += 70
            level = "HIGH"
        elif age <= RDAP_MEDIUM_DAYS:
            score += 50
            level = _raise(level, "MEDIUM")

    if rdap_created:
        age = _age_days(now, rdap_created)
        notes.append(
            f"Public RDAP domain registration date {rdap_created.date().isoformat()}. "
            "This is the domain registration date, not a Shopify launch date."
        )
        if age <= RDAP_HIGH_DAYS:
            score += 55
            level = _raise(level, "HIGH")
        elif age <= RDAP_MEDIUM_DAYS:
            score += 35
            level = _raise(level, "MEDIUM")
        else:
            notes.append("RDAP shows an older domain, so this is unlikely to be a newly created website.")
            score -= 25
            if level == "UNKNOWN":
                level = "LOW"

    first_cert = earliest_cert or cert_not_before
    if first_cert:
        age = _age_days(now, first_cert)
        if earliest_cert:
            notes.append(
                f"Earliest public TLS certificate for this hostname is {first_cert.date().isoformat()}. "
                "Certificate date is not a store launch date."
            )
        else:
            notes.append(
                f"A public TLS certificate not_before date of {first_cert.date().isoformat()} was seen. "
                "This may be a renewal. It is not a store launch date."
            )
        if earliest_cert and age <= RECENT_CERT_DAYS:
            score += 40
            level = _raise(level, "MEDIUM")
        elif earliest_cert and age <= RDAP_MEDIUM_DAYS:
            score += 15
            level = _raise(level, "MEDIUM") if age <= 90 else level
        elif cert_not_before and not earliest_cert and age <= RECENT_CERT_DAYS:
            score += 20
            level = _raise(level, "MEDIUM")
            notes.append("Only a recent certificate was seen; older certificates were not confirmed.")
        elif first_cert and age > RDAP_MEDIUM_DAYS:
            notes.append("Public certificate history is older than six months, so the hostname is not newly created.")
            score -= 20
            if level == "UNKNOWN":
                level = "LOW"

    launch_hit = _launch_language(html)
    if launch_hit:
        notes.append(
            f"Homepage publicly contains launch language: \"{launch_hit}\". "
            "No launch date is assumed from this wording alone."
        )
        score += 15
        if level in {"MEDIUM", "HIGH"}:
            score += 10
        elif level == "UNKNOWN":
            level = "MEDIUM"

    old_year = _established_year(html)
    current_year = now.year
    if old_year and old_year <= current_year - 2:
        notes.append(
            f"Public page mentions {old_year}, which suggests an established site rather than a new launch."
        )
        score -= 30
        level = "LOW" if level != "HIGH" else "MEDIUM"

    if source_kind in {"public_discussion", "web_archive", "web_index"}:
        notes.append(
            "This candidate came from a secondary mention source "
            "(discussion, archive, or web index). That is not evidence of a new launch."
        )
        if level == "UNKNOWN":
            level = "LOW"
        score = min(score, 25)

    if not notes:
        notes.append(
            "First discovered by this tool. No public launch date, first-certificate date, "
            "or domain-registration date was available. This is not a store launch date."
        )
        level = "UNKNOWN"
        score = 0

    score = max(0, min(score, 100))
    if score == 0 and level == "UNKNOWN":
        pass
    elif score < 20 and level not in {"HIGH", "MEDIUM"}:
        level = "LOW" if level == "UNKNOWN" else level

    return FreshnessResult(
        freshness_score=score,
        freshness_level=level,
        freshness_evidence=" ".join(notes),
    )


def lookup_earliest_certificate(domain: str) -> str:
    """
    Ask crt.sh for certificates on one hostname and keep the earliest date.

    If the lookup fails, return "" instead of guessing.
    """
    if not domain:
        return ""
    url = f"https://crt.sh/?q={domain}&output=json&exclude=expired"
    result = fetch_public(
        url,
        accept="application/json",
        max_bytes=400_000,
        timeout=SOURCE_TIMEOUT,
        allow_cross_domain_redirect=True,
        check_robots=False,
    )
    if not result.ok:
        return ""

    dates: list[datetime] = []
    try:
        rows = json.loads(result.text)
    except json.JSONDecodeError:
        return ""

    if not isinstance(rows, list):
        return ""
    for row in rows:
        parsed = parse_loose_date(str(row.get("not_before") or row.get("entry_timestamp") or ""))
        if parsed:
            dates.append(parsed)
    if not dates:
        return ""
    return min(dates).date().isoformat()


def _launch_language(html: str) -> str:
    text = re.sub(r"<[^>]+>", " ", html or "")
    lowered = " ".join(text.lower().split())
    for phrase in LAUNCH_PHRASES:
        if phrase in lowered:
            return phrase
    return ""


def _established_year(html: str) -> int | None:
    years: list[int] = []
    for match in COPYRIGHT_YEAR.findall(html or ""):
        years.append(int(match))
    for match in ESTABLISHED_YEAR.findall(html or ""):
        years.append(int(match))
    return min(years) if years else None


def _age_days(now: datetime, value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max((now - value).days, 0)


def _raise(current: str, candidate: str) -> str:
    return candidate if LEVEL_RANK[candidate] > LEVEL_RANK.get(current, 0) else current
