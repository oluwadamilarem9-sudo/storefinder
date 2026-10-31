"""Competitor compare, watchlist, and saved filter configs."""

from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_product_catalog_match_prefers_handle_then_title():
    from research.compare_stores import compare_product_catalogs

    a = [
        {
            "title": "Blue Mug",
            "handle": "blue-mug",
            "price": "12.00",
            "shopify_product_id": "111",
        },
        {"title": "Green Cup", "handle": "", "price": "", "shopify_product_id": "222"},
        {"title": "Only A", "handle": "only-a", "price": "1.00"},
    ]
    b = [
        {
            "title": "Blue Mug Deluxe",
            "handle": "blue-mug",
            "price": "14.00",
            "shopify_product_id": "999",
        },
        {"title": "Green Cup", "handle": "", "price": "9.00", "shopify_product_id": "888"},
        {"title": "Only B", "handle": "only-b"},
    ]
    result = compare_product_catalogs(a, b)
    assert result["shared_count"] == 2
    match_bys = {row["match_by"] for row in result["shared"]}
    assert match_bys == {"handle", "title"}
    handle_row = next(row for row in result["shared"] if row["match_by"] == "handle")
    assert handle_row["a"]["shopify_product_id"] == "111"
    assert handle_row["b"]["shopify_product_id"] == "999"
    assert result["only_a_count"] == 1
    assert result["only_b_count"] == 1
    assert result["only_a"][0]["title"] == "Only A"
    assert result["only_b"][0]["title"] == "Only B"


def test_compare_stores_does_not_invent_metrics():
    from research.compare_stores import compare_stores

    result = compare_stores(
        {
            "domain": "a.com",
            "country": "United States",
            "freshness_level": "HIGH",
            "freshness_score": 80,
            "freshness_evidence": "Domain registered recently.",
            "theme_name": "Dawn",
            "theme_confidence": "confirmed",
            "tracking_technologies": [{"name": "Meta Pixel", "confidence": "confirmed"}],
        },
        {
            "domain": "b.com",
            "country": "United States",
            "freshness_level": "UNKNOWN",
            "theme_name": "Dawn",
            "tracking_technologies": [
                {"name": "Meta Pixel", "confidence": "confirmed"},
                {"name": "TikTok Pixel", "confidence": "uncertain"},
            ],
        },
        [{"title": "Shared", "handle": "shared", "price": "10.00"}],
        [{"title": "Shared", "handle": "shared", "price": ""}],
    )
    assert result["fields"]["country_same"] is True
    assert result["fields"]["theme_same"] is True
    assert result["fields"]["tracking_shared"] == ["Meta Pixel"]
    assert result["fields"]["tracking_only_b"] == ["TikTok Pixel"]
    assert result["products"]["shared_count"] == 1
    assert result["products"]["shared"][0]["b"]["price"] == ""
    assert "revenue" not in result
    assert "traffic" not in result
    assert "sales" not in result


def test_watchlist_filters_and_compare_api(tmp_path, monkeypatch):
    import account_db

    db_file = tmp_path / "research.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    account_db.reset_account_db_engine()

    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"email": "research@example.com", "password": "secret123", "name": "R"},
        None,
    )
    token = signup["token"]
    user_id = signup["user"]["id"]
    project = account_db.handle("/projects", "POST", {"name": "Research"}, token)
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "run", "max_domains": 2, "on_page": False, "settings": {}},
        token,
    )

    account_db.flush_durable_batch(
        project["id"],
        user_id,
        run["id"],
        [
            {
                "domain": "alpha-store.com",
                "store_name": "Alpha",
                "country": "United States",
                "freshness_level": "HIGH",
                "freshness_score": 70,
                "freshness_evidence": "Certificate is recent.",
                "theme_name": "Dawn",
                "theme_confidence": "confirmed",
                "tracking_technologies": [{"name": "Meta Pixel", "confidence": "confirmed"}],
                "products": [
                    {
                        "title": "Blue Mug",
                        "handle": "blue-mug",
                        "product_url": "https://alpha-store.com/products/blue-mug",
                        "price": "12.00",
                        "currency": "USD",
                        "category": "Drinkware",
                    }
                ],
            },
            {
                "domain": "beta-store.com",
                "store_name": "Beta",
                "country": "Canada",
                "freshness_level": "UNKNOWN",
                "theme_name": "Refresh",
                "products": [
                    {
                        "title": "Blue Mug",
                        "handle": "blue-mug",
                        "product_url": "https://beta-store.com/products/blue-mug",
                        "price": "",
                        "currency": "",
                    },
                    {"title": "Beta Only", "handle": "beta-only"},
                ],
            },
        ],
        [],
        [],
    )

    watched = account_db.handle(
        f"/projects/{project['id']}/watchlist",
        "POST",
        {"domain": "alpha-store.com", "note": "competitor"},
        token,
    )
    assert watched["domain"] == "alpha-store.com"
    assert watched["lead"]["theme_name"] == "Dawn"
    assert watched["product_count"] == 1

    watch_rows = account_db.handle(f"/projects/{project['id']}/watchlist", "GET", None, token)
    assert len(watch_rows) == 1

    saved = account_db.handle(
        f"/projects/{project['id']}/filters",
        "POST",
        {
            "name": "US new Dawn",
            "config": {
                "store_age_filter": "new",
                "target_countries": ["US"],
                "all_countries": False,
                "theme_filter": "Dawn",
                "tracking_filter": "Meta Pixel",
                "max_domains": 25,
            },
        },
        token,
    )
    assert saved["name"] == "US new Dawn"
    assert saved["config"]["store_age_filter"] == "new"
    filters = account_db.handle(f"/projects/{project['id']}/filters", "GET", None, token)
    assert len(filters) == 1

    compared = account_db.handle(
        f"/projects/{project['id']}/compare",
        "POST",
        {"domain_a": "alpha-store.com", "domain_b": "beta-store.com"},
        token,
    )
    assert compared["a"]["domain"] == "alpha-store.com"
    assert compared["b"]["domain"] == "beta-store.com"
    assert compared["fields"]["country_same"] is False
    assert compared["products"]["shared_count"] == 1
    assert compared["products"]["shared"][0]["match_by"] == "handle"
    assert compared["products"]["only_b_count"] == 1
    assert compared["a"]["freshness_evidence"] == "Certificate is recent."
    assert "revenue" not in compared

    account_db.handle(
        f"/projects/{project['id']}/watchlist/{watched['id']}",
        "DELETE",
        None,
        token,
    )
    assert account_db.handle(f"/projects/{project['id']}/watchlist", "GET", None, token) == []

    account_db.handle(
        f"/projects/{project['id']}/filters/{saved['id']}",
        "DELETE",
        None,
        token,
    )
    assert account_db.handle(f"/projects/{project['id']}/filters", "GET", None, token) == []
