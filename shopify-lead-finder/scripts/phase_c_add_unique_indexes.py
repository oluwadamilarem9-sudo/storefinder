"""
Add UNIQUE (project_id, domain) indexes on scratch after successful dedupe.

Refuses to proceed if any duplicate pairs remain.
"""

from __future__ import annotations

import sys
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase_c_scratch import redacted, scratch_database_url  # noqa: E402


def _dup_count(connection, table: str) -> int:
    return int(
        connection.execute(
            text(
                f"""
                SELECT COUNT(*) FROM (
                    SELECT project_id, domain
                    FROM {table}
                    GROUP BY project_id, domain
                    HAVING COUNT(*) > 1
                ) d
                """
            )
        ).scalar()
        or 0
    )


def main() -> None:
    url = scratch_database_url()
    engine = create_engine(url)
    print(f"Adding unique indexes on scratch {redacted(url)}")
    with engine.begin() as connection:
        for table in ("leads", "rejected_candidates", "processed_domains"):
            try:
                dups = _dup_count(connection, table)
            except Exception as exc:
                if table == "processed_domains":
                    print(f"  skip {table}: {type(exc).__name__}")
                    continue
                raise
            if dups:
                raise SystemExit(
                    f"Refusing unique index on {table}: {dups} duplicate pairs remain. "
                    "Run phase_c_dedupe.py --apply first."
                )
        dialect = engine.dialect.name
        if dialect == "postgresql":
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_project_domain "
                    "ON leads (project_id, domain)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_rejected_project_domain "
                    "ON rejected_candidates (project_id, domain)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_processed_project_domain "
                    "ON processed_domains (project_id, domain)"
                )
            )
        else:
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_leads_project_domain "
                    "ON leads (project_id, domain)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_rejected_project_domain "
                    "ON rejected_candidates (project_id, domain)"
                )
            )
            connection.execute(
                text(
                    "CREATE UNIQUE INDEX IF NOT EXISTS uq_processed_project_domain "
                    "ON processed_domains (project_id, domain)"
                )
            )
    print("Unique indexes created.")


if __name__ == "__main__":
    main()
