"""Theme and tracking-technology detection from public HTML."""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from detection.storefront_profile import (
    inspect_storefront,
    parse_tracking,
    serialize_tracking,
)


def test_theme_confirmed_from_shopify_theme_object(monkeypatch):
    monkeypatch.setattr(
        "detection.storefront_profile._products_from_catalog",
        lambda _domain: ["Widget"],
    )
    html = """
    <html><script>
    Shopify.theme = {"name":"Dawn","id":123};
    </script></html>
    """
    profile = inspect_storefront("example.com", html)
    assert profile.theme_name == "Dawn"
    assert profile.theme_confidence == "confirmed"


def test_theme_undetected_is_empty_not_invented(monkeypatch):
    monkeypatch.setattr(
        "detection.storefront_profile._products_from_catalog",
        lambda _domain: ["Widget"],
    )
    profile = inspect_storefront("example.com", "<html><body>hello</body></html>")
    assert profile.theme_name == ""
    assert profile.theme_confidence == ""


def test_tracking_confirmed_and_uncertain(monkeypatch):
    monkeypatch.setattr(
        "detection.storefront_profile._products_from_catalog",
        lambda _domain: ["Widget"],
    )
    html = """
    <script src="https://connect.facebook.net/en_US/fbevents.js"></script>
    <script>fbq('init','123');</script>
    <script src="https://www.googletagmanager.com/gtm.js?id=GTM-ABC"></script>
    <script src="https://analytics.tiktok.com/i18n/pixel/events.js"></script>
    <script>ttq.load('x');</script>
    """
    profile = inspect_storefront("example.com", html)
    by_name = {item.name: item.confidence for item in profile.tracking}
    assert by_name["Meta Pixel"] == "confirmed"
    assert by_name["Google Tag Manager"] == "confirmed"
    assert by_name["TikTok Pixel"] == "confirmed"
    assert "Google Analytics" not in by_name


def test_google_analytics_distinct_from_gtm(monkeypatch):
    monkeypatch.setattr(
        "detection.storefront_profile._products_from_catalog",
        lambda _domain: ["Widget"],
    )
    html = """
    <script src="https://www.googletagmanager.com/gtag/js?id=G-ABC"></script>
    <script>gtag('config','G-ABC');</script>
    """
    profile = inspect_storefront("example.com", html)
    by_name = {item.name: item.confidence for item in profile.tracking}
    assert by_name["Google Analytics"] == "confirmed"
    assert "Google Tag Manager" not in by_name


def test_uncertain_meta_without_strong_pixel(monkeypatch):
    monkeypatch.setattr(
        "detection.storefront_profile._products_from_catalog",
        lambda _domain: ["Widget"],
    )
    html = '<img src="https://www.facebook.com/tr?id=1&ev=PageView" />'
    profile = inspect_storefront("example.com", html)
    by_name = {item.name: item.confidence for item in profile.tracking}
    assert by_name["Meta Pixel"] == "uncertain"


def test_serialize_and_parse_tracking_roundtrip():
    from detection.storefront_profile import TechDetection

    raw = serialize_tracking(
        [
            TechDetection("Meta Pixel", "confirmed"),
            TechDetection("TikTok Pixel", "uncertain"),
        ]
    )
    parsed = parse_tracking(raw)
    assert parsed == [
        {"name": "Meta Pixel", "confidence": "confirmed"},
        {"name": "TikTok Pixel", "confidence": "uncertain"},
    ]
    assert parse_tracking("") == []
    assert parse_tracking(None) == []


def test_pipeline_persists_theme_and_tracking_fields(monkeypatch, tmp_path):
    import pipeline
    from database.database import connect, load_leads_frame
    from detection.storefront_profile import TechDetection
    from discovery.freshness import NewStoreResult
    from types import SimpleNamespace

    class Profile:
        password_page = False
        product_names = ["Widget"]
        theme_name = "Refresh"
        theme_confidence = "confirmed"
        tracking = [
            TechDetection("Meta Pixel", "confirmed"),
            TechDetection("Google Analytics", "uncertain"),
        ]

        def evidence(self) -> str:
            return "Public catalog lists 1 product(s)."

    homepage = SimpleNamespace(
        ok=True,
        text="<html></html>",
        error="",
        headers={"x-shopid": "1"},
        final_url="https://acmewidgetsstore.com/",
    )
    monkeypatch.setattr(pipeline, "_fetch_storefront", lambda _d: homepage)
    monkeypatch.setattr(pipeline, "detect_shopify", lambda *_a, **_k: "SHOPIFY")
    monkeypatch.setattr(pipeline, "published_custom_domain", lambda domain, _html: domain)
    monkeypatch.setattr(pipeline, "is_password_page", lambda *_a, **_k: False)
    monkeypatch.setattr(pipeline, "inspect_storefront", lambda *_a, **_k: Profile())
    monkeypatch.setattr(
        pipeline,
        "find_public_contacts",
        lambda *_a, **_k: {"country": "", "public_email": "", "store_name": "Tech"},
    )
    monkeypatch.setattr(
        pipeline,
        "prove_new_store",
        lambda *_a, **_k: NewStoreResult(True, "HIGH", 80, "Domain registered recently.", 5),
    )

    db_path = tmp_path / "leads.db"
    connection = connect(db_path)
    try:
        stats = {
            "shopify": 0,
            "high": 0,
            "low": 0,
            "emails": 0,
            "this_run_lead_domains": [],
            "this_run_rejected_domains": [],
        }
        pipeline._process_candidate(
            connection=connection,
            candidate=SimpleNamespace(
                domain="acmewidgetsstore.com",
                source="test",
                source_url="",
                discovered_at="2026-10-31T00:00:00+00:00",
            ),
            index=1,
            total=1,
            stats=stats,
            log=lambda _m: None,
            countries=[],
            keep_unknown=True,
            store_age_filter="all",
        )
        frame = load_leads_frame(connection)
        assert len(frame) == 1
        row = frame.iloc[0]
        assert row["theme_name"] == "Refresh"
        assert row["theme_confidence"] == "confirmed"
        tracking = json.loads(row["tracking_technologies"])
        assert tracking[0]["name"] == "Meta Pixel"
        assert tracking[0]["confidence"] == "confirmed"
    finally:
        connection.close()
