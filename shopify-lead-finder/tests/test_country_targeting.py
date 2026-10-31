"""Published-country targeting: multi-select and keep-unknown behavior."""

from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from utils.countries import country_allows, normalize_country


def test_normalize_common_aliases():
    assert normalize_country("United States") == "US"
    assert normalize_country("UK") == "GB"
    assert normalize_country("nigeria") == "NG"


def test_multi_country_allows_any_selected():
    allowed, reason = country_allows("Canada", ["US", "CA", "GB"], False)
    assert allowed is True
    assert reason == ""


def test_multi_country_rejects_outside_list():
    allowed, reason = country_allows("Germany", ["US", "CA", "GB"], False)
    assert allowed is False
    assert reason == "country_mismatch"


def test_unknown_rejected_when_keep_unknown_false():
    allowed, reason = country_allows("", ["US"], False)
    assert allowed is False
    assert reason == "country_unknown"


def test_unknown_kept_when_keep_unknown_true():
    allowed, reason = country_allows("", ["US", "GB"], True)
    assert allowed is True
    assert reason == ""


def test_empty_target_list_allows_any_published():
    allowed, reason = country_allows("France", [], False)
    assert allowed is True
    assert reason == ""


def test_process_candidate_forwards_keep_unknown(monkeypatch):
    """Regression: keep_unknown must reach country_allows (was hardcoded False)."""
    import pipeline

    seen: dict = {}

    def fake_allows(published, target_codes, keep_unknown):
        seen["keep_unknown"] = keep_unknown
        seen["codes"] = list(target_codes)
        return False, "country_unknown"

    class Profile:
        password_page = False
        product_names = ["Widget"]

        def evidence(self) -> str:
            return "Public catalog lists 1 product(s)."

    homepage = SimpleNamespace(
        ok=True,
        text="<html></html>",
        error="",
        headers={"x-shopid": "1"},
        final_url="https://acme-widgets.com/",
    )

    monkeypatch.setattr(pipeline, "country_allows", fake_allows)
    monkeypatch.setattr(pipeline, "_fetch_storefront", lambda _domain: homepage)
    monkeypatch.setattr(pipeline, "detect_shopify", lambda *_a, **_k: "SHOPIFY")
    monkeypatch.setattr(pipeline, "published_custom_domain", lambda domain, _html: domain)
    monkeypatch.setattr(pipeline, "is_password_page", lambda *_a, **_k: False)
    monkeypatch.setattr(pipeline, "inspect_storefront", lambda *_a, **_k: Profile())
    monkeypatch.setattr(
        pipeline,
        "find_public_contacts",
        lambda *_a, **_k: {"country": "", "public_email": "", "store_name": "Acme"},
    )
    monkeypatch.setattr(pipeline, "upsert_rejected", lambda *_a, **_k: None)
    monkeypatch.setattr(pipeline, "mark_processed", lambda *_a, **_k: None)

    candidate = SimpleNamespace(
        domain="acme-widgets.com",
        source="test",
        source_url="https://example.test",
        discovered_at="",
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
        countries=["US", "GB"],
        keep_unknown=True,
    )
    assert seen.get("keep_unknown") is True
    assert seen.get("codes") == ["US", "GB"]
