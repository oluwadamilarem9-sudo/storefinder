"""Worker account flush must keep research fields from the local lead row."""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_store_rows_includes_evidence_and_timestamps(tmp_path, monkeypatch):
    import discovery_worker
    from database.database import connect, upsert_lead, upsert_store_products

    db_path = tmp_path / "flush.db"
    connection = connect(db_path)
    try:
        upsert_lead(
            connection,
            {
                "domain": "flushshop.com",
                "store_name": "Flush",
                "shopify_status": "SHOPIFY",
                "public_email": "",
                "country": "United States",
                "discovery_source": "test",
                "source_url": "",
                "discovered_at": "2026-10-31T00:00:00+00:00",
                "freshness_score": 70,
                "freshness_level": "HIGH",
                "freshness_evidence": "Domain registered recently.",
                "theme_name": "Dawn",
                "theme_confidence": "confirmed",
                "tracking_technologies": "[]",
            },
        )
        upsert_store_products(
            connection,
            "flushshop.com",
            [{"title": "Mug", "handle": "mug", "price": "10.00", "currency": "USD"}],
        )
    finally:
        connection.close()

    captured: dict = {}

    def fake_store(project_id, user_id, run_id, leads, rejected):
        captured["leads"] = leads
        captured["rejected"] = rejected

    monkeypatch.setattr(discovery_worker.account_db, "store_run_rows", fake_store)
    discovery_worker._store_rows(
        db_path,
        {"project_id": "p1", "user_id": "u1", "id": "r1"},
        ["flushshop.com"],
        [],
    )
    assert len(captured["leads"]) == 1
    lead = captured["leads"][0]
    assert lead["freshness_evidence"] == "Domain registered recently."
    assert lead["first_seen"]
    assert lead["last_checked"]
    assert lead["theme_name"] == "Dawn"
    assert lead["products"][0]["title"] == "Mug"
