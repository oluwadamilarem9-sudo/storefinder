"""New vs established store-age classification and pipeline filter."""

from __future__ import annotations

import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from discovery.freshness import (
    NewStoreResult,
    age_filter_allows,
    classify_store_age,
    judge_new_store,
    normalize_store_age_filter,
)


def test_normalize_store_age_filter():
    assert normalize_store_age_filter(None) == "all"
    assert normalize_store_age_filter("NEW") == "new"
    assert normalize_store_age_filter("nope") == "all"


def test_classify_new_established_unknown():
    assert classify_store_age(NewStoreResult(True, "HIGH", 80, "new proof", 10)) == "new"
    assert classify_store_age(NewStoreResult(False, "LOW", 0, "old domain")) == "established"
    assert classify_store_age(NewStoreResult(False, "UNKNOWN", 0, "no proof")) == "unknown"


def test_age_filter_allows():
    assert age_filter_allows("new", "all") is True
    assert age_filter_allows("new", "new") is True
    assert age_filter_allows("established", "new") is False
    assert age_filter_allows("unknown", "unknown") is True
    assert age_filter_allows("unknown", "established") is False


def test_judge_new_store_does_not_invent_dates():
    now = datetime(2026, 10, 31, tzinfo=timezone.utc)
    unknown = judge_new_store(
        rdap_created=None,
        certificate_dates=[],
        certificate_history_complete=False,
        earliest_archive=None,
        archive_lookup_ok=True,
        homepage_html="<html></html>",
        now=now,
    )
    assert unknown.is_new is False
    assert unknown.freshness_level == "UNKNOWN"
    assert "Found today is not launched today" in unknown.evidence

    old = judge_new_store(
        rdap_created=now - timedelta(days=400),
        certificate_dates=[],
        certificate_history_complete=False,
        earliest_archive=None,
        archive_lookup_ok=True,
        homepage_html="<html></html>",
        now=now,
    )
    assert old.is_new is False
    assert classify_store_age(old) == "established"

    recent = judge_new_store(
        rdap_created=now - timedelta(days=12),
        certificate_dates=[now - timedelta(days=10)],
        certificate_history_complete=True,
        earliest_archive=None,
        archive_lookup_ok=True,
        homepage_html="<html></html>",
        now=now,
    )
    assert recent.is_new is True
    assert classify_store_age(recent) == "new"


def test_pipeline_rejects_when_age_filter_mismatches(monkeypatch):
    import pipeline
    from discovery.freshness import NewStoreResult

    rejected: list[dict] = []

    class Profile:
        password_page = False
        product_names = ["Widget"]
        theme_name = ""
        theme_confidence = ""
        tracking = []

        def evidence(self) -> str:
            return "Public catalog lists 1 product(s)."

    homepage = SimpleNamespace(
        ok=True,
        text="<html></html>",
        error="",
        headers={"x-shopid": "1"},
        final_url="https://age-filter-shop.com/",
    )

    monkeypatch.setattr(pipeline, "_fetch_storefront", lambda _d: homepage)
    monkeypatch.setattr(pipeline, "detect_shopify", lambda *_a, **_k: "SHOPIFY")
    monkeypatch.setattr(pipeline, "published_custom_domain", lambda domain, _html: domain)
    monkeypatch.setattr(pipeline, "is_password_page", lambda *_a, **_k: False)
    monkeypatch.setattr(pipeline, "inspect_storefront", lambda *_a, **_k: Profile())
    monkeypatch.setattr(
        pipeline,
        "find_public_contacts",
        lambda *_a, **_k: {"country": "United States", "public_email": "", "store_name": "Age"},
    )
    monkeypatch.setattr(
        pipeline,
        "prove_new_store",
        lambda *_a, **_k: NewStoreResult(False, "UNKNOWN", 0, "No public proof."),
    )
    monkeypatch.setattr(pipeline, "upsert_rejected", lambda _c, row: rejected.append(row))
    monkeypatch.setattr(pipeline, "mark_processed", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "upsert_lead", lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("should not save")))

    candidate = SimpleNamespace(
        domain="age-filter-shop.com",
        source="test",
        source_url="https://example.test",
        discovered_at="2026-10-31T00:00:00+00:00",
    )
    stats = {
        "shopify": 0,
        "high": 0,
        "low": 0,
        "emails": 0,
        "this_run_lead_domains": [],
        "this_run_rejected_domains": [],
    }
    pipeline._process_candidate(
        connection=None,
        candidate=candidate,
        index=1,
        total=1,
        stats=stats,
        log=lambda _m: None,
        countries=[],
        keep_unknown=True,
        store_age_filter="new",
    )
    assert rejected
    assert rejected[0]["reject_reason"] == "store_age_filter"
    assert "First discovered by this tool" in rejected[0]["freshness_evidence"]
    assert stats["this_run_lead_domains"] == []


def test_pipeline_saves_with_real_freshness_not_hardcoded(monkeypatch):
    import pipeline
    from discovery.freshness import NewStoreResult

    saved: list[dict] = []

    class Profile:
        password_page = False
        product_names = ["Widget"]
        theme_name = ""
        theme_confidence = ""
        tracking = []

        def evidence(self) -> str:
            return "Public catalog lists 1 product(s)."

    homepage = SimpleNamespace(
        ok=True,
        text="<html></html>",
        error="",
        headers={"x-shopid": "1"},
        final_url="https://new-proof-shop.com/",
    )

    monkeypatch.setattr(pipeline, "_fetch_storefront", lambda _d: homepage)
    monkeypatch.setattr(pipeline, "detect_shopify", lambda *_a, **_k: "SHOPIFY")
    monkeypatch.setattr(pipeline, "published_custom_domain", lambda domain, _html: domain)
    monkeypatch.setattr(pipeline, "is_password_page", lambda *_a, **_k: False)
    monkeypatch.setattr(pipeline, "inspect_storefront", lambda *_a, **_k: Profile())
    monkeypatch.setattr(
        pipeline,
        "find_public_contacts",
        lambda *_a, **_k: {"country": "", "public_email": "a@b.com", "store_name": "New"},
    )
    monkeypatch.setattr(
        pipeline,
        "prove_new_store",
        lambda *_a, **_k: NewStoreResult(True, "HIGH", 80, "Domain registered recently.", 8),
    )
    monkeypatch.setattr(pipeline, "upsert_lead", lambda _c, row: saved.append(row))
    monkeypatch.setattr(pipeline, "clear_password_watch", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "mark_processed", lambda *_a, **_k: None)

    candidate = SimpleNamespace(
        domain="new-proof-shop.com",
        source="test",
        source_url="https://example.test",
        discovered_at="2026-10-31T12:00:00+00:00",
    )
    stats = {
        "shopify": 0,
        "high": 0,
        "low": 0,
        "emails": 0,
        "this_run_lead_domains": [],
        "this_run_rejected_domains": [],
    }
    pipeline._process_candidate(
        connection=None,
        candidate=candidate,
        index=1,
        total=1,
        stats=stats,
        log=lambda _m: None,
        countries=[],
        keep_unknown=True,
        store_age_filter="all",
    )
    assert len(saved) == 1
    assert saved[0]["freshness_level"] == "HIGH"
    assert saved[0]["freshness_score"] == 80
    assert "First discovered by this tool at 2026-10-31T12:00:00+00:00" in saved[0]["freshness_evidence"]
    assert "Domain registered recently" in saved[0]["freshness_evidence"]
