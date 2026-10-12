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
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text, create_engine, text
from sqlalchemy.orm import declarative_base, sessionmaker

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


def handle(path: str, method: str, payload: dict | None, token: str | None):
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    try:
        session = _session(url)
        try:
            return _route(session, path, method.upper(), payload or {}, token or "")
        finally:
            try:
                session.close()
            except Exception:
                pass
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Account database error: {_public_error(exc, url)}") from exc


def _session(url: str):
    global _engine, _Session
    if _Session is None:
        kwargs = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            kwargs["connect_args"] = {"check_same_thread": False}
        engine = create_engine(url, **kwargs)
        Base.metadata.create_all(bind=engine)
        _ensure_run_columns(engine)
        _engine = engine
        _Session = sessionmaker(bind=engine)
    return _Session()


def _ensure_run_columns(engine) -> None:
    """Add queue columns on databases created before cycles could run together."""
    from sqlalchemy import inspect

    try:
        existing = {column["name"] for column in inspect(engine).get_columns("discovery_runs")}
    except Exception:
        return
    extra = {
        "settings_json": "TEXT",
        "log_text": "TEXT",
        "summary": "TEXT",
        "error": "TEXT",
        "result_json": "TEXT",
    }
    missing = [name for name in extra if name not in existing]
    if not missing:
        return
    with engine.begin() as connection:
        for name in missing:
            connection.execute(text(f"ALTER TABLE discovery_runs ADD COLUMN {name} {extra[name]}"))


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
        )
        session.add(run)
        session.commit()
        return {"id": run.id, "name": run.name, "status": run.status}
    one_run = re.fullmatch(r"/projects/([^/]+)/runs/([^/]+)", path)
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


def claim_next_run() -> dict | None:
    """Take the oldest queued cycle. Other machines skip a cycle already taken."""
    url = database_url()
    if not url:
        return None
    session = _session(url)
    try:
        if session.get_bind().dialect.name == "postgresql":
            found = session.execute(
                text(
                    """
                    UPDATE discovery_runs
                    SET status = 'running'
                    WHERE id = (
                        SELECT id FROM discovery_runs
                        WHERE status = 'queued'
                        ORDER BY created_at
                        FOR UPDATE SKIP LOCKED
                        LIMIT 1
                    )
                    RETURNING id
                    """
                )
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
