"""
Account storage for the Streamlit app.

When DATABASE_URL is set, sign-in, projects, and leads are stored in
that Postgres database. Each row belongs to one user.
"""

from __future__ import annotations

import os
import re
import uuid
from datetime import datetime, timezone

import bcrypt
from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, create_engine
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
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://") :]
    if url.startswith("postgresql+psycopg2://"):
        url = "postgresql+psycopg://" + url[len("postgresql+psycopg2://") :]
    elif url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://") :]
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
            session.close()
    except RuntimeError:
        raise
    except Exception as exc:
        raise RuntimeError(f"Account database error: {_public_error(exc, url)}") from exc


def _session(url: str):
    global _engine, _Session
    if _engine is None:
        _engine = create_engine(url, pool_pre_ping=True)
        Base.metadata.create_all(bind=_engine)
        _Session = sessionmaker(bind=_engine)
    return _Session()


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
        )
        session.add(run)
        session.commit()
        return {"id": run.id, "name": run.name, "status": run.status}
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
