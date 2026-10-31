"""
Side-by-side store comparison from already-saved public fields.

Does not invent sales, traffic, revenue, prices, or categories.
Cross-store product matches use handle, then normalized title — never
shopify_product_id (that id is per-shop, not global).
"""

from __future__ import annotations

from typing import Any


def _norm_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip().lower()


def _product_keys(product: dict) -> list[tuple[str, str]]:
    """
    Reliable match keys for one product, strongest first.
    shopify_product_id is omitted for cross-store matching.
    """
    keys: list[tuple[str, str]] = []
    handle = _norm_text(product.get("handle"))
    if handle:
        keys.append(("handle", handle))
    title = _norm_text(product.get("title"))
    if title and len(title) >= 2:
        keys.append(("title", title))
    return keys


def compare_product_catalogs(
    products_a: list[dict],
    products_b: list[dict],
) -> dict[str, Any]:
    """
    Split catalogs into shared matches and store-only rows.

    A match requires the same non-empty handle, or (if no handle match)
    the same normalized title. Incomplete products without handle or title
    are listed as unmatched for their store.
    """
    left = [dict(item) for item in products_a or [] if isinstance(item, dict)]
    right = [dict(item) for item in products_b or [] if isinstance(item, dict)]

    used_right: set[int] = set()
    shared: list[dict] = []
    only_a: list[dict] = []

    # Prefer handle matches over title matches.
    for confidence in ("handle", "title"):
        index_b: dict[str, list[int]] = {}
        for idx, item in enumerate(right):
            if idx in used_right:
                continue
            for kind, key in _product_keys(item):
                if kind == confidence:
                    index_b.setdefault(key, []).append(idx)
        for item in left:
            if item.get("_matched"):
                continue
            match_idx = None
            match_key = ""
            for kind, key in _product_keys(item):
                if kind != confidence:
                    continue
                candidates = index_b.get(key) or []
                while candidates and candidates[0] in used_right:
                    candidates.pop(0)
                if candidates:
                    match_idx = candidates.pop(0)
                    match_key = key
                    break
            if match_idx is None:
                continue
            used_right.add(match_idx)
            item["_matched"] = True
            other = right[match_idx]
            shared.append(
                {
                    "match_by": confidence,
                    "match_key": match_key,
                    "a": _public_product(item),
                    "b": _public_product(other),
                }
            )

    for item in left:
        if item.get("_matched"):
            continue
        only_a.append(_public_product(item))
    only_b = [_public_product(right[i]) for i in range(len(right)) if i not in used_right]

    return {
        "shared": shared,
        "only_a": only_a,
        "only_b": only_b,
        "shared_count": len(shared),
        "only_a_count": len(only_a),
        "only_b_count": len(only_b),
    }


def _public_product(item: dict) -> dict:
    return {
        "title": str(item.get("title") or ""),
        "handle": str(item.get("handle") or ""),
        "product_url": str(item.get("product_url") or ""),
        "price": str(item.get("price") or ""),
        "currency": str(item.get("currency") or ""),
        "image_url": str(item.get("image_url") or ""),
        "category": str(item.get("category") or ""),
        "shopify_product_id": str(item.get("shopify_product_id") or ""),
        "domain": str(item.get("domain") or ""),
    }


def _tracking_names(raw: Any) -> list[str]:
    names: list[str] = []
    if isinstance(raw, list):
        for item in raw:
            if isinstance(item, dict) and item.get("name"):
                names.append(str(item["name"]))
            elif isinstance(item, str) and item.strip():
                names.append(item.strip())
    return names


def compare_stores(
    store_a: dict | None,
    store_b: dict | None,
    products_a: list[dict] | None = None,
    products_b: list[dict] | None = None,
) -> dict[str, Any]:
    """Compare two saved stores using public lead + catalog fields only."""
    a = dict(store_a or {})
    b = dict(store_b or {})
    catalog = compare_product_catalogs(products_a or [], products_b or [])
    track_a = set(_tracking_names(a.get("tracking_technologies")))
    track_b = set(_tracking_names(b.get("tracking_technologies")))
    return {
        "a": {
            "domain": a.get("domain") or "",
            "store_name": a.get("store_name") or "",
            "country": a.get("country") or "",
            "freshness_level": a.get("freshness_level") or "",
            "freshness_score": a.get("freshness_score"),
            "freshness_evidence": a.get("freshness_evidence") or "",
            "theme_name": a.get("theme_name") or "",
            "theme_confidence": a.get("theme_confidence") or "",
            "tracking_technologies": a.get("tracking_technologies") or [],
            "first_seen": a.get("first_seen") or "",
            "last_checked": a.get("last_checked") or "",
            "product_count": len(products_a or []),
        },
        "b": {
            "domain": b.get("domain") or "",
            "store_name": b.get("store_name") or "",
            "country": b.get("country") or "",
            "freshness_level": b.get("freshness_level") or "",
            "freshness_score": b.get("freshness_score"),
            "freshness_evidence": b.get("freshness_evidence") or "",
            "theme_name": b.get("theme_name") or "",
            "theme_confidence": b.get("theme_confidence") or "",
            "tracking_technologies": b.get("tracking_technologies") or [],
            "first_seen": b.get("first_seen") or "",
            "last_checked": b.get("last_checked") or "",
            "product_count": len(products_b or []),
        },
        "fields": {
            "country_same": _norm_text(a.get("country")) == _norm_text(b.get("country"))
            and bool(_norm_text(a.get("country"))),
            "theme_same": _norm_text(a.get("theme_name")) == _norm_text(b.get("theme_name"))
            and bool(_norm_text(a.get("theme_name"))),
            "freshness_level_same": _norm_text(a.get("freshness_level"))
            == _norm_text(b.get("freshness_level"))
            and bool(_norm_text(a.get("freshness_level"))),
            "tracking_shared": sorted(track_a & track_b),
            "tracking_only_a": sorted(track_a - track_b),
            "tracking_only_b": sorted(track_b - track_a),
        },
        "products": catalog,
    }
