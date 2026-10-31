"""
Shared account service (Phase 1A).

Canonical business logic for users, projects, discovery runs, leads,
rejected candidates, and durable flushes. Streamlit, workers, and (later)
FastAPI should call this module — not duplicate ORM/route logic.

When DATABASE_URL is set, data is stored in that Postgres (or SQLite) database.
Each row belongs to one user.

Compatibility: ``account_db`` re-exports this module for existing imports.
"""

from __future__ import annotations

import os
import re
import threading
import uuid
import json
from datetime import datetime, timedelta, timezone

import bcrypt
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    create_engine,
    text,
)
from sqlalchemy.orm import declarative_base, sessionmaker
from sqlalchemy.pool import NullPool

Base = declarative_base()
_engine = None
_Session = None
_ACCOUNT_DB_GENERATION = 8


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
    # UNIQUE(project_id, domain) is added by scripts/phase_c_add_unique_indexes.py
    # after dedupe — not here — so production-like scratch restores with duplicates can migrate.
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
    freshness_evidence = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    shopify_status = Column(String, nullable=True)
    contact_page = Column(String, nullable=True)
    about_page = Column(String, nullable=True)
    instagram = Column(String, nullable=True)
    facebook = Column(String, nullable=True)
    tiktok = Column(String, nullable=True)
    linkedin = Column(String, nullable=True)
    theme_name = Column(String, nullable=True)
    theme_confidence = Column(String, nullable=True)
    tracking_technologies = Column(Text, nullable=True)
    first_seen = Column(String, nullable=True)
    last_checked = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=True)
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
    freshness_level = Column(String, nullable=True)
    freshness_score = Column(Integer, nullable=True)
    freshness_evidence = Column(Text, nullable=True)
    source = Column(String, nullable=True)
    source_url = Column(String, nullable=True)
    shopify_status = Column(String, nullable=True)
    first_seen = Column(String, nullable=True)
    last_checked = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class ProcessedDomain(Base):
    __tablename__ = "processed_domains"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    domain = Column(String, nullable=False)
    first_seen = Column(String, nullable=True)
    last_checked = Column(String, nullable=True)
    shopify_status = Column(String, nullable=True)
    discovery_source = Column(String, nullable=True)
    updated_at = Column(DateTime, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class StoreProduct(Base):
    """Public catalog product linked to a saved store domain. Empty fields are unknown."""

    __tablename__ = "store_products"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    run_id = Column(String, ForeignKey("discovery_runs.id"), nullable=True)
    domain = Column(String, nullable=False)
    shopify_product_id = Column(String, nullable=True)
    title = Column(String, nullable=False)
    handle = Column(String, nullable=True)
    product_url = Column(String, nullable=True)
    price = Column(String, nullable=True)
    currency = Column(String, nullable=True)
    image_url = Column(String, nullable=True)
    category = Column(String, nullable=True)
    first_seen = Column(String, nullable=True)
    last_checked = Column(String, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))


class WatchlistItem(Base):
    """User-saved store domain for revisit / competitor tracking."""

    __tablename__ = "watchlist_items"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    domain = Column(String, nullable=False)
    note = Column(Text, nullable=True)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=True)


class SavedFilter(Base):
    """Reusable discovery / research filter configuration."""

    __tablename__ = "saved_filters"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    config_json = Column(Text, nullable=False, default="{}")
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    updated_at = Column(DateTime, nullable=True)


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
    url = _transaction_pooler(url)
    if url.startswith("postgresql") and "sslmode" not in url:
        join = "&" if "?" in url else "?"
        url = f"{url}{join}sslmode=require"
    return url


def _transaction_pooler(url: str) -> str:
    """Use the pooler's transaction port. Session mode refuses a 16th client."""
    if "pooler.supabase.com" not in url or "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    userinfo, hostpart = rest.rsplit("@", 1)
    host_only = hostpart.split("/")[0].split("?")[0]
    hostname = host_only
    if hostname.count(":") == 1 and not hostname.startswith("["):
        hostname = hostname.rsplit(":", 1)[0]
    if not hostname.endswith("pooler.supabase.com"):
        return url
    if host_only.endswith(":6543"):
        return url
    if ":5432" in host_only:
        hostpart = hostpart.replace(":5432", ":6543", 1)
    else:
        hostpart = hostpart.replace(hostname, hostname + ":6543", 1)
    return f"{scheme}://{userinfo}@{hostpart}"


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
_DB_SLOTS: threading.BoundedSemaphore | None = None
_DB_SLOTS_SIZE = 0


def _max_db_sessions() -> int:
    """
    Cap open SQLAlchemy sessions per process.

    Supabase session-mode poolers historically needed a hard 2.
    Dedicated Render Postgres / local SQLite tolerate a few more so the
    API can answer parallel Next.js requests while a worker process
    (separate semaphore) flushes leads.
    """
    raw = (os.environ.get("ACCOUNT_DB_MAX_SESSIONS") or "").strip()
    if raw.isdigit():
        return max(1, min(int(raw), 16))
    url = database_url()
    if "pooler.supabase.com" in url or "supabase.co" in url:
        return 2
    return 4


def _db_slots() -> threading.BoundedSemaphore:
    global _DB_SLOTS, _DB_SLOTS_SIZE
    size = _max_db_sessions()
    if _DB_SLOTS is None or _DB_SLOTS_SIZE != size:
        with _ENGINE_LOCK:
            size = _max_db_sessions()
            if _DB_SLOTS is None or _DB_SLOTS_SIZE != size:
                _DB_SLOTS = threading.BoundedSemaphore(size)
                _DB_SLOTS_SIZE = size
    return _DB_SLOTS


def reset_account_db_engine() -> None:
    """Test helper: drop the process-wide SQLAlchemy engine so a temp DB can be used."""
    global _engine, _Session, _DB_SLOTS, _DB_SLOTS_SIZE
    with _ENGINE_LOCK:
        if _engine is not None:
            try:
                _engine.dispose()
            except Exception:
                pass
        _engine = None
        _Session = None
        _DB_SLOTS = None
        _DB_SLOTS_SIZE = 0


def _session(url: str):
    """Open one database session under the per-process session cap."""
    global _engine, _Session
    if _Session is None:
        with _ENGINE_LOCK:
            if _Session is None:
                kwargs = {"poolclass": NullPool}
                if url.startswith("sqlite"):
                    kwargs["connect_args"] = {"check_same_thread": False}
                elif "postgresql" in url:
                    kwargs["connect_args"] = {"connect_timeout": 5}
                engine = create_engine(url, **kwargs)
                Base.metadata.create_all(bind=engine)
                _ensure_run_columns(engine)
                _ensure_phase_c_columns(engine)
                _engine = engine
                _Session = sessionmaker(bind=engine)
    slots = _db_slots()
    wait_raw = (os.environ.get("ACCOUNT_DB_SESSION_WAIT_SECONDS") or "12").strip()
    wait_seconds = float(wait_raw) if wait_raw.replace(".", "", 1).isdigit() else 12.0
    if not slots.acquire(timeout=max(0.05, wait_seconds)):
        raise RuntimeError("The account database is busy. Wait a moment and try again.")
    try:
        session = _Session()
    except Exception:
        slots.release()
        raise
    original_close = session.close
    closed = False

    def close():
        nonlocal closed
        if closed:
            return
        closed = True
        try:
            original_close()
        finally:
            slots.release()

    session.close = close
    return session


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


_PHASE_C_LEAD_COLUMNS = {
    "freshness_evidence": "TEXT",
    "source_url": "TEXT",
    "shopify_status": "TEXT",
    "contact_page": "TEXT",
    "about_page": "TEXT",
    "instagram": "TEXT",
    "facebook": "TEXT",
    "tiktok": "TEXT",
    "linkedin": "TEXT",
    "theme_name": "TEXT",
    "theme_confidence": "TEXT",
    "tracking_technologies": "TEXT",
    "first_seen": "TEXT",
    "last_checked": "TEXT",
    "updated_at": "TIMESTAMP",
}

_PHASE_C_REJECTED_COLUMNS = {
    "freshness_level": "TEXT",
    "freshness_score": "INTEGER",
    "freshness_evidence": "TEXT",
    "source": "TEXT",
    "source_url": "TEXT",
    "shopify_status": "TEXT",
    "first_seen": "TEXT",
    "last_checked": "TEXT",
    "updated_at": "TIMESTAMP",
}


def _ensure_phase_c_columns(engine) -> None:
    """Additive Phase C columns and processed_domains table. No unique indexes here."""
    from sqlalchemy import inspect

    is_postgres = engine.dialect.name == "postgresql"
    with _SCHEMA_LOCK:
        inspector = inspect(engine)
        tables = set(inspector.get_table_names())
        with engine.begin() as connection:
            if "processed_domains" not in tables:
                if is_postgres:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS processed_domains (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL REFERENCES projects(id),
                                user_id TEXT NOT NULL REFERENCES users(id),
                                domain TEXT NOT NULL,
                                first_seen TEXT,
                                last_checked TEXT,
                                shopify_status TEXT,
                                discovery_source TEXT,
                                updated_at TIMESTAMP,
                                created_at TIMESTAMP
                            )
                            """
                        )
                    )
                else:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS processed_domains (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL,
                                user_id TEXT NOT NULL,
                                domain TEXT NOT NULL,
                                first_seen TEXT,
                                last_checked TEXT,
                                shopify_status TEXT,
                                discovery_source TEXT,
                                updated_at TIMESTAMP,
                                created_at TIMESTAMP
                            )
                            """
                        )
                    )
            if "store_products" not in tables:
                if is_postgres:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS store_products (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL REFERENCES projects(id),
                                user_id TEXT NOT NULL REFERENCES users(id),
                                run_id TEXT REFERENCES discovery_runs(id),
                                domain TEXT NOT NULL,
                                shopify_product_id TEXT,
                                title TEXT NOT NULL,
                                handle TEXT,
                                product_url TEXT,
                                price TEXT,
                                currency TEXT,
                                image_url TEXT,
                                category TEXT,
                                first_seen TEXT,
                                last_checked TEXT,
                                created_at TIMESTAMP
                            )
                            """
                        )
                    )
                else:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS store_products (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL,
                                user_id TEXT NOT NULL,
                                run_id TEXT,
                                domain TEXT NOT NULL,
                                shopify_product_id TEXT,
                                title TEXT NOT NULL,
                                handle TEXT,
                                product_url TEXT,
                                price TEXT,
                                currency TEXT,
                                image_url TEXT,
                                category TEXT,
                                first_seen TEXT,
                                last_checked TEXT,
                                created_at TIMESTAMP
                            )
                            """
                        )
                    )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_store_products_project_domain "
                        "ON store_products (project_id, domain)"
                    )
                )
            if "watchlist_items" not in tables:
                if is_postgres:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS watchlist_items (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL REFERENCES projects(id),
                                user_id TEXT NOT NULL REFERENCES users(id),
                                domain TEXT NOT NULL,
                                note TEXT,
                                created_at TIMESTAMP,
                                updated_at TIMESTAMP
                            )
                            """
                        )
                    )
                else:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS watchlist_items (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL,
                                user_id TEXT NOT NULL,
                                domain TEXT NOT NULL,
                                note TEXT,
                                created_at TIMESTAMP,
                                updated_at TIMESTAMP
                            )
                            """
                        )
                    )
                connection.execute(
                    text(
                        "CREATE INDEX IF NOT EXISTS idx_watchlist_project_domain "
                        "ON watchlist_items (project_id, domain)"
                    )
                )
            if "saved_filters" not in tables:
                if is_postgres:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS saved_filters (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL REFERENCES projects(id),
                                user_id TEXT NOT NULL REFERENCES users(id),
                                name TEXT NOT NULL,
                                config_json TEXT NOT NULL,
                                created_at TIMESTAMP,
                                updated_at TIMESTAMP
                            )
                            """
                        )
                    )
                else:
                    connection.execute(
                        text(
                            """
                            CREATE TABLE IF NOT EXISTS saved_filters (
                                id TEXT PRIMARY KEY,
                                project_id TEXT NOT NULL,
                                user_id TEXT NOT NULL,
                                name TEXT NOT NULL,
                                config_json TEXT NOT NULL,
                                created_at TIMESTAMP,
                                updated_at TIMESTAMP
                            )
                            """
                        )
                    )
            for table, extras in (
                ("leads", _PHASE_C_LEAD_COLUMNS),
                ("rejected_candidates", _PHASE_C_REJECTED_COLUMNS),
            ):
                try:
                    existing = {column["name"] for column in inspector.get_columns(table)}
                except Exception:
                    continue
                for name, typ in extras.items():
                    if name in existing:
                        continue
                    if is_postgres:
                        statement = f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS {name} {typ}"
                    else:
                        statement = f"ALTER TABLE {table} ADD COLUMN {name} {typ}"
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
    if run_match and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, run_match.group(1))
        rows = (
            session.query(DiscoveryRun)
            .filter(DiscoveryRun.project_id == project.id, DiscoveryRun.user_id == user.id)
            .order_by(DiscoveryRun.created_at.desc())
            .all()
        )
        return [
            {
                "id": row.id,
                "name": row.name,
                "status": row.status,
                "max_domains": row.max_domains,
            }
            for row in rows
        ]
    if run_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, run_match.group(1))
        _close_stale_open_runs(
            session,
            project_id=project.id,
            user_id=user.id,
        )
        open_rows = (
            session.query(DiscoveryRun)
            .filter(
                DiscoveryRun.project_id == project.id,
                DiscoveryRun.user_id == user.id,
                DiscoveryRun.status.in_(("queued", "running")),
            )
            .all()
        )
        # Refuse a second live cycle for the same project (duplicate-run prevention).
        for old in open_rows:
            if _run_is_fresh(old):
                raise RuntimeError(
                    "A discovery cycle is already running for this project. "
                    "Wait for it to finish, or wait until it is marked abandoned."
                )
        now = datetime.now(timezone.utc).isoformat()
        for old in open_rows:
            _mark_run_terminal(
                old,
                status="cancelled",
                summary="Replaced by a new discovery cycle.",
                error="Replaced by a new discovery cycle.",
                touched_at=now,
            )
        max_domains = min(max(int(payload.get("max_domains") or 20), 1), 100)
        run = DiscoveryRun(
            project_id=project.id,
            user_id=user.id,
            name=payload.get("name") or "Discovery run",
            max_domains=max_domains,
            status="running" if payload.get("on_page") else "queued",
            settings_json=json.dumps(payload.get("settings") or {}),
            log_text="Cycle started. Searching public sources for shops on their own domain.",
            touched_at=now,
            result_json=json.dumps(
                {
                    "started_at": now,
                    "status": "running" if payload.get("on_page") else "queued",
                }
            ),
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
        lead = _upsert_lead_row(session, project.id, user.id, payload)
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
        row = _upsert_rejected_row(session, project.id, user.id, payload)
        session.commit()
        return {"id": row.id, "domain": row.domain}
    products_match = re.fullmatch(r"/projects/([^/]+)/products", path)
    if products_match and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, products_match.group(1))
        rows = (
            session.query(StoreProduct)
            .filter(StoreProduct.project_id == project.id, StoreProduct.user_id == user.id)
            .order_by(StoreProduct.domain.asc(), StoreProduct.title.asc())
            .all()
        )
        return [_product_dict(row) for row in rows]
    if products_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, products_match.group(1))
        domain = str(payload.get("domain") or "").strip()
        products = payload.get("products") if isinstance(payload.get("products"), list) else []
        count = _replace_store_products(
            session,
            project.id,
            user.id,
            domain,
            products,
            run_id=payload.get("run_id"),
        )
        session.commit()
        return {"domain": domain, "count": count}
    compare_match = re.fullmatch(r"/projects/([^/]+)/compare", path)
    if compare_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, compare_match.group(1))
        return _compare_project_stores(session, project.id, user.id, payload or {})
    watch_match = re.fullmatch(r"/projects/([^/]+)/watchlist", path)
    if watch_match and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, watch_match.group(1))
        rows = (
            session.query(WatchlistItem)
            .filter(WatchlistItem.project_id == project.id, WatchlistItem.user_id == user.id)
            .order_by(WatchlistItem.created_at.desc())
            .all()
        )
        return [_watchlist_dict(session, project.id, user.id, row) for row in rows]
    if watch_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, watch_match.group(1))
        row = _upsert_watchlist_item(session, project.id, user.id, payload or {})
        session.commit()
        return _watchlist_dict(session, project.id, user.id, row)
    watch_one = re.fullmatch(r"/projects/([^/]+)/watchlist/([^/]+)", path)
    if watch_one and method == "DELETE":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, watch_one.group(1))
        row = session.get(WatchlistItem, watch_one.group(2))
        if row is None or row.project_id != project.id or row.user_id != user.id:
            raise RuntimeError("Watchlist item not found")
        session.delete(row)
        session.commit()
        return {"ok": True}
    filters_match = re.fullmatch(r"/projects/([^/]+)/filters", path)
    if filters_match and method == "GET":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, filters_match.group(1))
        rows = (
            session.query(SavedFilter)
            .filter(SavedFilter.project_id == project.id, SavedFilter.user_id == user.id)
            .order_by(SavedFilter.created_at.desc())
            .all()
        )
        return [_filter_dict(row) for row in rows]
    if filters_match and method == "POST":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, filters_match.group(1))
        row = _create_saved_filter(session, project.id, user.id, payload or {})
        session.commit()
        return _filter_dict(row)
    filter_one = re.fullmatch(r"/projects/([^/]+)/filters/([^/]+)", path)
    if filter_one and method == "DELETE":
        user = _require_user(session, token)
        project = _owned_project(session, user.id, filter_one.group(1))
        row = session.get(SavedFilter, filter_one.group(2))
        if row is None or row.project_id != project.id or row.user_id != user.id:
            raise RuntimeError("Saved filter not found")
        session.delete(row)
        session.commit()
        return {"ok": True}
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
        "name": run.name or "",
        "status": run.status,
        "max_domains": int(run.max_domains or 0),
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


def _stale_after() -> timedelta:
    try:
        from config import RUN_STALE_MINUTES

        minutes = int(RUN_STALE_MINUTES or 20)
    except Exception:
        minutes = 20
    return timedelta(minutes=max(minutes, 1))


def _run_is_fresh(run: DiscoveryRun) -> bool:
    """True when a queued/running cycle still has a recent heartbeat."""
    if run.status not in {"queued", "running"}:
        return False
    stamp = _stamp(run.touched_at) or _stamp(run.created_at)
    if stamp is None:
        return False
    return stamp >= datetime.now(timezone.utc) - _stale_after()


def _mark_run_terminal(
    run: DiscoveryRun,
    *,
    status: str,
    summary: str,
    error: str,
    touched_at: str,
) -> None:
    payload = {}
    if run.result_json:
        try:
            parsed = json.loads(run.result_json)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            payload = {}
    started = payload.get("started_at") or (_iso(run.created_at) if run.created_at else touched_at)
    payload["started_at"] = started
    payload["completed_at"] = touched_at
    payload["status"] = status
    run.status = status
    run.summary = summary
    run.error = (error or "")[:500]
    run.result_json = json.dumps(payload, default=str)
    run.touched_at = touched_at


def _close_stale_open_runs(session, *, project_id: str | None = None, user_id: str | None = None) -> int:
    """Fail abandoned queued/running cycles. Returns how many were closed."""
    query = session.query(DiscoveryRun).filter(DiscoveryRun.status.in_(("queued", "running")))
    if project_id:
        query = query.filter(DiscoveryRun.project_id == project_id)
    if user_id:
        query = query.filter(DiscoveryRun.user_id == user_id)
    rows = query.all()
    now = datetime.now(timezone.utc).isoformat()
    closed = 0
    for run in rows:
        if _run_is_fresh(run):
            continue
        _mark_run_terminal(
            run,
            status="failed",
            summary="Cycle stopped because it sat with no progress.",
            error="Cycle stopped because it sat with no progress.",
            touched_at=now,
        )
        closed += 1
    if closed:
        session.commit()
    return closed


def _reclaim_stale_runs(session) -> None:
    """Close a cycle that sat with no heartbeat instead of leaving it forever."""
    _close_stale_open_runs(session)


def touch_run(run_id: str) -> bool:
    """Heartbeat for a long on-page cycle. False when the run is no longer running."""
    url = database_url()
    if not url or not run_id:
        return False
    session = _session(url)
    try:
        run = session.get(DiscoveryRun, run_id)
        if run is None or run.status != "running":
            return False
        run.touched_at = datetime.now(timezone.utc).isoformat()
        session.commit()
        return True
    finally:
        session.close()


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
    """Mark a discovery run finished. Idempotent once the run is already terminal."""
    url = database_url()
    if not url:
        return
    terminal = (status or "").strip().lower()
    err = (error or "").strip()
    # A failed operation must not be stored as complete.
    if err and terminal == "complete":
        terminal = "failed"
    if terminal not in {"complete", "failed", "cancelled"}:
        terminal = "failed" if err else "complete"
    session = _session(url)
    try:
        run = session.get(DiscoveryRun, run_id)
        if run is None:
            return
        # Do not reopen or rewrite a run that already finished.
        if run.status in {"complete", "failed", "cancelled"}:
            return
        completed_at = datetime.now(timezone.utc).isoformat()
        started_at = _iso(run.created_at) if run.created_at else completed_at
        payload = dict(result or {})
        if run.result_json:
            try:
                prior = json.loads(run.result_json)
                if isinstance(prior, dict) and prior.get("started_at"):
                    started_at = str(prior["started_at"])
            except json.JSONDecodeError:
                pass
        payload.setdefault("started_at", started_at)
        payload["completed_at"] = completed_at
        payload["status"] = terminal
        run.status = terminal
        run.summary = summary or ""
        run.error = err[:500]
        run.result_json = json.dumps(payload, default=str)
        run.touched_at = completed_at
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


def project_history(project_id: str, user_id: str | None = None) -> tuple[list[str], list[dict]]:
    """Domains already saved for a project, plus password-page rows to watch again."""
    url = database_url()
    if not url:
        return [], []
    session = _session(url)
    try:
        if user_id:
            _owned_project(session, user_id, project_id)
            lead_q = session.query(Lead).filter(Lead.project_id == project_id, Lead.user_id == user_id)
            rejected_q = session.query(RejectedCandidate).filter(
                RejectedCandidate.project_id == project_id,
                RejectedCandidate.user_id == user_id,
            )
        else:
            lead_q = session.query(Lead).filter(Lead.project_id == project_id)
            rejected_q = session.query(RejectedCandidate).filter(RejectedCandidate.project_id == project_id)
        leads = [row.domain for row in lead_q.all() if row.domain]
        rejected = [
            {"domain": row.domain, "reason": row.reason or ""}
            for row in rejected_q.all()
            if row.domain
        ]
        return leads, rejected
    finally:
        session.close()


def store_run_rows(project_id: str, user_id: str, run_id: str, leads: list[dict], rejected: list[dict]) -> None:
    """Copy this cycle's new rows into the shared account database (batched upsert)."""
    flush_durable_batch(project_id, user_id, run_id, leads, rejected, processed=[])


def flush_durable_batch(
    project_id: str,
    user_id: str,
    run_id: str,
    leads: list[dict],
    rejected: list[dict],
    processed: list[dict] | None = None,
) -> None:
    """Upsert a batch of durable rows. Verifies project ownership and run ownership."""
    url = database_url()
    if not url:
        return
    session = _session(url)
    try:
        project = _owned_project(session, user_id, project_id)
        if run_id:
            run = session.get(DiscoveryRun, run_id)
            if run is None or run.project_id != project.id or run.user_id != user_id:
                raise RuntimeError("Run not found or not owned by this user")
        for item in leads or []:
            payload = dict(item)
            payload["run_id"] = payload.get("run_id") or run_id
            _upsert_lead_row(session, project.id, user_id, payload)
            products = payload.get("products")
            if isinstance(products, list):
                _replace_store_products(
                    session,
                    project.id,
                    user_id,
                    str(payload.get("domain") or ""),
                    products,
                    run_id=payload.get("run_id") or run_id,
                )
        for item in rejected or []:
            payload = dict(item)
            payload["run_id"] = payload.get("run_id") or run_id
            if "reason" not in payload and payload.get("reject_reason"):
                payload["reason"] = payload.get("reject_reason")
            _upsert_rejected_row(session, project.id, user_id, payload)
        for item in processed or []:
            _upsert_processed_row(session, project.id, user_id, item)
        session.commit()
    finally:
        session.close()


def _prefer(existing, incoming):
    if incoming is None:
        return existing
    if isinstance(incoming, (int, float)):
        return incoming
    text_value = str(incoming).strip()
    if not text_value:
        return existing
    return incoming


def _upsert_lead_row(session, project_id: str, user_id: str, payload: dict) -> Lead:
    domain = str(payload.get("domain") or "").strip()
    if not domain:
        raise RuntimeError("domain is required")
    now = datetime.now(timezone.utc)
    row = (
        session.query(Lead)
        .filter(Lead.project_id == project_id, Lead.user_id == user_id, Lead.domain == domain)
        .first()
    )
    if row is None:
        row = Lead(project_id=project_id, user_id=user_id, domain=domain)
        session.add(row)
        row.first_seen = payload.get("first_seen") or now.isoformat()
    row.run_id = _prefer(row.run_id, payload.get("run_id"))
    row.store_name = _prefer(row.store_name, payload.get("store_name"))
    row.public_email = _prefer(row.public_email, payload.get("public_email"))
    row.country = _prefer(row.country, payload.get("country"))
    row.freshness_level = _prefer(row.freshness_level, payload.get("freshness_level"))
    score = payload.get("freshness_score")
    if score is not None:
        row.freshness_score = int(score or 0)
    row.freshness_evidence = _prefer(row.freshness_evidence, payload.get("freshness_evidence"))
    row.source = _prefer(row.source, payload.get("source") or payload.get("discovery_source"))
    row.source_url = _prefer(row.source_url, payload.get("source_url"))
    row.shopify_status = _prefer(row.shopify_status, payload.get("shopify_status"))
    row.contact_page = _prefer(row.contact_page, payload.get("contact_page"))
    row.about_page = _prefer(row.about_page, payload.get("about_page"))
    row.instagram = _prefer(row.instagram, payload.get("instagram"))
    row.facebook = _prefer(row.facebook, payload.get("facebook"))
    row.tiktok = _prefer(row.tiktok, payload.get("tiktok"))
    row.linkedin = _prefer(row.linkedin, payload.get("linkedin"))
    row.theme_name = _prefer(row.theme_name, payload.get("theme_name"))
    row.theme_confidence = _prefer(row.theme_confidence, payload.get("theme_confidence"))
    tracking = payload.get("tracking_technologies")
    if tracking is not None:
        if isinstance(tracking, (list, dict)):
            row.tracking_technologies = json.dumps(tracking, default=str)
        else:
            row.tracking_technologies = _prefer(row.tracking_technologies, tracking)
    row.last_checked = payload.get("last_checked") or now.isoformat()
    row.updated_at = now
    return row


def _upsert_rejected_row(session, project_id: str, user_id: str, payload: dict) -> RejectedCandidate:
    domain = str(payload.get("domain") or "").strip()
    if not domain:
        raise RuntimeError("domain is required")
    now = datetime.now(timezone.utc)
    row = (
        session.query(RejectedCandidate)
        .filter(
            RejectedCandidate.project_id == project_id,
            RejectedCandidate.user_id == user_id,
            RejectedCandidate.domain == domain,
        )
        .first()
    )
    if row is None:
        row = RejectedCandidate(project_id=project_id, user_id=user_id, domain=domain)
        session.add(row)
        row.first_seen = payload.get("first_seen") or now.isoformat()
    row.run_id = _prefer(row.run_id, payload.get("run_id"))
    row.store_name = _prefer(row.store_name, payload.get("store_name"))
    row.public_email = _prefer(row.public_email, payload.get("public_email"))
    row.reason = _prefer(row.reason, payload.get("reason") or payload.get("reject_reason"))
    row.freshness_level = _prefer(row.freshness_level, payload.get("freshness_level"))
    score = payload.get("freshness_score")
    if score is not None:
        row.freshness_score = int(score or 0)
    row.freshness_evidence = _prefer(row.freshness_evidence, payload.get("freshness_evidence"))
    row.source = _prefer(row.source, payload.get("source") or payload.get("discovery_source"))
    row.source_url = _prefer(row.source_url, payload.get("source_url"))
    row.shopify_status = _prefer(row.shopify_status, payload.get("shopify_status"))
    row.last_checked = payload.get("last_checked") or now.isoformat()
    row.updated_at = now
    return row


def _replace_store_products(
    session,
    project_id: str,
    user_id: str,
    domain: str,
    products: list,
    run_id: str | None = None,
) -> int:
    """Replace this project's catalog snapshot for a domain. Incomplete fields stay empty."""
    domain_key = str(domain or "").strip()
    if not domain_key:
        return 0
    now = datetime.now(timezone.utc).isoformat()
    session.query(StoreProduct).filter(
        StoreProduct.project_id == project_id,
        StoreProduct.user_id == user_id,
        StoreProduct.domain == domain_key,
    ).delete(synchronize_session=False)
    saved = 0
    for item in products or []:
        if not isinstance(item, dict):
            continue
        title = " ".join(str(item.get("title") or "").split())
        if len(title) < 2:
            continue
        session.add(
            StoreProduct(
                project_id=project_id,
                user_id=user_id,
                run_id=run_id or None,
                domain=domain_key,
                shopify_product_id=str(item.get("shopify_product_id") or "") or None,
                title=title,
                handle=str(item.get("handle") or "") or None,
                product_url=str(item.get("product_url") or "") or None,
                price=str(item.get("price") or "") or None,
                currency=str(item.get("currency") or "") or None,
                image_url=str(item.get("image_url") or "") or None,
                category=str(item.get("category") or "") or None,
                first_seen=now,
                last_checked=now,
            )
        )
        saved += 1
    return saved


def _upsert_processed_row(session, project_id: str, user_id: str, payload: dict) -> ProcessedDomain:
    domain = str(payload.get("domain") or "").strip()
    if not domain:
        raise RuntimeError("domain is required")
    now = datetime.now(timezone.utc)
    row = (
        session.query(ProcessedDomain)
        .filter(
            ProcessedDomain.project_id == project_id,
            ProcessedDomain.user_id == user_id,
            ProcessedDomain.domain == domain,
        )
        .first()
    )
    if row is None:
        row = ProcessedDomain(project_id=project_id, user_id=user_id, domain=domain)
        session.add(row)
        row.first_seen = payload.get("first_seen") or now.isoformat()
    row.last_checked = payload.get("last_checked") or now.isoformat()
    row.shopify_status = _prefer(row.shopify_status, payload.get("shopify_status"))
    row.discovery_source = _prefer(
        row.discovery_source,
        payload.get("discovery_source") or payload.get("source"),
    )
    row.updated_at = now
    return row


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


def assert_user_exists(user_id: str) -> str:
    """Cheap auth probe for the FastAPI JWT path. Streamlit still uses handle()."""
    url = database_url()
    if not url:
        raise RuntimeError("DATABASE_URL is not set")
    session = _session(url)
    try:
        user = _require_user(session, user_id)
        return user.id
    finally:
        session.close()


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
    tracking: list = []
    raw = row.tracking_technologies
    if raw:
        try:
            parsed = json.loads(raw) if isinstance(raw, str) else raw
            if isinstance(parsed, list):
                tracking = parsed
        except json.JSONDecodeError:
            tracking = []
    return {
        "id": row.id,
        "domain": row.domain,
        "store_name": row.store_name,
        "public_email": row.public_email,
        "country": row.country,
        "freshness_level": row.freshness_level,
        "freshness_score": row.freshness_score,
        "freshness_evidence": row.freshness_evidence or "",
        "source": row.source,
        "theme_name": row.theme_name or "",
        "theme_confidence": row.theme_confidence or "",
        "tracking_technologies": tracking,
        "first_seen": row.first_seen or "",
        "last_checked": row.last_checked or "",
    }


def _compare_project_stores(session, project_id: str, user_id: str, payload: dict) -> dict:
    from research.compare_stores import compare_stores

    domain_a = str(payload.get("domain_a") or payload.get("a") or "").strip()
    domain_b = str(payload.get("domain_b") or payload.get("b") or "").strip()
    if not domain_a or not domain_b:
        raise RuntimeError("domain_a and domain_b are required")
    if domain_a == domain_b:
        raise RuntimeError("Choose two different stores to compare")

    lead_a = (
        session.query(Lead)
        .filter(Lead.project_id == project_id, Lead.user_id == user_id, Lead.domain == domain_a)
        .first()
    )
    lead_b = (
        session.query(Lead)
        .filter(Lead.project_id == project_id, Lead.user_id == user_id, Lead.domain == domain_b)
        .first()
    )
    if lead_a is None or lead_b is None:
        missing = []
        if lead_a is None:
            missing.append(domain_a)
        if lead_b is None:
            missing.append(domain_b)
        raise RuntimeError("Store not found in project leads: " + ", ".join(missing))

    products_a = [
        _product_dict(row)
        for row in session.query(StoreProduct)
        .filter(
            StoreProduct.project_id == project_id,
            StoreProduct.user_id == user_id,
            StoreProduct.domain == domain_a,
        )
        .all()
    ]
    products_b = [
        _product_dict(row)
        for row in session.query(StoreProduct)
        .filter(
            StoreProduct.project_id == project_id,
            StoreProduct.user_id == user_id,
            StoreProduct.domain == domain_b,
        )
        .all()
    ]
    return compare_stores(_lead_dict(lead_a), _lead_dict(lead_b), products_a, products_b)


def _upsert_watchlist_item(session, project_id: str, user_id: str, payload: dict) -> WatchlistItem:
    domain = str(payload.get("domain") or "").strip()
    if not domain:
        raise RuntimeError("domain is required")
    now = datetime.now(timezone.utc)
    row = (
        session.query(WatchlistItem)
        .filter(
            WatchlistItem.project_id == project_id,
            WatchlistItem.user_id == user_id,
            WatchlistItem.domain == domain,
        )
        .first()
    )
    if row is None:
        row = WatchlistItem(project_id=project_id, user_id=user_id, domain=domain)
        session.add(row)
    note = payload.get("note")
    if note is not None:
        row.note = str(note).strip() or None
    row.updated_at = now
    return row


def _watchlist_dict(session, project_id: str, user_id: str, row: WatchlistItem) -> dict:
    lead = (
        session.query(Lead)
        .filter(Lead.project_id == project_id, Lead.user_id == user_id, Lead.domain == row.domain)
        .first()
    )
    product_count = (
        session.query(StoreProduct)
        .filter(
            StoreProduct.project_id == project_id,
            StoreProduct.user_id == user_id,
            StoreProduct.domain == row.domain,
        )
        .count()
    )
    return {
        "id": row.id,
        "domain": row.domain,
        "note": row.note or "",
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
        "lead": _lead_dict(lead) if lead else None,
        "product_count": int(product_count or 0),
    }


def _create_saved_filter(session, project_id: str, user_id: str, payload: dict) -> SavedFilter:
    name = " ".join(str(payload.get("name") or "").split())
    if len(name) < 1:
        raise RuntimeError("name is required")
    config = payload.get("config")
    if config is None:
        config = payload.get("settings") or {}
    if not isinstance(config, dict):
        raise RuntimeError("config must be an object")
    now = datetime.now(timezone.utc)
    row = SavedFilter(
        project_id=project_id,
        user_id=user_id,
        name=name,
        config_json=json.dumps(config, default=str),
        updated_at=now,
    )
    session.add(row)
    return row


def _filter_dict(row: SavedFilter) -> dict:
    try:
        config = json.loads(row.config_json or "{}")
    except json.JSONDecodeError:
        config = {}
    if not isinstance(config, dict):
        config = {}
    return {
        "id": row.id,
        "name": row.name,
        "config": config,
        "created_at": _iso(row.created_at),
        "updated_at": _iso(row.updated_at),
    }


def _rejected_dict(row: RejectedCandidate) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "store_name": row.store_name,
        "public_email": row.public_email,
        "reason": row.reason,
    }


def _product_dict(row: StoreProduct) -> dict:
    return {
        "id": row.id,
        "domain": row.domain,
        "shopify_product_id": row.shopify_product_id or "",
        "title": row.title,
        "handle": row.handle or "",
        "product_url": row.product_url or "",
        "price": row.price or "",
        "currency": row.currency or "",
        "image_url": row.image_url or "",
        "category": row.category or "",
    }


def _iso(value) -> str:
    if value is None:
        return ""
    return value.isoformat()


def _public_error(exc: Exception, url: str) -> str:
    text = str(exc).replace(url, "postgresql://***")
    if "EMAXCONNSESSION" in text or "max clients reached" in text.lower():
        return "The account database is busy. Wait a moment and try again."
    return text.split("\n")[0][:300]
