"""
Phase C safe-environment tests: durable buffer, upsert, isolation, rollback visibility.

Uses temporary SQLite account DBs only. Never touches production.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


def _account_db_with_temp(tmp_path, monkeypatch):
    import account_db

    db_file = tmp_path / "phase_c_account.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    monkeypatch.delenv("DISCOVERY_DURABLE_STORE", raising=False)
    account_db.reset_account_db_engine()
    return account_db


def _signup_project(account_db, email: str):
    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"email": email, "password": "secret-pass", "name": "T"},
        None,
    )
    token = signup["token"]
    project = account_db.handle("/projects", "POST", {"name": "P"}, token)
    return token, project, signup["user"]


def test_durable_store_mode_defaults_to_sqlite(monkeypatch):
    monkeypatch.delenv("DISCOVERY_DURABLE_STORE", raising=False)
    import importlib
    import config

    importlib.reload(config)
    assert config.durable_store_mode() == "sqlite"


def test_durable_store_mode_postgres(monkeypatch):
    monkeypatch.setenv("DISCOVERY_DURABLE_STORE", "postgres")
    import importlib
    import config

    importlib.reload(config)
    assert config.durable_store_mode() == "postgres"
    monkeypatch.delenv("DISCOVERY_DURABLE_STORE", raising=False)
    importlib.reload(config)


def test_buffer_drops_abandoned_and_batches(tmp_path, monkeypatch):
    from durable_store import DurableWriteBuffer

    flushed = []

    def flusher(project_id, user_id, run_id, leads, rejected, processed):
        flushed.append(
            {
                "leads": list(leads),
                "rejected": list(rejected),
                "processed": list(processed),
            }
        )

    abandoned = {"bad.example"}
    buf = DurableWriteBuffer(
        project_id="p1",
        user_id="u1",
        run_id="r1",
        is_abandoned=lambda d: d in abandoned,
        batch_size=3,
        flusher=flusher,
    )
    buf.record_lead({"domain": "a.example"})
    buf.record_rejected({"domain": "bad.example", "reason": "x"})
    buf.record_processed({"domain": "b.example"})
    assert flushed == []
    buf.record_lead({"domain": "c.example"})
    assert len(flushed) == 1
    domains = {row["domain"] for row in flushed[0]["leads"]} | {
        row["domain"] for row in flushed[0]["processed"]
    }
    assert "bad.example" not in domains
    assert "a.example" in domains
    assert "c.example" in domains


def test_upsert_same_domain_stays_one_row(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, user = _signup_project(account_db, "upsert@example.com")
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token,
    )
    account_db.flush_durable_batch(
        project["id"],
        user["id"],
        run["id"],
        [{"domain": "shop.example", "store_name": "A", "source": "s1"}],
        [],
        [],
    )
    account_db.flush_durable_batch(
        project["id"],
        user["id"],
        run["id"],
        [{"domain": "shop.example", "public_email": "a@b.c", "source": "s2"}],
        [],
        [],
    )
    leads = account_db.handle(f"/projects/{project['id']}/leads", "GET", None, token)
    assert len(leads) == 1
    assert leads[0]["domain"] == "shop.example"
    assert leads[0]["store_name"] == "A"
    assert leads[0]["public_email"] == "a@b.c"


def test_isolation_cross_user_and_same_domain_two_projects(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token_a, project_a, user_a = _signup_project(account_db, "a@example.com")
    token_b, project_b, user_b = _signup_project(account_db, "b@example.com")
    run_a = account_db.handle(
        f"/projects/{project_a['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token_a,
    )
    run_b = account_db.handle(
        f"/projects/{project_b['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token_b,
    )
    account_db.flush_durable_batch(
        project_a["id"],
        user_a["id"],
        run_a["id"],
        [{"domain": "shared.example", "store_name": "A"}],
        [{"domain": "rej.example", "reason": "password_page"}],
        [{"domain": "shared.example", "shopify_status": "SHOPIFY"}],
    )
    account_db.flush_durable_batch(
        project_b["id"],
        user_b["id"],
        run_b["id"],
        [{"domain": "shared.example", "store_name": "B"}],
        [],
        [],
    )
    leads_a = account_db.handle(f"/projects/{project_a['id']}/leads", "GET", None, token_a)
    leads_b = account_db.handle(f"/projects/{project_b['id']}/leads", "GET", None, token_b)
    assert len(leads_a) == 1 and leads_a[0]["store_name"] == "A"
    assert len(leads_b) == 1 and leads_b[0]["store_name"] == "B"
    try:
        account_db.handle(f"/projects/{project_a['id']}/leads", "GET", None, token_b)
        assert False, "cross-user read must fail"
    except RuntimeError as exc:
        assert "not owned" in str(exc).lower() or "not found" in str(exc).lower()


def test_password_watch_persists_across_engine_reset(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, user = _signup_project(account_db, "watch@example.com")
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token,
    )
    account_db.flush_durable_batch(
        project["id"],
        user["id"],
        run["id"],
        [],
        [{"domain": "locked.example", "reason": "password_page"}],
        [{"domain": "locked.example"}],
    )
    account_db.reset_account_db_engine()
    leads, rejected = account_db.project_history(project["id"], user_id=user["id"])
    assert leads == []
    assert any(row["domain"] == "locked.example" and row["reason"] == "password_page" for row in rejected)


def test_rollback_visibility_postgres_only_rows_still_readable(tmp_path, monkeypatch):
    """Switching flag to sqlite must not hide rows written while postgres/dual was on."""
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, user = _signup_project(account_db, "roll@example.com")
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token,
    )
    monkeypatch.setenv("DISCOVERY_DURABLE_STORE", "postgres")
    account_db.flush_durable_batch(
        project["id"],
        user["id"],
        run["id"],
        [{"domain": "pg-only.example", "store_name": "PG"}],
        [{"domain": "pg-rej.example", "reason": "no_public_products"}],
        [],
    )
    monkeypatch.setenv("DISCOVERY_DURABLE_STORE", "sqlite")
    import importlib
    import config

    importlib.reload(config)
    assert config.durable_store_mode() == "sqlite"
    leads = account_db.handle(f"/projects/{project['id']}/leads", "GET", None, token)
    rejected = account_db.handle(f"/projects/{project['id']}/rejected", "GET", None, token)
    assert any(row["domain"] == "pg-only.example" for row in leads)
    assert any(row["domain"] == "pg-rej.example" for row in rejected)


def test_ownership_rejects_foreign_run(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token_a, project_a, user_a = _signup_project(account_db, "owna@example.com")
    token_b, project_b, user_b = _signup_project(account_db, "ownb@example.com")
    run_b = account_db.handle(
        f"/projects/{project_b['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": True},
        token_b,
    )
    try:
        account_db.flush_durable_batch(
            project_a["id"],
            user_a["id"],
            run_b["id"],
            [{"domain": "x.example"}],
            [],
            [],
        )
        assert False, "expected ownership error"
    except RuntimeError as exc:
        assert "not owned" in str(exc).lower() or "not found" in str(exc).lower()


def test_scratch_guard_refuses_missing_and_prod_match(monkeypatch):
    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import phase_c_scratch

    monkeypatch.delenv("SCRATCH_DATABASE_URL", raising=False)
    try:
        phase_c_scratch.scratch_database_url()
        assert False, "expected exit"
    except SystemExit as exc:
        assert "SCRATCH_DATABASE_URL" in str(exc)

    monkeypatch.setenv("DATABASE_URL", "postgresql+psycopg2://u:p@host/db")
    monkeypatch.setenv("SCRATCH_DATABASE_URL", "postgresql+psycopg2://u:p@host/db")
    try:
        phase_c_scratch.scratch_database_url()
        assert False, "expected exit"
    except SystemExit as exc:
        assert "must not equal" in str(exc)


def test_dedupe_preserves_backup_and_never_cross_project(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, user = _signup_project(account_db, "dup@example.com")
    token2, project2, user2 = _signup_project(account_db, "dup2@example.com")
    session = account_db._session(account_db.database_url())
    try:
        # Insert duplicate rejected rows directly (bypass upsert) for project 1
        for _ in range(3):
            session.add(
                account_db.RejectedCandidate(
                    project_id=project["id"],
                    user_id=user["id"],
                    domain="dup.example",
                    reason="password_page",
                )
            )
        session.add(
            account_db.RejectedCandidate(
                project_id=project2["id"],
                user_id=user2["id"],
                domain="dup.example",
                reason="no_public_products",
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setenv("SCRATCH_DATABASE_URL", os.environ["DATABASE_URL"])
    monkeypatch.delenv("DATABASE_URL", raising=False)
    # Re-set both: guard compares equality; use same sqlite path only as scratch
    # after clearing prod-like DATABASE_URL
    db_url = f"sqlite:///{(tmp_path / 'phase_c_account.db').as_posix()}"
    monkeypatch.setenv("SCRATCH_DATABASE_URL", db_url)
    monkeypatch.delenv("DATABASE_URL", raising=False)

    scripts = ROOT / "scripts"
    if str(scripts) not in sys.path:
        sys.path.insert(0, str(scripts))
    import phase_c_dedupe

    # Point dedupe at scratch sqlite by ensuring env is set; engine from scratch URL
    from sqlalchemy import create_engine, text

    engine = create_engine(db_url)
    with engine.begin() as connection:
        phase_c_dedupe._ensure_backup_tables(connection)
        result = phase_c_dedupe._dedupe_table(
            connection,
            "rejected_candidates",
            "rejected_candidates_dedupe_backup",
            ["store_name", "public_email", "reason", "run_id"],
            apply=True,
        )
    assert result["duplicate_pairs"] == 1
    assert result["after"] == 2  # one survivor in p1 + one in p2
    with engine.connect() as connection:
        backup = connection.execute(text("SELECT COUNT(*) FROM rejected_candidates_dedupe_backup")).scalar()
        p1 = connection.execute(
            text("SELECT COUNT(*) FROM rejected_candidates WHERE project_id = :p"),
            {"p": project["id"]},
        ).scalar()
        p2 = connection.execute(
            text("SELECT COUNT(*) FROM rejected_candidates WHERE project_id = :p"),
            {"p": project2["id"]},
        ).scalar()
    assert backup == 2
    assert p1 == 1
    assert p2 == 1
