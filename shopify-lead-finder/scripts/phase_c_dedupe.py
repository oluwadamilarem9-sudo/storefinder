"""
Dedupe leads / rejected_candidates within each project on SCRATCH only.

1. Dry-run (default): print before counts and proposed survivors
2. --apply: copy losers into *_dedupe_backup, delete losers, print after + report

Never merges across projects. Never uses DATABASE_URL.

Usage:
  set SCRATCH_DATABASE_URL=...
  python scripts/phase_c_dedupe.py
  python scripts/phase_c_dedupe.py --apply
"""

from __future__ import annotations

import json
import sys
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, text

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from phase_c_scratch import redacted, scratch_database_url  # noqa: E402


def _pick_survivor(rows: list[dict]) -> dict:
    def score(row: dict) -> tuple:
        has_run = 1 if row.get("run_id") else 0
        created = str(row.get("created_at") or "")
        return (has_run, created, str(row.get("id") or ""))

    return sorted(rows, key=score, reverse=True)[0]


def _merge_fields(survivor: dict, losers: list[dict], fields: list[str]) -> dict:
    merged = dict(survivor)
    for field in fields:
        if merged.get(field) not in (None, ""):
            continue
        for loser in losers:
            if loser.get(field) not in (None, ""):
                merged[field] = loser[field]
                break
    return merged


def _table_rows(connection, table: str) -> list[dict]:
    result = connection.execute(text(f"SELECT * FROM {table}"))
    columns = list(result.keys())
    return [dict(zip(columns, row)) for row in result.fetchall()]


def _group_duplicates(rows: list[dict]) -> dict[tuple[str, str], list[dict]]:
    groups: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for row in rows:
        project_id = str(row.get("project_id") or "")
        domain = str(row.get("domain") or "")
        if not project_id or not domain:
            continue
        groups[(project_id, domain)].append(row)
    return {key: value for key, value in groups.items() if len(value) > 1}


def _ensure_backup_tables(connection) -> None:
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS leads_dedupe_backup (
                backup_id TEXT PRIMARY KEY,
                original_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                run_id TEXT,
                domain TEXT NOT NULL,
                store_name TEXT,
                public_email TEXT,
                country TEXT,
                freshness_level TEXT,
                freshness_score INTEGER,
                source TEXT,
                created_at TIMESTAMP,
                backed_up_at TIMESTAMP,
                survivor_id TEXT,
                row_json TEXT
            )
            """
        )
    )
    connection.execute(
        text(
            """
            CREATE TABLE IF NOT EXISTS rejected_candidates_dedupe_backup (
                backup_id TEXT PRIMARY KEY,
                original_id TEXT NOT NULL,
                project_id TEXT NOT NULL,
                user_id TEXT NOT NULL,
                run_id TEXT,
                domain TEXT NOT NULL,
                store_name TEXT,
                public_email TEXT,
                reason TEXT,
                created_at TIMESTAMP,
                backed_up_at TIMESTAMP,
                survivor_id TEXT,
                row_json TEXT
            )
            """
        )
    )


def _dedupe_table(connection, table: str, backup: str, fields: list[str], apply: bool) -> dict:
    rows = _table_rows(connection, table)
    before = len(rows)
    groups = _group_duplicates(rows)
    moved = 0
    report = []
    now = datetime.now(timezone.utc).isoformat()
    for (project_id, domain), group in sorted(groups.items()):
        survivor = _pick_survivor(group)
        losers = [row for row in group if str(row.get("id")) != str(survivor.get("id"))]
        merged = _merge_fields(survivor, losers, fields)
        report.append(
            {
                "project_id": project_id,
                "domain": domain,
                "before": len(group),
                "survivor_id": survivor.get("id"),
                "loser_ids": [row.get("id") for row in losers],
            }
        )
        if not apply:
            continue
        for loser in losers:
            connection.execute(
                text(
                    f"""
                    INSERT INTO {backup} (
                        backup_id, original_id, project_id, user_id, run_id, domain,
                        store_name, public_email,
                        {"country, freshness_level, freshness_score, source," if table == "leads" else "reason,"}
                        created_at, backed_up_at, survivor_id, row_json
                    ) VALUES (
                        :backup_id, :original_id, :project_id, :user_id, :run_id, :domain,
                        :store_name, :public_email,
                        {":country, :freshness_level, :freshness_score, :source," if table == "leads" else ":reason,"}
                        :created_at, :backed_up_at, :survivor_id, :row_json
                    )
                    """
                ),
                {
                    "backup_id": str(uuid.uuid4()),
                    "original_id": str(loser.get("id")),
                    "project_id": str(loser.get("project_id")),
                    "user_id": str(loser.get("user_id")),
                    "run_id": loser.get("run_id"),
                    "domain": str(loser.get("domain")),
                    "store_name": loser.get("store_name"),
                    "public_email": loser.get("public_email"),
                    "country": loser.get("country"),
                    "freshness_level": loser.get("freshness_level"),
                    "freshness_score": loser.get("freshness_score"),
                    "source": loser.get("source"),
                    "reason": loser.get("reason"),
                    "created_at": loser.get("created_at"),
                    "backed_up_at": now,
                    "survivor_id": str(survivor.get("id")),
                    "row_json": json.dumps(loser, default=str),
                },
            )
            connection.execute(
                text(f"DELETE FROM {table} WHERE id = :id"),
                {"id": str(loser.get("id"))},
            )
            moved += 1
        # Apply sparse-field merge onto survivor
        sets = ", ".join(f"{field} = :{field}" for field in fields if field in merged)
        if sets:
            params = {field: merged.get(field) for field in fields}
            params["id"] = str(survivor.get("id"))
            connection.execute(text(f"UPDATE {table} SET {sets} WHERE id = :id"), params)
    after_rows = _table_rows(connection, table) if apply else rows
    after = len(after_rows) if apply else before - sum(len(g) - 1 for g in groups.values())
    return {
        "table": table,
        "before": before,
        "after": after,
        "duplicate_pairs": len(groups),
        "moved_to_backup": moved if apply else sum(len(g) - 1 for g in groups.values()),
        "report": report,
    }


def main() -> None:
    apply = "--apply" in sys.argv
    url = scratch_database_url()
    engine = create_engine(url)
    print(f"{'APPLY' if apply else 'DRY-RUN'} dedupe on scratch {redacted(url)}")
    with engine.begin() as connection:
        _ensure_backup_tables(connection)
        lead_result = _dedupe_table(
            connection,
            "leads",
            "leads_dedupe_backup",
            ["store_name", "public_email", "country", "freshness_level", "freshness_score", "source", "run_id"],
            apply,
        )
        rejected_result = _dedupe_table(
            connection,
            "rejected_candidates",
            "rejected_candidates_dedupe_backup",
            ["store_name", "public_email", "reason", "run_id"],
            apply,
        )
    for result in (lead_result, rejected_result):
        print(
            f"{result['table']}: before={result['before']} after={result['after']} "
            f"dup_pairs={result['duplicate_pairs']} moved={result['moved_to_backup']}"
        )
        by_project: dict[str, dict] = defaultdict(lambda: {"pairs": 0, "extra_rows": 0})
        for item in result["report"]:
            by_project[item["project_id"]]["pairs"] += 1
            by_project[item["project_id"]]["extra_rows"] += item["before"] - 1
        print("  per-project reconciliation:")
        for project_id, stats in sorted(by_project.items()):
            print(f"    {project_id}: pairs={stats['pairs']} extra_rows={stats['extra_rows']}")
    if not apply:
        print("Re-run with --apply on scratch after reviewing the dry-run.")


if __name__ == "__main__":
    main()
