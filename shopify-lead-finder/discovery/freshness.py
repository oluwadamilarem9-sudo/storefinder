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
    NEW_STORE_DAYS,
    NEW_STORE_HIGH_DAYS,
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


@dataclass
class NewStoreResult:
    is_new: bool
    freshness_level: str
    freshness_score: int
    evidence: str
    age_days: int | None = None


def judge_new_store(
    *,
    rdap_created: datetime | None,
    certificate_dates: list[datetime],
    certificate_history_complete: bool,
    earliest_archive: datetime | None,
    archive_lookup_ok: bool,
    homepage_html: str,
    now: datetime | None = None,
) -> NewStoreResult:
    """
    Decide whether public records show a new store.

    A positive result needs a domain registration or a complete earliest
    certificate inside the window, and no older public record.
    """
    now = now or datetime.now(timezone.utc)
    notes: list[str] = []
    negatives: list[str] = []
    positives: list[tuple[int, str]] = []

    old_year = _established_year(homepage_html)
    if old_year and old_year <= now.year - 2:
        negatives.append(
            f"Public page mentions {old_year}, which is an established site."
        )

    if rdap_created:
        age = _age_days(now, rdap_created)
        dated = rdap_created.date().isoformat()
        if age <= NEW_STORE_DAYS:
            positives.append((age, f"Domain registered on {dated} ({age} days ago)."))
        else:
            negatives.append(
                f"Domain registered on {dated}, older than {NEW_STORE_DAYS} days."
            )

    if certificate_dates and certificate_history_complete:
        earliest = min(certificate_dates)
        age = _age_days(now, earliest)
        dated = earliest.date().isoformat()
        if age <= NEW_STORE_DAYS:
            positives.append((age, f"Earliest public certificate is {dated} ({age} days ago)."))
        else:
            negatives.append(
                f"Earliest public certificate is {dated}, older than {NEW_STORE_DAYS} days."
            )
    elif certificate_dates:
        if any(_age_days(now, item) > NEW_STORE_DAYS for item in certificate_dates):
            negatives.append(
                "Certificate history includes a date older than the new-store window."
            )
        else:
            notes.append(
                "Certificate history was incomplete, so a recent certificate is not treated as a new store."
            )
    else:
        notes.append("No complete public certificate history was available.")

    if archive_lookup_ok and earliest_archive:
        age = _age_days(now, earliest_archive)
        dated = earliest_archive.date().isoformat()
        if age > NEW_STORE_DAYS:
            negatives.append(
                f"Web archive first captured this site on {dated}, older than {NEW_STORE_DAYS} days."
            )
        else:
            notes.append(
                f"Web archive first capture on {dated} is recent. That alone is not a launch date."
            )
    elif not archive_lookup_ok:
        notes.append("Web archive lookup did not answer.")

    if negatives:
        evidence = " ".join(negatives + notes)
        return NewStoreResult(False, "LOW", 0, evidence)

    if not positives:
        evidence = (
            "No public proof this store appeared within "
            f"{NEW_STORE_DAYS} days. Found today is not launched today. "
            + " ".join(notes)
        ).strip()
        return NewStoreResult(False, "UNKNOWN", 0, evidence)

    age_days = min(item[0] for item in positives)
    level = "HIGH" if age_days <= NEW_STORE_HIGH_DAYS else "MEDIUM"
    evidence = " ".join([item[1] for item in positives] + notes)
    return NewStoreResult(
        True,
        level,
        80 if level == "HIGH" else 55,
        evidence,
        age_days,
    )


def prove_new_store(domain: str, homepage_html: str = "") -> NewStoreResult:
    """Look up public age records, then judge them. Never invents a launch date."""
    from contact.public_contact_finder import maybe_rdap_created

    dates, complete = _certificate_history(domain)
    rdap_raw = maybe_rdap_created(domain)
    archive, archive_ok = _earliest_archive(domain)
    return judge_new_store(
        rdap_created=parse_loose_date(rdap_raw) if rdap_raw else None,
        certificate_dates=dates,
        certificate_history_complete=complete,
        earliest_archive=archive,
        archive_lookup_ok=archive_ok,
        homepage_html=homepage_html,
    )


def _certificate_history(domain: str) -> tuple[list[datetime], bool]:
    """Return certificate dates and whether the history looks complete."""
    if not domain:
        return [], False
    result = fetch_public(
        f"https://crt.sh/?q={domain}&output=json",
        accept="application/json",
        max_bytes=500_000,
        timeout=max(SOURCE_TIMEOUT, 20),
        allow_cross_domain_redirect=True,
        check_robots=False,
    )
    if not result.ok:
        return [], False
    text = result.text.strip()
    try:
        rows = json.loads(text)
    except json.JSONDecodeError:
        return [], False
    if not isinstance(rows, list):
        return [], False
    dates: list[datetime] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        parsed = parse_loose_date(str(row.get("not_before") or row.get("entry_timestamp") or ""))
        if parsed:
            dates.append(parsed)
    complete = text.endswith("]") and len(text) < 490_000
    return dates, complete


def _earliest_archive(domain: str) -> tuple[datetime | None, bool]:
    """First public web-archive capture. (None, True) means no capture was found."""
    if not domain:
        return None, False
    result = fetch_public(
        "https://web.archive.org/cdx/search/cdx"
        f"?url={domain}/*&output=json&fl=timestamp&limit=1&filter=statuscode:200",
        accept="application/json",
        max_bytes=20_000,
        timeout=max(SOURCE_TIMEOUT, 20),
        allow_cross_domain_redirect=True,
        check_robots=False,
    )
    if result.error == "404" or (result.ok and result.text.strip() in {"", "[]"}):
        return None, True
    if not result.ok:
        return None, False
    try:
        rows = json.loads(result.text)
    except json.JSONDecodeError:
        return None, False
    if not isinstance(rows, list) or not rows:
        return None, True
    stamp = ""
    for row in rows:
        value = str(row[0] if isinstance(row, list) and row else row)
        if value.isdigit() and len(value) >= 8:
            stamp = value
            break
    if not stamp:
        return None, True
    try:
        found = datetime.strptime(stamp[:8], "%Y%m%d").replace(tzinfo=timezone.utc)
    except ValueError:
        return None, False
    return found, True


def _age_days(now: datetime, value: datetime) -> int:
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return max((now - value).days, 0)


def _raise(current: str, candidate: str) -> str:
    return candidate if LEVEL_RANK[candidate] > LEVEL_RANK.get(current, 0) else current
