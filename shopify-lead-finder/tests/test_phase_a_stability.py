"""
Phase A regression tests: nested SQLite locks, run terminal states, session clear.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path
from threading import Lock, RLock, Thread
ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def test_plain_lock_deadlocks_on_nested_acquire():
    """Reproduce the pre-fix failure mode: nested acquire on a plain Lock hangs."""
    lock = Lock()
    hung = {"value": False}

    def nested() -> None:
        with lock:
            acquired = lock.acquire(timeout=0.3)
            hung["value"] = not acquired

    thread = Thread(target=nested, daemon=True)
    thread.start()
    thread.join(2)
    assert hung["value"] is True
    assert thread.is_alive() is False or hung["value"] is True


def test_rlock_allows_nested_acquire():
    lock = RLock()
    seen = []

    def nested() -> None:
        with lock:
            seen.append("outer")
            with lock:
                seen.append("inner")

    thread = Thread(target=nested, daemon=True)
    thread.start()
    thread.join(2)
    assert seen == ["outer", "inner"]
    assert thread.is_alive() is False


def test_upsert_lead_nested_mark_processed_does_not_hang(tmp_path):
    from database.database import connect, load_leads_frame, upsert_lead

    db_path = tmp_path / "leads.db"
    connection = connect(db_path)
    try:
        done = {"ok": False}

        def work() -> None:
            upsert_lead(
                connection,
                {
                    "domain": "example-shop.test",
                    "store_name": "Example",
                    "shopify_status": "SHOPIFY",
                    "public_email": "a@example.com",
                    "country": "US",
                    "discovery_source": "test",
                    "source_url": "https://example-shop.test",
                    "freshness_score": 80,
                    "freshness_level": "HIGH",
                    "freshness_evidence": "test",
                },
            )
            done["ok"] = True

        thread = Thread(target=work, daemon=True)
        thread.start()
        thread.join(5)
        assert thread.is_alive() is False, "upsert_lead hung (nested lock deadlock)"
        assert done["ok"] is True
        frame = load_leads_frame(connection)
        assert "example-shop.test" in set(frame["domain"].astype(str))
    finally:
        connection.close()


def test_upsert_rejected_nested_mark_processed_does_not_hang(tmp_path):
    from database.database import connect, load_rejected_frame, upsert_rejected

    db_path = tmp_path / "leads.db"
    connection = connect(db_path)
    try:
        done = {"ok": False}

        def work() -> None:
            upsert_rejected(
                connection,
                {
                    "domain": "rejected-shop.test",
                    "shopify_status": "SHOPIFY",
                    "discovery_source": "test",
                    "source_url": "",
                    "freshness_level": "LOW",
                    "freshness_score": 0,
                    "freshness_evidence": "no products",
                    "reject_reason": "no_public_products",
                },
            )
            done["ok"] = True

        thread = Thread(target=work, daemon=True)
        thread.start()
        thread.join(5)
        assert thread.is_alive() is False, "upsert_rejected hung (nested lock deadlock)"
        assert done["ok"] is True
        frame = load_rejected_frame(connection)
        assert "rejected-shop.test" in set(frame["domain"].astype(str))
    finally:
        connection.close()


def test_concurrent_database_writes_do_not_hang(tmp_path):
    from database.database import connect, upsert_lead, upsert_rejected

    db_path = tmp_path / "leads.db"
    # Prime schema once on the main thread.
    prime = connect(db_path)
    prime.close()

    errors: list[str] = []

    def writer(index: int) -> None:
        local = connect(db_path)
        try:
            if index % 2 == 0:
                upsert_lead(
                    local,
                    {
                        "domain": f"shop-{index}.test",
                        "store_name": f"Shop {index}",
                        "shopify_status": "SHOPIFY",
                        "discovery_source": "test",
                        "freshness_score": 80,
                        "freshness_level": "HIGH",
                        "freshness_evidence": "test",
                    },
                )
            else:
                upsert_rejected(
                    local,
                    {
                        "domain": f"reject-{index}.test",
                        "shopify_status": "SHOPIFY",
                        "discovery_source": "test",
                        "reject_reason": "no_public_products",
                        "freshness_level": "LOW",
                        "freshness_score": 0,
                        "freshness_evidence": "test",
                    },
                )
        except Exception as exc:
            errors.append(str(exc))
        finally:
            local.close()

    threads = [Thread(target=writer, args=(i,), daemon=True) for i in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(10)
        assert thread.is_alive() is False, "a concurrent writer hung"
    assert errors == []


def test_db_lock_is_reentrant():
    from database import database as dbmod

    assert type(dbmod._DB_LOCK).__name__ == "RLock"


def _account_db_with_temp(tmp_path, monkeypatch):
    import account_db

    db_file = tmp_path / "accounts.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    account_db.reset_account_db_engine()
    return account_db


def test_finish_run_empty_result_is_terminal_complete(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"name": "A", "email": "a@example.com", "password": "secret123"},
        None,
    )
    token = signup["token"]
    project = account_db.handle("/projects", "POST", {"name": "P"}, token)
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token,
    )
    assert run["status"] == "running"
    account_db.finish_run(
        run["id"],
        status="complete",
        summary="No live shop saved.",
        result={"saved": 0, "complete": False, "lead_domains": []},
        error="",
    )
    loaded = account_db.get_project_run(project["id"], run["id"], token)
    assert loaded["status"] == "complete"
    assert loaded["complete"] is False
    assert loaded["saved"] == 0
    assert loaded["error"] == ""
    session = account_db._session(account_db.database_url())
    try:
        row = session.get(account_db.DiscoveryRun, run["id"])
        payload = json.loads(row.result_json)
        assert payload.get("started_at")
        assert payload.get("completed_at")
        assert payload.get("status") == "complete"
        assert row.touched_at == payload["completed_at"]
    finally:
        session.close()


def test_finish_run_records_failure_not_complete(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"name": "B", "email": "b@example.com", "password": "secret123"},
        None,
    )
    token = signup["token"]
    project = account_db.handle("/projects", "POST", {"name": "P"}, token)
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True, "settings": {}},
        token,
    )
    account_db.finish_run(
        run["id"],
        status="complete",  # caller mistake with error must not stay complete
        summary="stopped",
        result={"saved": 0, "complete": False},
        error="network timeout",
    )
    loaded = account_db.get_project_run(project["id"], run["id"], token)
    assert loaded["status"] == "failed"
    assert "timeout" in (loaded["error"] or "")
    session = account_db._session(account_db.database_url())
    try:
        row = session.get(account_db.DiscoveryRun, run["id"])
        payload = json.loads(row.result_json or "{}")
        assert payload.get("status") == "failed"
        assert payload.get("completed_at")
    finally:
        session.close()


def test_run_cycle_empty_sources_finishes(tmp_path, monkeypatch):
    import pipeline

    monkeypatch.setattr(
        pipeline,
        "discover_candidates",
        lambda **kwargs: [],
    )
    monkeypatch.setattr(pipeline, "active_sources", lambda enable_secondary=None: [])

    db_path = tmp_path / "project.db"
    logs: list[str] = []
    result = pipeline.run_cycle(
        on_log=logs.append,
        max_domains=5,
        enable_secondary=False,
        target_countries=["US"],
        keep_unknown_country=False,
        db_path=db_path,
        leads_csv=tmp_path / "leads.csv",
        rejected_csv=tmp_path / "rejected.csv",
    )
    assert result["checked"] == 0
    assert result["complete"] is False
    assert result["this_run_lead_domains"] == []
    assert any("Public sources ran out" in line or "No candidates" in line for line in logs)


def test_clear_account_session_state_drops_previous_user_data():
    from session_reset import clear_account_session_state

    state = {
        "auth_token": "user-a-token",
        "backend_user": {"id": "a", "email": "a@example.com"},
        "selected_project_id": "proj-a",
        "backend_projects": [{"id": "proj-a"}],
        "backend_run_id": "run-a",
        "active_run_id": "run-a",
        "active_run_project": "proj-a",
        "this_run_lead_domains": ["secret-shop.test"],
        "this_run_rejected_domains": ["old.reject"],
        "logs": ["User A secret log"],
        "last_stats": {"checked": 99},
        "flash": "User A flash",
        "flash_kind": "success",
        "history_seed_project": "proj-a",
        "tables_project": "proj-a",
        "cached_leads": "leads-a",
        "cached_rejected": "rejected-a",
        "unrelated_ui_flag": True,
    }
    clear_account_session_state(state)

    assert state["auth_token"] is None
    assert state["backend_user"] is None
    assert state["selected_project_id"] is None
    assert state["backend_projects"] == []
    assert state["backend_run_id"] is None
    assert state["active_run_id"] is None
    assert state["active_run_project"] is None
    assert state["this_run_lead_domains"] == []
    assert state["this_run_rejected_domains"] == []
    assert "secret" not in " ".join(state["logs"]).lower()
    assert state["last_stats"] == {}
    assert state["flash"] == ""
    assert "history_seed_project" not in state
    assert "tables_project" not in state
    assert "cached_leads" not in state
    assert "cached_rejected" not in state
    assert state["unrelated_ui_flag"] is True


def test_check_workers_default_is_serial_for_phase_a():
    import importlib

    import config

    if not os.environ.get("CHECK_WORKERS"):
        assert config.CHECK_WORKERS == 1
    else:
        assert config.CHECK_WORKERS == int(os.environ["CHECK_WORKERS"])