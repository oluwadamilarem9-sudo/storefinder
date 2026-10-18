"""
Apply migrations/phase_c_durable_v1.sql to SCRATCH_DATABASE_URL only.

Usage:
  set SCRATCH_DATABASE_URL=postgresql+psycopg2://...
  python scripts/phase_c_apply_migration.py
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase_c_scratch import redacted, scratch_database_url  # noqa: E402


def main() -> None:
    url = scratch_database_url()
    sql_path = ROOT / "migrations" / "phase_c_durable_v1.sql"
    cleaned = []
    for chunk in sql_path.read_text(encoding="utf-8").split(";"):
        lines = [
            line
            for line in chunk.splitlines()
            if line.strip() and not line.strip().startswith("--")
        ]
        body = "\n".join(lines).strip()
        if body:
            cleaned.append(body)
    engine = create_engine(url)
    print(f"Applying phase_c_durable_v1 to scratch {redacted(url)}")
    with engine.begin() as connection:
        for statement in cleaned:
            connection.execute(text(statement))
    print(f"Applied {len(cleaned)} statements.")


if __name__ == "__main__":
    main()
