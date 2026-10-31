"""Local concurrency check for the account-DB session cap (API + worker style)."""

from __future__ import annotations

import sys
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _setup(tmp_path, monkeypatch, *, max_sessions: str | None = None):
    import account_db

    db_file = tmp_path / "concurrency.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    if max_sessions is None:
        monkeypatch.delenv("ACCOUNT_DB_MAX_SESSIONS", raising=False)
    else:
        monkeypatch.setenv("ACCOUNT_DB_MAX_SESSIONS", max_sessions)
    account_db.reset_account_db_engine()
    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"email": "conc@example.com", "password": "secret123", "name": "C"},
        None,
    )
    token = signup["token"]
    project = account_db.handle("/projects", "POST", {"name": "P"}, token)
    return account_db, token, project["id"], signup["user"]["id"]


def test_default_session_cap_is_four_for_local_sqlite(tmp_path, monkeypatch):
    account_db, token, _project_id, _user_id = _setup(tmp_path, monkeypatch)
    assert account_db._max_db_sessions() == 4


def test_supabase_url_keeps_two_session_cap(tmp_path, monkeypatch):
    import account_db

    monkeypatch.setenv(
        "DATABASE_URL",
        "postgresql+psycopg2://u:p@db.project.supabase.co:5432/postgres",
    )
    monkeypatch.delenv("ACCOUNT_DB_MAX_SESSIONS", raising=False)
    account_db.reset_account_db_engine()
    assert account_db._max_db_sessions() == 2


def test_parallel_api_style_reads_succeed_under_default_cap(tmp_path, monkeypatch):
    account_db, token, project_id, user_id = _setup(tmp_path, monkeypatch)
    run = account_db.handle(
        f"/projects/{project_id}/runs",
        "POST",
        {"name": "r", "max_domains": 1, "on_page": False, "settings": {}},
        token,
    )
    account_db.flush_durable_batch(
        project_id,
        user_id,
        run["id"],
        [
            {
                "domain": "conc-store.com",
                "store_name": "Conc",
                "freshness_level": "HIGH",
                "freshness_score": 70,
                "freshness_evidence": "Public age proof.",
                "products": [{"title": "Item", "handle": "item"}],
            }
        ],
        [],
        [],
    )

    errors: list[str] = []
    lock = threading.Lock()

    def one_round(_i: int) -> None:
        try:
            # Mimic Next.js project page + a worker-style flush probe overlapping.
            account_db.handle(f"/projects/{project_id}/leads", "GET", None, token)
            account_db.handle(f"/projects/{project_id}/products", "GET", None, token)
            account_db.handle(f"/projects/{project_id}/runs", "GET", None, token)
            account_db.assert_user_exists(user_id)
        except Exception as exc:  # noqa: BLE001 - collect for assertion
            with lock:
                errors.append(str(exc))

    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = [pool.submit(one_round, i) for i in range(24)]
        for future in as_completed(futures):
            future.result()

    assert errors == [], errors


def test_two_cap_busy_when_slots_held(tmp_path, monkeypatch):
    """A hard cap of 2 is too tight once two sessions are already open."""
    account_db, token, project_id, user_id = _setup(tmp_path, monkeypatch, max_sessions="2")
    monkeypatch.setenv("ACCOUNT_DB_SESSION_WAIT_SECONDS", "0.2")
    assert account_db._max_db_sessions() == 2

    first = account_db._session(account_db.database_url())
    second = account_db._session(account_db.database_url())
    try:
        try:
            account_db._session(account_db.database_url())
            raised = False
        except RuntimeError as exc:
            raised = True
            assert "busy" in str(exc).lower()
        assert raised
    finally:
        first.close()
        second.close()
    account_db.handle(f"/projects/{project_id}/leads", "GET", None, token)
    account_db.assert_user_exists(user_id)
