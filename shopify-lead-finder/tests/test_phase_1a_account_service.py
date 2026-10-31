"""
Phase 1A: shared account service extraction stays behavior-compatible.

Uses temporary SQLite only. Never touches production databases.
"""

from __future__ import annotations

import importlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


PUBLIC_API = (
    "Base",
    "User",
    "Project",
    "DiscoveryRun",
    "Lead",
    "RejectedCandidate",
    "ProcessedDomain",
    "database_url",
    "using_database",
    "handle",
    "reset_account_db_engine",
    "get_project_run",
    "latest_open_run",
    "touch_run",
    "claim_next_run",
    "save_run_log",
    "finish_run",
    "project_history",
    "store_run_rows",
    "flush_durable_batch",
    "_ACCOUNT_DB_GENERATION",
)


def test_account_db_shim_exposes_same_public_api_as_service():
    import account_db
    from services import account_service

    for name in PUBLIC_API:
        assert hasattr(account_db, name), f"account_db missing {name}"
        assert hasattr(account_service, name), f"account_service missing {name}"
        assert getattr(account_db, name) is getattr(account_service, name), name


def test_mutable_engine_state_is_shared_through_shim(tmp_path, monkeypatch):
    import account_db
    from services import account_service

    db_file = tmp_path / "phase1a.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    account_db.reset_account_db_engine()

    session = account_db._session(account_db.database_url())
    try:
        assert account_service._engine is not None
        assert account_db._engine is account_service._engine
    finally:
        session.close()

    account_db.reset_account_db_engine()
    assert account_service._engine is None
    assert account_db._engine is None


def test_handle_signup_project_via_shim_matches_service_path(tmp_path, monkeypatch):
    import account_db

    db_file = tmp_path / "phase1a_flow.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    account_db.reset_account_db_engine()

    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"email": "phase1a@example.com", "password": "secret-pass", "name": "P1A"},
        None,
    )
    token = signup["token"]
    assert token == signup["user"]["id"]

    project = account_db.handle("/projects", "POST", {"name": "Demo"}, token)
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token,
    )
    account_db.flush_durable_batch(
        project["id"],
        signup["user"]["id"],
        run["id"],
        [{"domain": "phase1a.example", "store_name": "Demo Store"}],
        [],
        [{"domain": "phase1a.example"}],
    )
    leads = account_db.handle(f"/projects/{project['id']}/leads", "GET", None, token)
    assert len(leads) == 1
    assert leads[0]["domain"] == "phase1a.example"


def test_generation_constant_still_eight():
    import account_db
    from services import account_service

    assert account_db._ACCOUNT_DB_GENERATION == 8
    assert account_service._ACCOUNT_DB_GENERATION == 8


def test_reload_account_db_shim_keeps_service_importable():
    import account_db

    reloaded = importlib.reload(account_db)
    assert reloaded._ACCOUNT_DB_GENERATION == 8
    assert callable(reloaded.handle)
    from services import account_service

    assert reloaded.handle is account_service.handle
