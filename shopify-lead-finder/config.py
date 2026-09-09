"""
Local configuration for Shopify Lead Finder.

Change these values in this file. There are no paid APIs or cloud keys.
"""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent

# Pause between HTTP requests so we do not hammer websites.
REQUEST_DELAY = 0.5

# Give slow websites time to answer, then move on.
REQUEST_TIMEOUT = 8

# How many candidate websites to visit in one cycle.
# The web UI allows 5 to 10,000. Public sources may return fewer.
MAX_DOMAINS_PER_CYCLE = 20

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

# A recent certificate means the hostname recently appeared in CT logs.
# It is not a Shopify launch date. Let's Encrypt also renews old stores.
RECENT_CERT_DAYS = 21
RDAP_HIGH_DAYS = 60
RDAP_MEDIUM_DAYS = 180

# Hacker News, Wayback, and Common Crawl only mention existing sites.
# Leave this False so they cannot flood the lead list.
ENABLE_SECONDARY_SOURCES = False

# Empty list means every country. Country is taken from public store pages only.
TARGET_COUNTRIES: list[str] = []
KEEP_UNKNOWN_COUNTRY = True

USER_AGENT = (
    "ShopifyLeadFinder/1.0 "
    "(local public-research tool; respects robots.txt; no login)"
)
