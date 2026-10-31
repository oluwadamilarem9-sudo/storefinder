"""Public Shopify catalog product extraction and persistence."""

from __future__ import annotations

import json
import sys
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def test_fetch_catalog_products_parses_public_fields(monkeypatch):
    from detection.storefront_profile import fetch_catalog_products

    payload = {
        "products": [
            {
                "id": 101,
                "title": "Blue Mug",
                "handle": "blue-mug",
                "product_type": "Drinkware",
                "variants": [{"price": "12.50"}],
                "image": {"src": "https://cdn.example/mug.jpg"},
            },
            {
                "id": 102,
                "title": "Untitled Draft",
                "handle": "",
                "product_type": "",
                "variants": [],
                "images": [],
            },
            {
                "id": 103,
                "title": "  ",
                "handle": "blank",
                "variants": [{"price": "9.00"}],
            },
        ]
    }

    monkeypatch.setattr(
        "detection.storefront_profile.fetch_public",
        lambda *_a, **_k: SimpleNamespace(ok=True, text=json.dumps(payload)),
    )
    monkeypatch.setattr(
        "detection.storefront_profile.website_url",
        lambda domain: f"https://{domain}",
    )

    rows = fetch_catalog_products("mugshop.example", currency="USD")
    assert len(rows) == 2
    first = rows[0]
    assert first.title == "Blue Mug"
    assert first.price == "12.50"
    assert first.currency == "USD"
    assert first.product_url == "https://mugshop.example/products/blue-mug"
    assert first.image_url == "https://cdn.example/mug.jpg"
    assert first.category == "Drinkware"
    assert first.shopify_product_id == "101"
    second = rows[1]
    assert second.title == "Untitled Draft"
    assert second.price == ""
    assert second.currency == ""
    assert second.product_url == ""
    assert second.category == ""


def test_fetch_catalog_products_empty_on_bad_payload(monkeypatch):
    from detection.storefront_profile import fetch_catalog_products

    monkeypatch.setattr(
        "detection.storefront_profile.fetch_public",
        lambda *_a, **_k: SimpleNamespace(ok=True, text="not-json"),
    )
    assert fetch_catalog_products("broken.example") == []

    monkeypatch.setattr(
        "detection.storefront_profile.fetch_public",
        lambda *_a, **_k: SimpleNamespace(ok=False, text=""),
    )
    assert fetch_catalog_products("down.example") == []


def test_pipeline_persists_products_linked_to_store(monkeypatch, tmp_path):
    import pipeline
    from database.database import connect, load_store_products
    from detection.storefront_profile import CatalogProduct
    from discovery.freshness import NewStoreResult

    class Profile:
        password_page = False
        product_names = ["Blue Mug"]
        products = [
            CatalogProduct(
                title="Blue Mug",
                handle="blue-mug",
                product_url="https://productmugshop.com/products/blue-mug",
                price="12.50",
                currency="USD",
                image_url="https://cdn.shopify.com/mug.jpg",
                category="Drinkware",
                shopify_product_id="101",
            )
        ]
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
        final_url="https://productmugshop.com/",
    )
    monkeypatch.setattr(pipeline, "_fetch_storefront", lambda _d: homepage)
    monkeypatch.setattr(pipeline, "detect_shopify", lambda *_a, **_k: "SHOPIFY")
    monkeypatch.setattr(pipeline, "published_custom_domain", lambda domain, _html: domain)
    monkeypatch.setattr(pipeline, "is_password_page", lambda *_a, **_k: False)
    monkeypatch.setattr(pipeline, "inspect_storefront", lambda *_a, **_k: Profile())
    monkeypatch.setattr(
        pipeline,
        "find_public_contacts",
        lambda *_a, **_k: {"country": "", "public_email": "", "store_name": "Mugs"},
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
                domain="productmugshop.com",
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
        products = load_store_products(connection, ["productmugshop.com"])
        assert len(products) == 1
        row = products[0]
        assert row["domain"] == "productmugshop.com"
        assert row["title"] == "Blue Mug"
        assert row["price"] == "12.50"
        assert row["currency"] == "USD"
        assert row["category"] == "Drinkware"
        assert row["product_url"] == "https://productmugshop.com/products/blue-mug"
        assert row["image_url"] == "https://cdn.shopify.com/mug.jpg"
    finally:
        connection.close()


def test_account_products_endpoint_and_flush(tmp_path, monkeypatch):
    import account_db

    db_file = tmp_path / "account.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    account_db.reset_account_db_engine()

    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"email": "products@example.com", "password": "secret123", "name": "Prod"},
        None,
    )
    token = signup["token"]
    project = account_db.handle("/projects", "POST", {"name": "Product project"}, token)
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "run", "max_domains": 1, "on_page": False, "settings": {}},
        token,
    )

    account_db.flush_durable_batch(
        project["id"],
        signup["user"]["id"],
        run["id"],
        [
            {
                "domain": "mugshop.example",
                "store_name": "Mugs",
                "source": "test",
                "products": [
                    {
                        "title": "Blue Mug",
                        "handle": "blue-mug",
                        "product_url": "https://mugshop.example/products/blue-mug",
                        "price": "12.50",
                        "currency": "USD",
                        "image_url": "https://cdn.example/mug.jpg",
                        "category": "Drinkware",
                        "shopify_product_id": "101",
                    },
                    {
                        "title": "Title Only",
                        "price": "",
                        "currency": "",
                        "category": "",
                        "product_url": "",
                    },
                ],
            }
        ],
        [],
        [],
    )

    products = account_db.handle(f"/projects/{project['id']}/products", "GET", None, token)
    assert len(products) == 2
    by_title = {row["title"]: row for row in products}
    assert by_title["Blue Mug"]["domain"] == "mugshop.example"
    assert by_title["Blue Mug"]["price"] == "12.50"
    assert by_title["Blue Mug"]["category"] == "Drinkware"
    assert by_title["Title Only"]["price"] == ""
    assert by_title["Title Only"]["product_url"] == ""
