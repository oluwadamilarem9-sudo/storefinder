"""Mark previously discovered domains as processed so they are never re-checked.

Usage:
  python mark_processed.py [--db DB_PATH] [--user USER_ID] [--project PROJECT_ID] [--delete-visible]

This script makes a backup of the SQLite DB before modifying it. It imports domains
from the project's `leads.csv` and `rejected_candidates.csv` (if present) and
inserts them into the `processed_domains` table so future discovery cycles skip them.
If `--delete-visible` is passed the script will also remove the rows from `leads`
and `rejected_candidates` (the processed marker is kept).
"""
from __future__ import annotations

import argparse
import shutil
import sqlite3
from pathlib import Path
import csv
import time

import sys
from pathlib import Path

# Make the shopify-lead-finder package directory importable when running the script
ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import database.database as dbmod


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Mark previous domains as processed")
    p.add_argument("--db", help="Path to SQLite DB (overrides project user/project)", default=None)
    p.add_argument("--user", help="Project user id", default=None)
    p.add_argument("--project", help="Project id", default=None)
    p.add_argument(
        "--delete-visible",
        action="store_true",
        help="Also delete visible rows from leads/rejected tables (processed marker preserved)",
    )
    return p.parse_args()


def load_domains_from_csv(path: Path) -> set[str]:
    if not path.exists():
        return set()
    domains = set()
    with path.open("r", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            d = (row.get("domain") or "").strip()
            if d:
                domains.add(d)
    return domains


def backup_db(db_path: Path) -> Path:
    ts = int(time.time())
    bak = db_path.with_suffix(f".bak.{ts}")
    shutil.copy2(db_path, bak)
    return bak


def mark_processed(connection: sqlite3.Connection, domain: str) -> None:
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    connection.execute(
        "INSERT OR IGNORE INTO processed_domains (domain, first_seen, last_checked, shopify_status, discovery_source) VALUES (?, ?, ?, ?, ?)",
        (domain, now, now, "MANUAL_MARK", "mark_processed_script"),
    )


def main() -> None:
    args = parse_args()
    if args.db:
        db_path = Path(args.db)
    else:
        # Default to the shared local database in the project root
        db_path = ROOT / "leads.db"

    if not db_path.exists():
        print(f"Database not found: {db_path}")
        return

    bak = backup_db(db_path)
    print(f"Backup created: {bak}")

    leads_csv = db_path.parent / "leads.csv"
    rejected_csv = db_path.parent / "rejected_candidates.csv"
    # Also consider the global output files which the discovery engine writes by default.
    global_output = db_path.parent / "output"
    global_leads = global_output / "leads.csv"
    global_rejected = global_output / "rejected_candidates.csv"
    domains = set()
    domains |= load_domains_from_csv(leads_csv)
    domains |= load_domains_from_csv(rejected_csv)
    domains |= load_domains_from_csv(global_leads)
    domains |= load_domains_from_csv(global_rejected)

    if not domains:
        print("No domains found in project CSVs. Nothing to mark.")
        return

    conn = sqlite3.connect(db_path)
    try:
        conn.row_factory = sqlite3.Row
        for d in sorted(domains):
            mark_processed(conn, d)
        conn.commit()

        if args.delete_visible:
            placeholders = ",".join("?" for _ in domains)
            conn.execute(f"DELETE FROM leads WHERE domain IN ({placeholders})", tuple(domains))
            conn.execute(f"DELETE FROM rejected_candidates WHERE domain IN ({placeholders})", tuple(domains))
            conn.commit()
            print(f"Deleted visible rows for {len(domains)} domains.")

        print(f"Marked {len(domains)} domains as processed.")
    finally:
        conn.close()


if __name__ == "__main__":
    main()
