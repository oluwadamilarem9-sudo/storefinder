"""
Phase B regression tests: run lifecycle, stale recovery, heartbeats, late writers.
"""

from __future__ import annotations

import json
import sys
import threading
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from threading import Thread

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
REPO = Path(__file__).resolve().parents[2]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))


def _account_db_with_temp(tmp_path, monkeypatch):
    import account_db

    db_file = tmp_path / "accounts.db"
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{db_file.as_posix()}")
    account_db.reset_account_db_engine()
    return account_db


def _signup_project_run(account_db, email: str, *, on_page: bool = True):
    signup = account_db.handle(
        "/auth/signup",
        "POST",
        {"name": email.split("@")[0], "email": email, "password": "secret123"},
        None,
    )
    token = signup["token"]
    project = account_db.handle("/projects", "POST", {"name": "P"}, token)
    run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R", "max_domains": 5, "on_page": on_page, "settings": {}},
        token,
    )
    return token, project, run


def test_duplicate_fresh_run_is_rejected(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, run = _signup_project_run(account_db, "dup@example.com")
    assert run["status"] == "running"
    try:
        account_db.handle(
            f"/projects/{project['id']}/runs",
            "POST",
            {"name": "R2", "max_domains": 5, "on_page": True},
            token,
        )
        assert False, "expected duplicate run to raise"
    except RuntimeError as exc:
        assert "already running" in str(exc).lower()
    loaded = account_db.get_project_run(project["id"], run["id"], token)
    assert loaded["status"] == "running"


def test_stale_run_is_closed_then_new_run_allowed(tmp_path, monkeypatch):
    import config

    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    monkeypatch.setattr(config, "RUN_STALE_MINUTES", 1)

    token, project, run = _signup_project_run(account_db, "stale@example.com")
    session = account_db._session(account_db.database_url())
    try:
        row = session.get(account_db.DiscoveryRun, run["id"])
        row.touched_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        session.commit()
    finally:
        session.close()

    new_run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R2", "max_domains": 5, "on_page": True},
        token,
    )
    assert new_run["status"] == "running"
    assert new_run["id"] != run["id"]
    old = account_db.get_project_run(project["id"], run["id"], token)
    assert old["status"] == "failed"
    assert "no progress" in (old["error"] or "").lower()


def test_touch_run_heartbeat_refreshes_touched_at(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, run = _signup_project_run(account_db, "beat@example.com")
    session = account_db._session(account_db.database_url())
    try:
        row = session.get(account_db.DiscoveryRun, run["id"])
        row.touched_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        session.commit()
        before = row.touched_at
    finally:
        session.close()
    assert account_db.touch_run(run["id"]) is True
    session = account_db._session(account_db.database_url())
    try:
        row = session.get(account_db.DiscoveryRun, run["id"])
        assert row.status == "running"
        assert row.touched_at != before
    finally:
        session.close()


def test_touch_run_false_when_already_finished(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    _token, _project, run = _signup_project_run(account_db, "done@example.com")
    account_db.finish_run(
        run["id"],
        status="complete",
        summary="done",
        result={"saved": 0, "complete": False},
    )
    assert account_db.touch_run(run["id"]) is False


def test_finish_run_is_idempotent(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, run = _signup_project_run(account_db, "idem@example.com")
    account_db.finish_run(
        run["id"],
        status="complete",
        summary="first",
        result={"saved": 1, "complete": True, "lead_domains": ["a.test"]},
    )
    account_db.finish_run(
        run["id"],
        status="failed",
        summary="second should be ignored",
        result={"saved": 0, "complete": False},
        error="should not apply",
    )
    loaded = account_db.get_project_run(project["id"], run["id"], token)
    assert loaded["status"] == "complete"
    assert loaded["saved"] == 1
    assert loaded["error"] == ""


def test_finish_run_with_error_cannot_stay_complete(tmp_path, monkeypatch):
    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, run = _signup_project_run(account_db, "err@example.com")
    account_db.finish_run(
        run["id"],
        status="complete",
        summary="x",
        result={"saved": 0, "complete": False},
        error="boom",
    )
    loaded = account_db.get_project_run(project["id"], run["id"], token)
    assert loaded["status"] == "failed"
    assert "boom" in (loaded["error"] or "")


def test_cancelled_status_when_stale_replaced(tmp_path, monkeypatch):
    """Queued stale rows are cancelled when a new run starts after reclaim."""
    import config

    account_db = _account_db_with_temp(tmp_path, monkeypatch)
    token, project, run = _signup_project_run(account_db, "cancel@example.com", on_page=False)
    assert run["status"] == "queued"
    session = account_db._session(account_db.database_url())
    try:
        row = session.get(account_db.DiscoveryRun, run["id"])
        monkeypatch.setattr(config, "RUN_STALE_MINUTES", 1)
        row.touched_at = (datetime.now(timezone.utc) - timedelta(minutes=5)).isoformat()
        session.commit()
    finally:
        session.close()
    # Stale queued is closed as failed by reclaim; leftover non-fresh open rows
    # get cancelled if any remain. After reclaim, open_rows should be empty.
    new_run = account_db.handle(
        f"/projects/{project['id']}/runs",
        "POST",
        {"name": "R2", "max_domains": 5, "on_page": True},
        token,
    )
    assert new_run["status"] == "running"
    old = account_db.get_project_run(project["id"], run["id"], token)
    assert old["status"] in {"failed", "cancelled"}


def test_abandoned_domain_skips_late_writes(tmp_path, monkeypatch):
    import pipeline
    from database.database import connect, load_leads_frame

    db_path = tmp_path / "project.db"
    connection = connect(db_path)
    connection.close()

    gate = threading.Event()
    started = threading.Event()

    def slow_process(connection, candidate, index, total, stats, log, countries, keep_unknown, is_cancelled=None):
        started.set()
        gate.wait(3)
        if is_cancelled and is_cancelled():
            log(f"  Status: skipped (abandoned) on {candidate.domain}")
            return True
        from database.database import upsert_lead

        upsert_lead(
            connection,
            {
                "domain": candidate.domain,
                "store_name": "Late",
                "shopify_status": "SHOPIFY",
                "discovery_source": "test",
                "freshness_score": 80,
                "freshness_level": "HIGH",
                "freshness_evidence": "late",
            },
        )
        stats.setdefault("this_run_lead_domains", []).append(candidate.domain)
        log("  Status: saved for download")
        return True

    monkeypatch.setattr(pipeline, "_process_candidate", slow_process)
    monkeypatch.setattr(pipeline.config, "CHECK_WORKERS", 1)
    monkeypatch.setattr(pipeline.config, "SITE_CHECK_TIMEOUT", 1)
    monkeypatch.setattr(
        pipeline,
        "discover_candidates",
        lambda **kwargs: [
            pipeline.DiscoveredCandidate(
                domain="late-shop.test",
                source="test",
                source_url="",
                discovered_at="2026-01-01T00:00:00+00:00",
                evidence="test",
            )
        ],
    )
    monkeypatch.setattr(pipeline, "prepare_candidates", lambda raw, connection: (raw, 0, 0))
    monkeypatch.setattr(pipeline, "active_sources", lambda enable_secondary=None: [])
    monkeypatch.setattr(pipeline, "watched_password_domains", lambda connection: [])
    monkeypatch.setattr(pipeline, "known_domains", lambda connection: set())

    logs: list[str] = []

    def run():
        pipeline.run_cycle(
            on_log=logs.append,
            max_domains=1,
            enable_secondary=False,
            target_countries=[],
            keep_unknown_country=True,
            db_path=db_path,
            leads_csv=tmp_path / "leads.csv",
            rejected_csv=tmp_path / "rejected.csv",
        )

    thread = Thread(target=run, daemon=True)
    thread.start()
    assert started.wait(2)
    # Let the site timeout path mark abandoned, then release the slow worker.
    time.sleep(1.5)
    gate.set()
    thread.join(8)
    assert thread.is_alive() is False
    assert any("timeout" in line.lower() or "abandoned" in line.lower() or "late result" in line.lower() for line in logs)
    connection = connect(db_path)
    try:
        leads = load_leads_frame(connection)
        assert "late-shop.test" not in set(leads["domain"].astype(str))
    finally:
        connection.close()


def test_run_cycle_calls_heartbeat(tmp_path, monkeypatch):
    import pipeline

    beats = {"n": 0}

    def on_heartbeat():
        beats["n"] += 1

    monkeypatch.setattr(pipeline, "discover_candidates", lambda **kwargs: [])
    monkeypatch.setattr(pipeline, "active_sources", lambda enable_secondary=None: [])
    pipeline.run_cycle(
        on_log=lambda _m: None,
        on_heartbeat=on_heartbeat,
        max_domains=3,
        enable_secondary=False,
        target_countries=["US"],
        keep_unknown_country=False,
        db_path=tmp_path / "p.db",
        leads_csv=tmp_path / "leads.csv",
        rejected_csv=tmp_path / "rejected.csv",
    )
    assert beats["n"] >= 1


def test_fetch_public_respects_timeout_budget(monkeypatch):
    """Network calls must not run without a finite timeout."""
    import utils.http as http

    captured = {}

    class FakeResponse:
        status_code = 200
        url = "https://example.com/"
        encoding = "utf-8"
        headers = {}
        raw = None

        def close(self):
            return None

    def fake_get(url, timeout=None, allow_redirects=True, headers=None, stream=False):
        captured["timeout"] = timeout
        captured["stream"] = stream
        response = FakeResponse()

        class Raw:
            def _init_decoder(self):
                return None

            @property
            def _decoder(self):
                return None

            @property
            def _fp(self):
                class FP:
                    def read1(self, _n):
                        return b"<html>ok</html>"

                return FP()

            @property
            def _connection(self):
                return None

        response.raw = Raw()
        return response

    class FakeSession:
        def get(self, *args, **kwargs):
            return fake_get(*args, **kwargs)

    monkeypatch.setattr(http, "_session", lambda: FakeSession())
    monkeypatch.setattr(http, "_wait_for_host", lambda url: None)
    monkeypatch.setattr(http, "robots_allowed", lambda url: True)
    result = http.fetch_public("https://example.com/", check_robots=False, timeout=3)
    assert result.ok
    assert captured["timeout"] == (2.5, 3.0) or captured["timeout"][0] == 2.5
    assert captured["stream"] is True
