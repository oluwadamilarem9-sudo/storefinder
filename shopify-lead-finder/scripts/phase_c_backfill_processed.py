"""
Backfill processed_domains from distinct lead + rejected domains on scratch.

Idempotent upsert. Does not import orphan local SQLite or global CLI DB.
"""

from __future__ import annotations

import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase_c_scratch import redacted, scratch_database_url  # noqa: E402


def main() -> None:
    url = scratch_database_url()
    engine = create_engine(url)
    now = datetime.now(timezone.utc).isoformat()
    print(f"Backfill processed_domains on scratch {redacted(url)}")
    inserted = 0
    with engine.begin() as connection:
        rows = connection.execute(
            text(
                """
                SELECT project_id, user_id, domain FROM leads
                UNION
                SELECT project_id, user_id, domain FROM rejected_candidates
                """
            )
        ).fetchall()
        for project_id, user_id, domain in rows:
            if not project_id or not user_id or not domain:
                continue
            existing = connection.execute(
                text(
                    """
                    SELECT id FROM processed_domains
                    WHERE project_id = :project_id AND domain = :domain
                    """
                ),
                {"project_id": project_id, "domain": domain},
            ).first()
            if existing:
                connection.execute(
                    text(
                        """
                        UPDATE processed_domains
                        SET last_checked = :last_checked, updated_at = :updated_at
                        WHERE id = :id
                        """
                    ),
                    {"last_checked": now, "updated_at": now, "id": existing[0]},
                )
                continue
            connection.execute(
                text(
                    """
                    INSERT INTO processed_domains (
                        id, project_id, user_id, domain, first_seen, last_checked,
                        shopify_status, discovery_source, updated_at, created_at
                    ) VALUES (
                        :id, :project_id, :user_id, :domain, :first_seen, :last_checked,
                        :shopify_status, :discovery_source, :updated_at, :created_at
                    )
                    """
                ),
                {
                    "id": str(uuid.uuid4()),
                    "project_id": project_id,
                    "user_id": user_id,
                    "domain": domain,
                    "first_seen": now,
                    "last_checked": now,
                    "shopify_status": "UNKNOWN",
                    "discovery_source": "phase_c_backfill",
                    "updated_at": now,
                    "created_at": now,
                },
            )
            inserted += 1
    print(f"Inserted {inserted} processed_domains rows (existing rows updated).")


if __name__ == "__main__":
    main()
