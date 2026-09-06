"""
Public discovery sources for recently appearing Shopify hostnames.

Primary sources look for hostnames that recently showed up in public
certificate or scan data. That is NOT a guaranteed store launch date.

Secondary sources (Hacker News, Wayback, Common Crawl) only mention
existing websites. They are off by default because they flood the
pipeline with old stores.

If a source blocks automated access, it is skipped.
"""

from __future__ import annotations

import json
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import quote

from config import (
    ENABLE_SECONDARY_SOURCES,
    MAX_CANDIDATES_PER_SOURCE,
    MAX_SOURCE_BYTES,
    RECENT_CERT_DAYS,
    SOURCE_TIMEOUT,
)
from utils.http import fetch_public
from utils.normalization import (
    is_junk_store_domain,
    is_usable_shop_domain,
    normalize_domain,
    now_iso,
    parse_loose_date,
)

MYSHOPIFY_FINDER = re.compile(r"\b[a-z0-9][a-z0-9-]{0,60}\.myshopify\.com\b", re.IGNORECASE)


@dataclass
class DiscoveredCandidate:
    domain: str
    source: str
    source_url: str
    discovered_at: str
    evidence: str
    extra: dict = field(default_factory=dict)


class PublicDiscoverySource:
    name = "base"
    tier = "primary"

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        raise NotImplementedError


class CrtShRecentCertificateSource(PublicDiscoverySource):
    """
    Primary source: recently published Certificate Transparency records.

    Why it is useful: a brand-new *.myshopify.com hostname usually
    receives a public TLS certificate quickly. The atom feed lists
    recent certificates rather than the entire historical dump.

    Why it does not prove a launch: Let's Encrypt also renews old
    stores. freshness.py later checks the earliest certificate date.
    """

    name = "crt.sh_recent"
    tier = "primary"
    atom_url = "https://crt.sh/atom?q=%.myshopify.com"
    json_url = "https://crt.sh/?q=%.myshopify.com&output=json&exclude=expired"

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        result = fetch_public(
            self.atom_url,
            accept="application/atom+xml, application/xml, text/xml",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not result.ok:
            print(f"  {self.name} atom feed failed ({result.error}). Trying JSON listing...")
            return self._from_json(limit)

        try:
            root = ET.fromstring(result.text)
        except ET.ParseError:
            print(f"  Skipping {self.name} (invalid XML).")
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_CERT_DAYS)
        found: list[DiscoveredCandidate] = []
        seen: set[str] = set()

        for entry in root.iter():
            tag = entry.tag.lower()
            if not tag.endswith("entry"):
                continue
            title, updated, link = _atom_fields(entry)
            stamp = parse_loose_date(updated)
            if stamp and stamp < cutoff:
                continue
            for domain in domains_from_text(f"{title} {entry.text or ''}"):
                if domain in seen:
                    continue
                seen.add(domain)
                cert_date = stamp.date().isoformat() if stamp else ""
                found.append(
                    DiscoveredCandidate(
                        domain=domain,
                        source=self.name,
                        source_url=link or self.atom_url,
                        discovered_at=now_iso(),
                        evidence=(
                            f"Hostname appeared in the crt.sh recent-certificate feed"
                            + (f" on {cert_date}" if cert_date else "")
                            + ". This is a public certificate observation, not a store launch date."
                        ),
                        extra={
                            "source_kind": "certificate_transparency",
                            "cert_not_before": cert_date,
                        },
                    )
                )
                if len(found) >= limit:
                    return found

        if found:
            return found

        # Some atom feeds do not wrap each cert in <entry>. Harvest hostnames
        # only when the feed itself is the recent-certificate document.
        for domain in domains_from_text(result.text):
            if domain in seen:
                continue
            seen.add(domain)
            found.append(
                DiscoveredCandidate(
                    domain=domain,
                    source=self.name,
                    source_url=self.atom_url,
                    discovered_at=now_iso(),
                    evidence=(
                        "Hostname appeared in the crt.sh recent-certificate feed. "
                        "Certificate date still needs confirmation. "
                        "This is not a store launch date."
                    ),
                    extra={"source_kind": "certificate_transparency"},
                )
            )
            if len(found) >= limit:
                break
        return found

    def _from_json(self, limit: int) -> list[DiscoveredCandidate]:
        result = fetch_public(
            self.json_url,
            accept="application/json",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not result.ok:
            print(f"  Skipping {self.name} ({result.error}).")
            return []
        found: list[DiscoveredCandidate] = []
        seen: set[str] = set()
        for domain in domains_from_text(result.text):
            if domain in seen:
                continue
            seen.add(domain)
            found.append(
                DiscoveredCandidate(
                    domain=domain,
                    source=self.name,
                    source_url=self.json_url,
                    discovered_at=now_iso(),
                    evidence=(
                        "Hostname appeared in a crt.sh certificate listing. "
                        "This is not a store launch date."
                    ),
                    extra={"source_kind": "certificate_transparency"},
                )
            )
            if len(found) >= limit:
                break
        return found


class UrlscanRecentSource(PublicDiscoverySource):
    """
    Primary source: recent public urlscan.io results.

    Why it is useful: people and scanners submit live storefronts.
    A first-time or very recent scan can mean the site just became
    visible on the public web.

    Why it does not prove a launch: the scan date is when urlscan
    saw the page, not when the store opened.
    """

    name = "urlscan_recent"
    tier = "primary"
    queries = (
        "page.domain:myshopify.com",
        "domain:myshopify.com",
        "page.server:Shopify",
    )

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        found: list[DiscoveredCandidate] = []
        seen: set[str] = set()
        last_error = ""

        for query in self.queries:
            search_after = None
            for _page in range(3):
                if len(found) >= limit:
                    break
                url = (
                    "https://urlscan.io/api/v1/search/"
                    f"?q={quote(query)}&size=100"
                )
                if search_after:
                    url += f"&search_after={quote(','.join(str(part) for part in search_after))}"
                payload, last_error = self._search(url)
                if payload is None:
                    break
                results = payload.get("results") or []
                self._collect(payload, url, found, seen, limit)
                sort_key = results[-1].get("sort") if results else None
                if not results or not isinstance(sort_key, list) or len(found) >= limit:
                    break
                search_after = sort_key

        if not found and last_error:
            print(f"  Skipping {self.name} ({last_error}).")
        return found

    def _search(self, url: str) -> tuple[dict | None, str]:
        result = fetch_public(
            url,
            accept="application/json",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if result.error == "429":
            print("  urlscan.io asked us to slow down. Waiting and retrying once...")
            time.sleep(8)
            result = fetch_public(
                url,
                accept="application/json",
                max_bytes=MAX_SOURCE_BYTES,
                timeout=SOURCE_TIMEOUT,
                allow_cross_domain_redirect=True,
                check_robots=False,
            )
        if not result.ok:
            return None, result.error
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError:
            return None, "invalid JSON"
        if not isinstance(payload, dict):
            return None, "invalid JSON"
        return payload, ""

    def _collect(
        self,
        payload: dict,
        url: str,
        found: list[DiscoveredCandidate],
        seen: set[str],
        limit: int,
    ) -> None:
        for row in payload.get("results", []):
            page = row.get("page") or {}
            task = row.get("task") or {}
            host = normalize_domain(page.get("domain") or task.get("domain") or "")
            if not is_usable_shop_domain(host) or is_junk_store_domain(host) or host in seen:
                continue
            if not host.endswith(".myshopify.com"):
                server = str(page.get("server") or "").lower()
                if "shopify" not in server:
                    continue
            stamp = parse_loose_date(str(task.get("time") or row.get("indexedAt") or ""))
            scan_url = row.get("result") or ""
            source_url = scan_url if str(scan_url).startswith("http") else f"https://urlscan.io/domain/{host}"
            scan_date = stamp.date().isoformat() if stamp else ""
            seen.add(host)
            found.append(
                DiscoveredCandidate(
                    domain=host,
                    source=self.name,
                    source_url=source_url,
                    discovered_at=now_iso(),
                    evidence=(
                        f"Public urlscan.io result for this hostname"
                        + (f" on {scan_date}" if scan_date else "")
                        + ". Scan date is not a store launch date."
                    ),
                    extra={"source_kind": "public_scan", "source_timestamp": scan_date},
                )
            )
            if len(found) >= limit:
                return


class CertSpotterRecentSource(PublicDiscoverySource):
    """
    Primary source: Cert Spotter issuances for myshopify.com.

    Why it is useful: another free public view of newly issued
    certificates for Shopify-hosted hostnames.

    Skipped automatically if the unauthenticated API is blocked.
    """

    name = "certspotter_recent"
    tier = "primary"
    api_url = (
        "https://api.certspotter.com/v1/issuances"
        "?domain=myshopify.com&include_subdomains=true&expand=dns_names"
    )

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        result = fetch_public(
            self.api_url,
            accept="application/json",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not result.ok:
            print(f"  Skipping {self.name} ({result.error}).")
            return []

        try:
            rows = json.loads(result.text)
        except json.JSONDecodeError:
            print(f"  Skipping {self.name} (invalid JSON).")
            return []

        if not isinstance(rows, list):
            return []

        cutoff = datetime.now(timezone.utc) - timedelta(days=RECENT_CERT_DAYS)
        found: list[DiscoveredCandidate] = []
        seen: set[str] = set()
        for row in rows:
            stamp = parse_loose_date(str(row.get("not_before") or row.get("tbs_sha256") or ""))
            names = row.get("dns_names") or []
            blob = " ".join(names) if isinstance(names, list) else str(names)
            if stamp and stamp < cutoff:
                continue
            for domain in domains_from_text(blob):
                if domain in seen:
                    continue
                seen.add(domain)
                cert_date = stamp.date().isoformat() if stamp else ""
                found.append(
                    DiscoveredCandidate(
                        domain=domain,
                        source=self.name,
                        source_url=self.api_url,
                        discovered_at=now_iso(),
                        evidence=(
                            "Public Cert Spotter issuance for this hostname"
                            + (f" with not_before {cert_date}" if cert_date else "")
                            + ". Certificate date is not a store launch date."
                        ),
                        extra={
                            "source_kind": "certificate_transparency",
                            "cert_not_before": cert_date,
                        },
                    )
                )
                if len(found) >= limit:
                    return found
        return found


class CommonCrawlSource(PublicDiscoverySource):
    """Secondary. A crawl date only means the page was archived, not launched."""

    name = "common_crawl"
    tier = "secondary"
    collections_url = "https://index.commoncrawl.org/collinfo.json"

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        collections = fetch_public(
            self.collections_url,
            accept="application/json",
            max_bytes=500_000,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not collections.ok:
            print(f"  Skipping {self.name} ({collections.error}).")
            return []
        try:
            items = json.loads(collections.text)
        except json.JSONDecodeError:
            return []
        if not items:
            return []
        cdxi_api = items[0].get("cdx-api") or items[0].get("id")
        if not cdxi_api:
            return []
        if not str(cdxi_api).startswith("http"):
            cdxi_api = f"https://index.commoncrawl.org/{cdxi_api}-index"
        query = f"{cdxi_api}?url=*.myshopify.com&output=json&fl=url,timestamp&filter=status:200&limit={limit}"
        result = fetch_public(
            query,
            accept="application/json",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not result.ok:
            print(f"  Skipping {self.name} ({result.error}).")
            return []
        return _index_lines_to_candidates(
            result.text,
            source=self.name,
            source_url=query,
            kind="web_index",
            limit=limit,
        )


class WaybackMachineSource(PublicDiscoverySource):
    """Secondary. Archive date is not a launch date."""

    name = "wayback_machine"
    tier = "secondary"

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        year = datetime.now(timezone.utc).year
        query = (
            "https://web.archive.org/cdx/search/cdx"
            "?url=*.myshopify.com/*&output=json&fl=original,timestamp"
            f"&filter=statuscode:200&collapse=urlkey&limit={limit}"
            f"&from={year}0101"
        )
        result = fetch_public(
            query,
            accept="application/json",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not result.ok:
            print(f"  Skipping {self.name} ({result.error}).")
            return []
        try:
            rows = json.loads(result.text)
        except json.JSONDecodeError:
            return []
        found: list[DiscoveredCandidate] = []
        seen: set[str] = set()
        for row in rows[1:] if rows and isinstance(rows[0], list) else []:
            if not isinstance(row, list) or not row:
                continue
            for domain in domains_from_text(row[0]):
                if domain in seen:
                    continue
                seen.add(domain)
                found.append(
                    DiscoveredCandidate(
                        domain=domain,
                        source=self.name,
                        source_url=query,
                        discovered_at=now_iso(),
                        evidence=(
                            "Listed in the Internet Archive CDX index. "
                            "An archive date is not a store launch date."
                        ),
                        extra={"source_kind": "web_archive", "source_timestamp": row[1] if len(row) > 1 else ""},
                    )
                )
                if len(found) >= limit:
                    return found
        return found


class HackerNewsPublicApiSource(PublicDiscoverySource):
    """Secondary. HN mentions existing sites; it is not a new-store feed."""

    name = "hacker_news_api"
    tier = "secondary"
    api_url = "https://hn.algolia.com/api/v1/search?query=myshopify.com&hitsPerPage=20"

    def discover(self, limit: int = MAX_CANDIDATES_PER_SOURCE) -> list[DiscoveredCandidate]:
        result = fetch_public(
            self.api_url,
            accept="application/json",
            max_bytes=MAX_SOURCE_BYTES,
            timeout=SOURCE_TIMEOUT,
            allow_cross_domain_redirect=True,
            check_robots=False,
        )
        if not result.ok:
            print(f"  Skipping {self.name} ({result.error}).")
            return []
        try:
            payload = json.loads(result.text)
        except json.JSONDecodeError:
            return []
        found: list[DiscoveredCandidate] = []
        seen: set[str] = set()
        for hit in payload.get("hits", []):
            blob = " ".join(str(hit.get(key) or "") for key in ("url", "story_url", "title", "story_text"))
            object_id = hit.get("objectID") or ""
            source_url = f"https://news.ycombinator.com/item?id={object_id}" if object_id else self.api_url
            for domain in domains_from_text(blob):
                if domain in seen:
                    continue
                seen.add(domain)
                found.append(
                    DiscoveredCandidate(
                        domain=domain,
                        source=self.name,
                        source_url=source_url,
                        discovered_at=now_iso(),
                        evidence=(
                            "Mentioned on Hacker News. A discussion date is not a store launch date."
                        ),
                        extra={"source_kind": "public_discussion"},
                    )
                )
                if len(found) >= limit:
                    return found
        return found


PRIMARY_SOURCES: list[PublicDiscoverySource] = [
    UrlscanRecentSource(),
    CrtShRecentCertificateSource(),
    CertSpotterRecentSource(),
]

SECONDARY_SOURCES: list[PublicDiscoverySource] = [
    CommonCrawlSource(),
    WaybackMachineSource(),
    HackerNewsPublicApiSource(),
]


def active_sources() -> list[PublicDiscoverySource]:
    sources = list(PRIMARY_SOURCES)
    if ENABLE_SECONDARY_SOURCES:
        sources.extend(SECONDARY_SOURCES)
    return sources


def fallback_sources() -> list[PublicDiscoverySource]:
    """Used only when every primary source comes back empty."""
    return [
        WaybackMachineSource(),
        CommonCrawlSource(),
        HackerNewsPublicApiSource(),
    ]


def domains_from_text(text: str) -> list[str]:
    found: list[str] = []
    seen: set[str] = set()
    for match in MYSHOPIFY_FINDER.findall(text or ""):
        domain = normalize_domain(match)
        if not is_usable_shop_domain(domain) or is_junk_store_domain(domain) or domain in seen:
            continue
        seen.add(domain)
        found.append(domain)
    return found


def _atom_fields(entry: ET.Element) -> tuple[str, str, str]:
    title = ""
    updated = ""
    link = ""
    for child in entry:
        tag = child.tag.lower()
        if tag.endswith("title"):
            title = child.text or ""
        elif tag.endswith("updated") or tag.endswith("published"):
            updated = child.text or ""
        elif tag.endswith("id") and (child.text or "").startswith("http"):
            link = child.text or ""
        elif tag.endswith("link"):
            link = child.attrib.get("href") or link
    return title, updated, link


def _index_lines_to_candidates(
    text: str,
    source: str,
    source_url: str,
    kind: str,
    limit: int,
) -> list[DiscoveredCandidate]:
    found: list[DiscoveredCandidate] = []
    seen: set[str] = set()
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
            url = row.get("url") or ""
        except json.JSONDecodeError:
            url = line
        for domain in domains_from_text(url):
            if domain in seen:
                continue
            seen.add(domain)
            found.append(
                DiscoveredCandidate(
                    domain=domain,
                    source=source,
                    source_url=source_url,
                    discovered_at=now_iso(),
                    evidence=(
                        "Listed in a public web index. An index date is not a store launch date."
                    ),
                    extra={"source_kind": kind},
                )
            )
            if len(found) >= limit:
                return found
    return found
