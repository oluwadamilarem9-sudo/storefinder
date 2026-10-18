"""
Local configuration for Shopify Lead Finder.

Change these values in this file. There are no paid APIs or cloud keys.
"""

from __future__ import annotations

import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Pause between requests to the same website. Other websites are not held back.
REQUEST_DELAY = 0.2

# Give slow websites time to answer, then move on.
REQUEST_TIMEOUT = 5

# How many different websites to open at the same time during one cycle.
# Temporary Phase A default is 1 so the deadlock fix can be verified serially.
# Override with CHECK_WORKERS=6 (or higher) after Phase A is confirmed.
# Do not treat this as the permanent parallel setting.
CHECK_WORKERS = int(os.environ.get("CHECK_WORKERS") or "1")

# Hard stop for one website check. A slow site is skipped so the cycle moves on.
SITE_CHECK_TIMEOUT = int(os.environ.get("SITE_CHECK_TIMEOUT") or "12")

# How many discovery cycles this machine runs at the same time.
# Each extra computer that runs worker.py adds this many more cycles.
WORKER_SLOTS = int(os.environ.get("WORKER_SLOTS") or "8")

# How many candidate websites to visit in one cycle.
# The web UI allows 5 to 100. Public sources may return fewer.
MAX_DOMAINS_PER_CYCLE = 50

# How long to wait between cycles when running: python main.py --continuous
DISCOVERY_INTERVAL = 60 * 60

# Local files.
OUTPUT_FILE = ROOT / "output" / "leads.csv"
REJECTED_FILE = ROOT / "output" / "rejected_candidates.csv"
DATABASE_FILE = ROOT / "leads.db"

# Only HIGH and MEDIUM records go into leads.csv.
# LOW and UNKNOWN go to rejected_candidates.csv.
MIN_FRESHNESS_LEVEL = "MEDIUM"

# Extra local settings used by the discovery engine.
MAX_CANDIDATES_PER_SOURCE = 25
MAX_RETRIES = 0
# 0 = never re-check. A website from an earlier cycle is never used again.
RECHECK_AFTER_DAYS = 0
MAX_SOURCE_BYTES = 1_000_000
SOURCE_TIMEOUT = 10
FAST_MODE = True
MAX_EXTRA_PAGES = 2

# A store is new only with public proof it appeared inside this window.
# Found today is not launched today. A certificate renewal is not a launch.
NEW_STORE_DAYS = 60
NEW_STORE_HIGH_DAYS = 30
RECENT_CERT_DAYS = 21
RDAP_HIGH_DAYS = 60
RDAP_MEDIUM_DAYS = 180

# Hacker News, Wayback, and Common Crawl only mention existing sites.
# Leave this False so they cannot flood the lead list.
ENABLE_SECONDARY_SOURCES = False

# Empty list means every published country. A blank country is not a lead.
TARGET_COUNTRIES: list[str] = []
KEEP_UNKNOWN_COUNTRY = False

USER_AGENT = (
    "ShopifyLeadFinder/1.0 "
    "(local public-research tool; respects robots.txt; no login)"
)


def project_storage_paths(user_id: str, project_id: str) -> dict[str, Path]:
    """Files for one signed-in project. Never the shared leads.db."""
    safe_user = _safe_storage_id(user_id)
    safe_project = _safe_storage_id(project_id)
    root = ROOT / "project_data" / "users" / safe_user / "projects" / safe_project
    root.mkdir(parents=True, exist_ok=True)
    return {
        "root": root,
        "db": root / "leads.db",
        "leads_csv": root / "leads.csv",
        "rejected_csv": root / "rejected_candidates.csv",
    }


def _safe_storage_id(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9_-]", "", value or "")
    if not cleaned:
        raise ValueError("A user id and project id are required for project storage.")
    return cleaned
