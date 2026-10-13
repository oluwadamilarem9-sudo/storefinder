"""
Account storage for the Streamlit app.

When DATABASE_URL is set, sign-in, projects, and leads are stored in
that Postgres database. Each row belongs to one user.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
import json
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

Base = declarative_base()
_engine = None
_Session = None


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=True)
    status = Column(String, default="queued", nullable=False)
    max_domains = Column(Integer, default=20)
    settings_json = Column(Text, nullable=True)
    log_text = Column(Text, nullable=True)
    summary = Column(Text, nullable=True)
    error = Column(Text, nullable=True)
    result_json = Column(Text, nullable=True)
    touched_at = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class Lead(Base):
    __tablename__ = "leads"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    run_id = Column(String, ForeignKey("discovery_runs.id"), nullable=True)
    domain = Column(String, nullable=False)
    store_name = Column(String, nullable=True)
    public_email = Column(String, nullable=True)
    country = Column(String, nullable=True)
    freshness_level = Column(String, nullable=True)
    freshness_score = Column(Integer, nullable=True)
    source = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class RejectedCandidate(Base):
    __tablename__ = "rejected_candidates"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    run_id = Column(String, ForeignKey("discovery_runs.id"), nullable=True)
    domain = Column(String, nullable=False)
    store_name = Column(String, nullable=True)
    public_email = Column(String, nullable=True)
    reason = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


def database_url() -> str:
    url = os.environ.get("DATABASE_URL", "").strip()
    if not url:
        try:
            import streamlit as st

            url = str(st.secrets["DATABASE_URL"]).strip()
        except Exception:
            url = ""
    if not url:
        return ""
    if "://" in url:
        scheme, rest = url.split("://", 1)
        if scheme in {"postgres", "postgresql", "postgresql+psycopg", "postgresql+psycopg2"}:
            url = "postgresql+psycopg2://" + rest
    if url.startswith("postgresql") and "sslmode" not in url:
        join = "&" if "?" in url else "?"
        url = f"{url}{join}sslmode=require"
    return url


def using_database() -> bool:
    return bool(database_url())


def _clean_path(path: str) -> str:
    return (path or "").strip().split("?", 1)[0].rstrip("/")


def handle(path: str, method: str, payload: dict | None, token: str | None):
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    method_name = (method or "GET").upper()
    cleaned = _clean_path(path)
    one_run = re.fullmatch(r"/projects/([^/]+)/runs/([^/]+)", cleaned)
    if one_run and method_name == "GET":
        return get_project_run(one_run.group(1), one_run.group(2), token or "")
    try:
        session = _session(url)
        try:
            return _route(session, cleaned, method_name, payload or {}, token or "")
        finally:
            try:
                session.close()
            except Exception:
                pass
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Account database error: {_public_error(exc, url)}") from exc


_ENGINE_LOCK = threading.Lock()


def _session(url: str):
    global _engine, _Session
    if _Session is None:
        with _ENGINE_LOCK:
            if _Session is None:
                kwargs = {"poolclass": NullPool}
                if url.startswith("sqlite"):
                    kwargs["connect_args"] = {"check_same_thread": False}
                engine = create_engine(url, **kwargs)
                Base.metadata.create_all(bind=engine)
                _ensure_run_columns(engine)
                _engine = engine
                _Session = sessionmaker(bind=engine)
    return _Session()


def _busy(exc: Exception) -> RuntimeError:
    """Hide connection details. The database only allows a few open sessions."""
    return RuntimeError("The account database is busy. The page will try again.")


_SCHEMA_LOCK = threading.Lock()


def _ensure_run_columns(engine) -> None:
    """Add queue columns on databases created before cycles could run together."""
    from sqlalchemy import inspect

    extra = {
        "settings_json": "TEXT",
        "log_text": "TEXT",
        "summary": "TEXT",
        "error": "TEXT",
        "result_json": "TEXT",
        "touched_at": "TEXT",
    }
    with _SCHEMA_LOCK:
        try:
            existing = {column["name"] for column in inspect(engine).get_columns("discovery_runs")}
        except Exception:
            return
        missing = [name for name in extra if name not in existing]
        if not missing:
            return
        is_postgres = engine.dialect.name == "postgresql"
        with engine.begin() as connection:
            for name in missing:
                if is_postgres:
                    statement = f"ALTER TABLE discovery_runs ADD COLUMN IF NOT EXISTS {name} {extra[name]}"
                else:
                    statement = f"ALTER TABLE discovery_runs ADD COLUMN {name} {extra[name]}"
                try:
                    connection.execute(text(statement))
                except Exception as exc:
                    message = str(exc).lower()
                    if "duplicate column" in message or "already exists" in message:
                        continue
                    raise


def _route(session, path: str, method: str, payload: dict, token: str):
    if path == "/auth/signup" and method == "POST":
        return _signup(session, payload)
    if path == "/auth/login" and method == "POST":
        return _login(session, payload)
    if path == "/projects" and method == "GET":
        user = _require_user(session, token)
        rows = session.query(Project).filter(Project.user_id == user.id).all()
        return [{"id": row.id, "name": row.name, "created_at": _iso(row.created_at)} for row in rows]
    if path == "/projects" and method == "POST":
        user = _require_user(session, token)
        project = Project(user_id=user.id, name=str(payload.get("name") or "Project"))
        session.add(project)
        session.commit()
        return {"id": project.id, "name": project.name}
    run_match = re.fullmatch(r"/projects/([^/]+)/runs", path)
    if run_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, run_match.group(1))
        run = DiscoveryRun(
            project_id=project.id,
            user_id=user.id,
            name=payload.get("name") or "Discovery run",
            max_domains=int(payload.get("max_domains") or 20),
            status="queued",
            settings_json=json.dumps(payload.get("settings") or {}),
            log_text="Cycle started. Searching public sources for shops on their own domain.",
            touched_at=datetime.now(timezone.utc).isoformat(),
        )
        session.add(run)
        session.commit()
        return {"id": run.id, "name": run.name, "status": run.status}
    one_run = re.fullmatch(r"/projects/([^/]+)/runs/([^/]+)", _clean_path(path))
    if one_run and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, one_run.group(1))
        run = session.get(DiscoveryRun, one_run.group(2))
        if run is None or run.project_id != project.id or run.user_id != user.id:
            raise RuntimeError("Run not found")
        return _run_public(run)
    lead_match = re.fullmatch(r"/projects/([^/]+)/leads", path)
    if lead_match and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, lead_match.group(1))
        rows = session.query(Lead).filter(Lead.project_id == project.id, Lead.user_id == user.id).all()
        return [_lead_dict(row) for row in rows]
    if lead_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, lead_match.group(1))
        lead = Lead(
            project_id=project.id,
            user_id=user.id,
            run_id=payload.get("run_id"),
            domain=str(payload.get("domain") or ""),
            store_name=payload.get("store_name"),
            public_email=payload.get("public_email"),
            country=payload.get("country"),
            freshness_level=payload.get("freshness_level"),
            freshness_score=payload.get("freshness_score"),
            source=payload.get("source"),
        )
        session.add(lead)
        session.commit()
        return {"id": lead.id, "domain": lead.domain}
    rejected_match = re.fullmatch(r"/projects/([^/]+)/rejected", path)
    if rejected_match and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, rejected_match.group(1))
        rows = (
            session.query(RejectedCandidate)
            .filter(RejectedCandidate.project_id == project.id, RejectedCandidate.user_id == user.id)
            .all()
        )
        return [_rejected_dict(row) for row in rows]
    if rejected_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, rejected_match.group(1))
        row = RejectedCandidate(
            project_id=project.id,
            user_id=user.id,
            run_id=payload.get("run_id"),
            domain=str(payload.get("domain") or ""),
            store_name=payload.get("store_name"),
            public_email=payload.get("public_email"),
            reason=payload.get("reason"),
        )
        session.add(row)
        session.commit()
        return {"id": row.id, "domain": row.domain}
    raise RuntimeError(f"Unsupported account request: {method} {path}")


def get_project_run(project_id: str, run_id: str, token: str) -> dict:
    """Read one cycle for the signed-in project."""
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    try:
        session = _session(url)
        try:
            user = _require_user(session, token or "")
            project = _owned_project(session, user.id, project_id)
            run = session.get(DiscoveryRun, run_id)
            if run is None or run.project_id != project.id or run.user_id != user.id:
                raise RuntimeError("Run not found")
            return _run_public(run)
        finally:
            session.close()
    except RuntimeError:
        raise
    except Exception as exc:
        raise _busy(exc) from exc


def latest_open_run(project_id: str, token: str) -> dict | None:
    """Return the signed-in project's cycle that is still queued or running."""
    url = database_url()
    if not url:
        return None
    try:
        session = _session(url)
        try:
            user = _require_user(session, token or "")
            project = _owned_project(session, user.id, project_id)
            run = (
                session.query(DiscoveryRun)
                .filter(
                    DiscoveryRun.project_id == project.id,
                    DiscoveryRun.user_id == user.id,
                    DiscoveryRun.status.in_(("queued", "running")),
                )
                .order_by(DiscoveryRun.created_at.desc())
                .first()
            )
            if run is None:
                return None
            return _run_public(run)
        finally:
            session.close()
    except RuntimeError:
        raise
    except Exception as exc:
        raise _busy(exc) from exc


def _run_public(run: DiscoveryRun) -> dict:
    result = {}
    if run.result_json:
        try:
            parsed = json.loads(run.result_json)
            if isinstance(parsed, dict):
                result = parsed
        except json.JSONDecodeError:
            result = {}
    return {
        "id": run.id,
        "status": run.status,
        "log": run.log_text or "",
        "summary": run.summary or "",
        "error": run.error or "",
        "saved": int(result.get("saved") or 0),
        "complete": bool(result.get("complete")),
        "lead_domains": list(result.get("lead_domains") or []),
        "rejected_domains": list(result.get("rejected_domains") or []),
        "stats": result.get("stats") or {},
    }


_CLAIM_LOCK = threading.Lock()


def _stamp(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _reclaim_stale_runs(session) -> None:
    """Put a cycle back in the queue when its worker stopped without finishing."""
    cutoff = datetime.now(timezone.utc) - timedelta(minutes=3)
    rows = session.query(DiscoveryRun).filter(DiscoveryRun.status == "running").all()
    changed = False
    for run in rows:
        if not str(run.touched_at or "").strip():
            run.status = "queued"
            changed = True
            continue
        stamp = _stamp(run.touched_at)
        if stamp is None or stamp < cutoff:
            run.status = "queued"
            changed = True
    if changed:
        session.commit()


def claim_next_run() -> dict | None:
    """Take the oldest queued cycle. Other machines skip a cycle already taken."""
    url = database_url()
    if not url:
        return None
    session = _session(url)
    try:
        _reclaim_stale_runs(session)
        if session.get_bind().dialect.name == "postgresql":
            now = datetime.now(timezone.utc).isoformat()
            found = session.execute(
                text(
                    """
                    UPDATE discovery_runs
                    SET status = 'running',
                        touched_at = :touched,
                        log_text = CASE
                            WHEN log_text IS NULL OR log_text = '' THEN :started
                            ELSE log_text
                        END
                    WHERE id = (
                        SELECT id FROM discovery_runs
                        WHERE status = 'queued'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                ),
                {
                    "touched": now,
                    "started": "Cycle started. Searching public sources for shops on their own domain.",
                },
            ).first()
            session.commit()
            if not found:
                return None
            run = session.get(DiscoveryRun, found[0])
            return None if run is None else _job_dict(run)
        with _CLAIM_LOCK:
            run = (
                session.query(DiscoveryRun)
                .filter(DiscoveryRun.status == "queued")
                .order_by(DiscoveryRun.created_at)
                .first()
            )
            if run is None:
                return None
            run.status = "running"
            run.touched_at = datetime.now(timezone.utc).isoformat()
            if not (run.log_text or "").strip():
                run.log_text = "Cycle started. Searching public sources for shops on their own domain."
            session.commit()
            return _job_dict(run)
    finally:
        session.close()


def save_run_log(run_id: str, log_text: str) -> None:
    url = database_url()
    if not url:
        return
    session = _session(url)
    try:
        run = session.get(DiscoveryRun, run_id)
        if run is None:
            return
        run.log_text = log_text[-20000:]
        run.touched_at = datetime.now(timezone.utc).isoformat()
        session.commit()
    finally:
        session.close()


def finish_run(run_id: str, *, status: str, summary: str, result: dict, error: str = "") -> None:
    url = database_url()
    if not url:
        return
    session = _session(url)
    try:
        run = session.get(DiscoveryRun, run_id)
        if run is None:
            return
        run.status = status
        run.summary = summary
        run.error = (error or "")[:500]
        run.result_json = json.dumps(result, default=str)
        session.commit()
    finally:
        session.close()


def _job_dict(run: DiscoveryRun) -> dict:
    settings = {}
    if run.settings_json:
        try:
            parsed = json.loads(run.settings_json)
            if isinstance(parsed, dict):
                settings = parsed
        except json.JSONDecodeError:
            settings = {}
    return {
        "id": run.id,
        "project_id": run.project_id,
        "user_id": run.user_id,
        "max_domains": int(run.max_domains or 20),
        "settings": settings,
    }


def project_history(project_id: str) -> tuple[list[str], list[dict]]:
    """Domains already saved for a project, plus password-page rows to watch again."""
    url = database_url()
    if not url:
        return [], []
    session = _session(url)
    try:
        leads = [row.domain for row in session.query(Lead).filter(Lead.project_id == project_id).all() if row.domain]
        rejected = [
            {"domain": row.domain, "reason": row.reason or ""}
            for row in session.query(RejectedCandidate).filter(RejectedCandidate.project_id == project_id).all()
            if row.domain
        ]
        return leads, rejected
    finally:
        session.close()


def store_run_rows(project_id: str, user_id: str, run_id: str, leads: list[dict], rejected: list[dict]) -> None:
    """Copy this cycle's new rows into the shared account database."""
    url = database_url()
    if not url:
        return
    session = _session(url)
    try:
        known_leads = {
            row.domain
            for row in session.query(Lead).filter(Lead.project_id == project_id).all()
            if row.domain
        }
        known_rejected = {
            row.domain
            for row in session.query(RejectedCandidate).filter(RejectedCandidate.project_id == project_id).all()
            if row.domain
        }
        for item in leads:
            domain = str(item.get("domain") or "")
            if not domain or domain in known_leads:
                continue
            session.add(
                Lead(
                    project_id=project_id,
                    user_id=user_id,
                    run_id=run_id,
                    domain=domain,
                    store_name=item.get("store_name"),
                    public_email=item.get("public_email"),
                    country=item.get("country"),
                    freshness_level=item.get("freshness_level"),
                    freshness_score=item.get("freshness_score"),
                    source=item.get("source"),
                )
            )
            known_leads.add(domain)
        for item in rejected:
            domain = str(item.get("domain") or "")
            if not domain or domain in known_rejected or domain in known_leads:
                continue
            session.add(
                RejectedCandidate(
                    project_id=project_id,
                    user_id=user_id,
                    run_id=run_id,
                    domain=domain,
                    store_name=item.get("store_name"),
                    public_email=item.get("public_email"),
                    reason=item.get("reason"),
                )
            )
            known_rejected.add(domain)
        session.commit()
    finally:
        session.close()


def _signup(session, payload: dict):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    if not email or not password:
        raise RuntimeError("Email and password are required")
    existing = session.query(User).filter(User.email == email).first()
    if existing:
        raise RuntimeError("User already exists")
    user = User(
        name=str(payload.get("name") or email.split("@")[0]),
        email=email,
        password_hash=bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8"),
    )
    session.add(user)
    session.commit()
    return {"token": user.id, "user": {"id": user.id, "name": user.name, "email": user.email}}


def _login(session, payload: dict):
    email = str(payload.get("email") or "").strip().lower()
    password = str(payload.get("password") or "")
    user = session.query(User).filter(User.email == email).first()
    if not user or not _password_ok(password, user.password_hash):
        raise RuntimeError("Invalid credentials")
    return {"token": user.id, "user": {"id": user.id, "name": user.name, "email": user.email}}


def _password_ok(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        return False


def _require_user(session, token: str) -> User:
    user = session.get(User, token) if token else None
    if user is None:
        raise RuntimeError("Sign in first")
    return user


def _owned_project(session, user_id: str, project_id: str) -> Project:
    project = session.query(Project).filter(Project.id == project_id, Project.user_id == user_id).first()
    if project is None:
        raise RuntimeError("Project not found or not owned by this user")
    return project


def _lead_dict(row: Lead) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "store_name": row.store_name,
        "public_email": row.public_email,
        "country": row.country,
        "freshness_level": row.freshness_level,
        "freshness_score": row.freshness_score,
        "source": row.source,
    }


def _rejected_dict(row: RejectedCandidate) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "store_name": row.store_name,
        "public_email": row.public_email,
        "reason": row.reason,
    }


def _iso(value) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _public_error(exc: Exception, url: str) -> str:
    text = str(exc).replace(url, "postgresql://***")
    return text.split("\n")[0][:300]
