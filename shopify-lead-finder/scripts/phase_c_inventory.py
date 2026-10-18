"""
Read-only inventory for a scratch (or local temp) account database.

Excludes orphan local SQLite and global CLI leads.db from import proposals.

Usage:
  set SCRATCH_DATABASE_URL=...
  python scripts/phase_c_inventory.py
"""

from __future__ import annotations

import sys
from collections import defaultdict
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase_c_scratch import redacted, scratch_database_url  # noqa: E402

# Explicitly excluded from production import (preserve files; do not modify).
EXCLUDED_SQLITE_GLOBS = (
    "project_data/users/*/projects/*/leads.db",  # only import if mapped later
    "leads.db",  # global CLI database
)


def main() -> None:
    url = scratch_database_url()
    engine = create_engine(url)
    print(f"Inventory for scratch {redacted(url)}")
    with engine.connect() as connection:
        for table in ("users", "projects", "discovery_runs", "leads", "rejected_candidates"):
            try:
                count = connection.execute(text(f"SELECT COUNT(*) FROM {table}")).scalar()
            except Exception as exc:
                print(f"  {table}: unavailable ({type(exc).__name__})")
                continue
            print(f"  {table}: {count}")
        try:
            count = connection.execute(text("SELECT COUNT(*) FROM processed_domains")).scalar()
            print(f"  processed_domains: {count}")
        except Exception:
            print("  processed_domains: (missing)")

        print("Duplicate (project_id, domain) pairs:")
        for table in ("leads", "rejected_candidates"):
            rows = connection.execute(
                text(
                    f"""
                    SELECT project_id, domain, COUNT(*) AS n
                    FROM {table}
                    GROUP BY project_id, domain
                    HAVING COUNT(*) > 1
                    ORDER BY n DESC
                    """
                )
            ).fetchall()
            print(f"  {table}: {len(rows)} duplicate pairs")
            for project_id, domain, n in rows[:20]:
                print(f"    project={project_id} domain={domain} n={n}")

        print("Per-project counts:")
        per = defaultdict(lambda: {"leads": 0, "rejected": 0})
        for table, key in (("leads", "leads"), ("rejected_candidates", "rejected")):
            rows = connection.execute(
                text(f"SELECT project_id, COUNT(*) FROM {table} GROUP BY project_id")
            ).fetchall()
            for project_id, n in rows:
                per[project_id][key] = int(n)
        for project_id, counts in sorted(per.items(), key=lambda item: -item[1]["rejected"]):
            print(f"  {project_id}: leads={counts['leads']} rejected={counts['rejected']}")

    print("Import policy:")
    print("  - Orphan local SQLite project_data DBs: EXCLUDED unless explicitly mapped")
    print("  - Global CLI leads.db: EXCLUDED")
    print("  - Cloud-only SQLite: not assumed recoverable")


if __name__ == "__main__":
    main()
