"""
Shared guard: Phase C migration scripts must use SCRATCH_DATABASE_URL only.

Never reads DATABASE_URL. Never prints connection strings or credentials.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def scratch_database_url() -> str:
    url = os.environ.get("SCRATCH_DATABASE_URL", "").strip()
    if not url:
        raise SystemExit(
            "SCRATCH_DATABASE_URL is not set. "
            "Phase C migration scripts refuse DATABASE_URL / production. "
            "Provide a separate scratch database URL to continue."
        )
    prod = os.environ.get("DATABASE_URL", "").strip()
    if prod and url == prod:
        raise SystemExit(
            "SCRATCH_DATABASE_URL must not equal DATABASE_URL. "
            "Refusing to mutate what looks like production."
        )
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
            url = "postgresql+psycopg2://" + rest
    return url


def redacted(url: str) -> str:
    if "://" not in url:
        return "(invalid)"
    scheme, rest = url.split("://", 1)
    if "@" in rest:
        rest = "***@" + rest.rsplit("@", 1)[-1]
    return f"{scheme}://{rest}"
