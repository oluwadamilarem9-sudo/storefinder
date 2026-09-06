"""
Local SQLite storage for discovered domains and public lead fields.

The database remembers domains so the same website is not processed
again and again in later cycles.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pandas as pd

from config import DATABASE_FILE, MIN_FRESHNESS_LEVEL, OUTPUT_FILE, REJECTED_FILE
from utils.normalization import now_iso

_LEVEL_RANK = {"UNKNOWN": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3}

LEAD_COLUMNS = [
    "store_name",
    "domain",
    "public_email",
    "shopify_status",
    "freshness_level",
    "freshness_score",
    "freshness_evidence",
    "country",
    "contact_page",
    "about_page",
    "instagram",
    "facebook",
    "tiktok",
    "linkedin",
    "discovery_source",
    "source_url",
    "first_seen",
    "last_checked",
]


def connect(path: Path | None = None) -> sqlite3.Connection:
    """Open the local database and create tables if needed."""
    db_path = path or DATABASE_FILE
    db_path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_path)
    connection.row_factory = sqlite3.Row
    _create_tables(connection)
    _migrate_leads(connection)
    _rehome_unscored_leads(connection)
    return connection


def _create_tables(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        CREATE TABLE IF NOT EXISTS processed_domains (
            domain TEXT PRIMARY KEY,
            first_seen TEXT NOT NULL,
            last_checked TEXT NOT NULL,
            shopify_status TEXT,
            discovery_source TEXT
        );

        CREATE TABLE IF NOT EXISTS leads (
            domain TEXT PRIMARY KEY,
            store_name TEXT,
            shopify_status TEXT,
            public_email TEXT,
            contact_page TEXT,
            about_page TEXT,
            instagram TEXT,
            facebook TEXT,
            tiktok TEXT,
            linkedin TEXT,
            youtube TEXT,
            country TEXT,
            discovery_source TEXT,
            source_url TEXT,
            discovered_at TEXT,
            first_seen TEXT NOT NULL,
            last_checked TEXT NOT NULL,
            freshness_score INTEGER,
            freshness_level TEXT,
            freshness_evidence TEXT,
            estimated_newness TEXT,
            newness_confidence TEXT
        );

        CREATE TABLE IF NOT EXISTS rejected_candidates (
            domain TEXT PRIMARY KEY,
            store_name TEXT,
            shopify_status TEXT,
            public_email TEXT,
            freshness_level TEXT,
            freshness_score INTEGER,
            freshness_evidence TEXT,
            discovery_source TEXT,
            source_url TEXT,
            reject_reason TEXT,
            first_seen TEXT NOT NULL,
            last_checked TEXT NOT NULL
        );
        """
    )
    connection.commit()


def _migrate_leads(connection: sqlite3.Connection) -> None:
    columns = {row[1] for row in connection.execute("PRAGMA table_info(leads)")}
    additions = {
        "source_url": "TEXT",
        "freshness_score": "INTEGER",
        "freshness_level": "TEXT",
        "freshness_evidence": "TEXT",
    }
    for name, typ in additions.items():
        if name not in columns:
            connection.execute(f"ALTER TABLE leads ADD COLUMN {name} {typ}")
    connection.commit()


def _rehome_unscored_leads(connection: sqlite3.Connection) -> None:
    """Move old rows with no freshness score out of the main lead list."""
    rows = connection.execute(
        """
        SELECT domain, store_name, shopify_status, public_email, discovery_source,
               first_seen, last_checked
        FROM leads
        WHERE freshness_level IS NULL OR freshness_level = ''
        """
    ).fetchall()
    for row in rows:
        connection.execute(
            """
            INSERT OR IGNORE INTO rejected_candidates (
                domain, store_name, shopify_status, public_email, freshness_level,
                freshness_score, freshness_evidence, discovery_source, source_url,
                reject_reason, first_seen, last_checked
            ) VALUES (?, ?, ?, ?, 'UNKNOWN', 0, ?, ?, '', 'legacy_unscored_record', ?, ?)
            """,
            (
                row["domain"],
                row["store_name"],
                row["shopify_status"],
                row["public_email"],
                "Older record had no freshness evidence. Discovery date is not a launch date.",
                row["discovery_source"],
                row["first_seen"],
                row["last_checked"],
            ),
        )
        connection.execute("DELETE FROM leads WHERE domain = ?", (row["domain"],))
    if rows:
        connection.commit()


def already_processed(connection: sqlite3.Connection, domain: str, recheck_after_days: int) -> bool:
    """True when this domain was checked recently enough to skip."""
    row = connection.execute(
        "SELECT last_checked FROM processed_domains WHERE domain = ?",
        (domain,),
    ).fetchone()
    if row is None:
        return False
    if recheck_after_days <= 0:
        return True
    last_checked = row["last_checked"]
    try:
        from datetime import datetime, timezone

        checked = datetime.fromisoformat(last_checked)
        if checked.tzinfo is None:
            checked = checked.replace(tzinfo=timezone.utc)
        age_days = (datetime.now(timezone.utc) - checked).days
        return age_days < recheck_after_days
    except Exception:
        return True


def recently_processed_domains(
    connection: sqlite3.Connection,
    recheck_after_days: int,
) -> set[str]:
    """Domains the pipeline should skip until the recheck window expires."""
    from datetime import datetime, timezone

    rows = connection.execute(
        "SELECT domain, last_checked FROM processed_domains"
    ).fetchall()
    skipped: set[str] = set()
    now = datetime.now(timezone.utc)
    for row in rows:
        domain = str(row["domain"] or "").strip()
        if not domain:
            continue
        if recheck_after_days <= 0:
            skipped.add(domain)
            continue
        try:
            checked = datetime.fromisoformat(row["last_checked"])
            if checked.tzinfo is None:
                checked = checked.replace(tzinfo=timezone.utc)
            if (now - checked).days < recheck_after_days:
                skipped.add(domain)
        except Exception:
            skipped.add(domain)
    return skipped


def is_new_domain(connection: sqlite3.Connection, domain: str) -> bool:
    row = connection.execute(
        "SELECT domain FROM processed_domains WHERE domain = ?",
        (domain,),
    ).fetchone()
    return row is None


def mark_processed(
    connection: sqlite3.Connection,
    domain: str,
    shopify_status: str,
    discovery_source: str = "",
) -> None:
    """Remember that a domain was checked, even if it was not Shopify."""
    timestamp = now_iso()
    existing = connection.execute(
        "SELECT first_seen FROM processed_domains WHERE domain = ?",
        (domain,),
    ).fetchone()
    first_seen = existing["first_seen"] if existing else timestamp
    connection.execute(
        """
        INSERT INTO processed_domains (domain, first_seen, last_checked, shopify_status, discovery_source)
        VALUES (?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            last_checked = excluded.last_checked,
            shopify_status = excluded.shopify_status
        """,
        (domain, first_seen, timestamp, shopify_status, discovery_source),
    )
    connection.commit()


def upsert_lead(connection: sqlite3.Connection, lead: dict) -> None:
    """Insert or update one Shopify store that met the freshness threshold."""
    timestamp = now_iso()
    existing = connection.execute(
        "SELECT first_seen FROM leads WHERE domain = ?",
        (lead["domain"],),
    ).fetchone()
    first_seen = existing["first_seen"] if existing else lead.get("first_seen") or timestamp
    connection.execute(
        """
        INSERT INTO leads (
            domain, store_name, shopify_status, public_email, contact_page, about_page,
            instagram, facebook, tiktok, linkedin, youtube, country, discovery_source,
            source_url, discovered_at, first_seen, last_checked, freshness_score,
            freshness_level, freshness_evidence
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            store_name = excluded.store_name,
            shopify_status = excluded.shopify_status,
            public_email = excluded.public_email,
            contact_page = excluded.contact_page,
            about_page = excluded.about_page,
            instagram = excluded.instagram,
            facebook = excluded.facebook,
            tiktok = excluded.tiktok,
            linkedin = excluded.linkedin,
            youtube = excluded.youtube,
            country = excluded.country,
            discovery_source = excluded.discovery_source,
            source_url = excluded.source_url,
            last_checked = excluded.last_checked,
            freshness_score = excluded.freshness_score,
            freshness_level = excluded.freshness_level,
            freshness_evidence = excluded.freshness_evidence
        """,
        (
            lead.get("domain", ""),
            lead.get("store_name", ""),
            lead.get("shopify_status", ""),
            lead.get("public_email", ""),
            lead.get("contact_page", ""),
            lead.get("about_page", ""),
            lead.get("instagram", ""),
            lead.get("facebook", ""),
            lead.get("tiktok", ""),
            lead.get("linkedin", ""),
            lead.get("youtube", ""),
            lead.get("country", ""),
            lead.get("discovery_source", ""),
            lead.get("source_url", ""),
            lead.get("discovered_at", timestamp),
            first_seen,
            timestamp,
            lead.get("freshness_score", 0),
            lead.get("freshness_level", "UNKNOWN"),
            lead.get("freshness_evidence", ""),
        ),
    )
    mark_processed(
        connection,
        lead.get("domain", ""),
        lead.get("shopify_status", "SHOPIFY"),
        lead.get("discovery_source", ""),
    )
    connection.commit()


def upsert_rejected(connection: sqlite3.Connection, row: dict) -> None:
    timestamp = now_iso()
    existing = connection.execute(
        "SELECT first_seen FROM rejected_candidates WHERE domain = ?",
        (row["domain"],),
    ).fetchone()
    first_seen = existing["first_seen"] if existing else timestamp
    connection.execute(
        """
        INSERT INTO rejected_candidates (
            domain, store_name, shopify_status, public_email, freshness_level,
            freshness_score, freshness_evidence, discovery_source, source_url,
            reject_reason, first_seen, last_checked
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(domain) DO UPDATE SET
            last_checked = excluded.last_checked,
            freshness_level = excluded.freshness_level,
            freshness_score = excluded.freshness_score,
            freshness_evidence = excluded.freshness_evidence,
            reject_reason = excluded.reject_reason
        """,
        (
            row.get("domain", ""),
            row.get("store_name", ""),
            row.get("shopify_status", ""),
            row.get("public_email", ""),
            row.get("freshness_level", "UNKNOWN"),
            row.get("freshness_score", 0),
            row.get("freshness_evidence", ""),
            row.get("discovery_source", ""),
            row.get("source_url", ""),
            row.get("reject_reason", "below_freshness_threshold"),
            first_seen,
            timestamp,
        ),
    )
    mark_processed(
        connection,
        row.get("domain", ""),
        row.get("shopify_status", ""),
        row.get("discovery_source", ""),
    )
    connection.commit()


def delete_leads(connection: sqlite3.Connection, domains: list[str] | None = None) -> int:
    """
    Delete qualifying leads.

    If domains is None, delete all lead rows.
    Also forget them in processed_domains so they can be discovered again.
    """
    if domains is None:
        rows = connection.execute("SELECT domain FROM leads").fetchall()
        targets = [row["domain"] for row in rows]
    else:
        targets = [item for item in domains if item]

    if not targets:
        return 0

    placeholders = ",".join("?" * len(targets))
    connection.execute(
        f"DELETE FROM leads WHERE domain IN ({placeholders})",
        targets,
    )
    connection.execute(
        f"DELETE FROM processed_domains WHERE domain IN ({placeholders})",
        targets,
    )
    connection.commit()
    export_csv(connection)
    return len(targets)


def delete_rejected(connection: sqlite3.Connection, domains: list[str] | None = None) -> int:
    """
    Delete rejected candidates.

    If domains is None, delete all rejected rows.
    Also forget them in processed_domains so they can be discovered again.
    """
    if domains is None:
        rows = connection.execute("SELECT domain FROM rejected_candidates").fetchall()
        targets = [row["domain"] for row in rows]
    else:
        targets = [item for item in domains if item]

    if not targets:
        return 0

    placeholders = ",".join("?" * len(targets))
    connection.execute(
        f"DELETE FROM rejected_candidates WHERE domain IN ({placeholders})",
        targets,
    )
    connection.execute(
        f"DELETE FROM processed_domains WHERE domain IN ({placeholders})",
        targets,
    )
    connection.commit()
    export_rejected_csv(connection)
    return len(targets)


def latest_leads(connection: sqlite3.Connection, limit: int = 5) -> list[dict]:
    rows = connection.execute(
        """
        SELECT store_name, domain, public_email, freshness_level
        FROM leads
        ORDER BY freshness_score DESC, last_checked DESC
        LIMIT ?
        """,
        (limit,),
    ).fetchall()
    return [dict(row) for row in rows]


def export_csv(connection: sqlite3.Connection, path: Path | None = None) -> Path:
    """Write HIGH/MEDIUM Shopify records to output/leads.csv."""
    output = path or OUTPUT_FILE
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = connection.execute(
        """
        SELECT store_name, domain, public_email, shopify_status, freshness_level,
               freshness_score, freshness_evidence, country, contact_page, about_page,
               instagram, facebook, tiktok, linkedin, discovery_source, source_url,
               first_seen, last_checked
        FROM leads
        WHERE shopify_status = 'SHOPIFY'
        ORDER BY freshness_score DESC, last_checked DESC
        """
    ).fetchall()
    kept = [
        dict(row)
        for row in rows
        if _LEVEL_RANK.get(row["freshness_level"] or "UNKNOWN", 0)
        >= _LEVEL_RANK.get(MIN_FRESHNESS_LEVEL, 0)
    ]
    frame = pd.DataFrame(kept, columns=LEAD_COLUMNS)
    frame.to_csv(output, index=False, encoding="utf-8")
    return output


def load_leads_frame(connection: sqlite3.Connection) -> pd.DataFrame:
    """Return qualifying leads as a DataFrame for the web UI."""
    rows = connection.execute(
        """
        SELECT store_name, domain, public_email, shopify_status, freshness_level,
               freshness_score, freshness_evidence, country, contact_page, about_page,
               instagram, facebook, tiktok, linkedin, discovery_source, source_url,
               first_seen, last_checked
        FROM leads
        WHERE shopify_status = 'SHOPIFY'
        ORDER BY freshness_score DESC, last_checked DESC
        """
    ).fetchall()
    kept = [
        dict(row)
        for row in rows
        if _LEVEL_RANK.get(row["freshness_level"] or "UNKNOWN", 0)
        >= _LEVEL_RANK.get(MIN_FRESHNESS_LEVEL, 0)
    ]
    return pd.DataFrame(kept, columns=LEAD_COLUMNS)


def load_rejected_frame(connection: sqlite3.Connection) -> pd.DataFrame:
    rows = connection.execute(
        """
        SELECT domain, store_name, shopify_status, public_email, freshness_level,
               freshness_score, freshness_evidence, discovery_source, source_url,
               reject_reason, first_seen, last_checked
        FROM rejected_candidates
        ORDER BY last_checked DESC
        """
    ).fetchall()
    return pd.DataFrame([dict(row) for row in rows])


def export_rejected_csv(connection: sqlite3.Connection, path: Path | None = None) -> Path:
    output = path or REJECTED_FILE
    output.parent.mkdir(parents=True, exist_ok=True)
    rows = connection.execute(
        """
        SELECT domain, store_name, shopify_status, public_email, freshness_level,
               freshness_score, freshness_evidence, discovery_source, source_url,
               reject_reason, first_seen, last_checked
        FROM rejected_candidates
        ORDER BY last_checked DESC
        """
    ).fetchall()
    frame = pd.DataFrame([dict(row) for row in rows])
    frame.to_csv(output, index=False, encoding="utf-8")
    return output
