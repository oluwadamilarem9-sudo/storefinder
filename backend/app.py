"""
StoreFinder HTTP API.

Phase 1B: routes delegate account business logic to
``services.account_service`` (same layer Streamlit/workers use).
JWT is issued for API clients; opaque user-id tokens still work so
Streamlit-style tokens are not rejected during the transition.
"""

from __future__ import annotations

import os
import sys
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path

import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr

JWT_SECRET = os.getenv("JWT_SECRET_KEY") or os.getenv("AUTH_SECRET") or "dev-secret-change-me-please-set-production-secret"
JWT_ALGORITHM = "HS256"
JWT_TTL_MINUTES = 60 * 24

security = HTTPBearer()

_LEAD_FINDER_ROOT = Path(__file__).resolve().parents[1] / "shopify-lead-finder"
if str(_LEAD_FINDER_ROOT) not in sys.path:
    sys.path.insert(0, str(_LEAD_FINDER_ROOT))


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class DeviceLoginRequest(BaseModel):
    device_id: str


class ProjectCreateRequest(BaseModel):
    name: str


class RunCreateRequest(BaseModel):
    name: str | None = None
    max_domains: int = 20
    on_page: bool = False
    settings: dict | None = None


class LeadCreateRequest(BaseModel):
    domain: str
    store_name: str | None = None
    public_email: str | None = None
    country: str | None = None
    freshness_level: str | None = None
    freshness_score: int | None = None
    source: str | None = None
    run_id: str | None = None


class RejectedCreateRequest(BaseModel):
    domain: str
    store_name: str | None = None
    public_email: str | None = None
    reason: str | None = None
    run_id: str | None = None


def get_db_url(db_path: str | None = None) -> str:
    if db_path:
        return f"sqlite:///{Path(db_path).resolve().as_posix()}"
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    return "sqlite:///./storefinder.db"


def create_auth_token(*, user_id: str, email: str) -> str:
    payload = {
        "sub": user_id,
        "email": email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def _bind_account_database(db_path: str | None = None) -> None:
    """Point the shared account service at a local/test URL for this app instance."""
    from services import account_service

    url = get_db_url(db_path)
    os.environ["DATABASE_URL"] = url
    account_service.reset_account_db_engine()


def _service_call(path: str, method: str, payload: dict | None, token: str | None):
    from services import account_service

    try:
        return account_service.handle(path, method, payload, token)
    except RuntimeError as exc:
        message = str(exc)
        lowered = message.lower()
        if "sign in first" in lowered or "invalid credentials" in lowered:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail=message) from exc
        if "already exists" in lowered:
            raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc
        if "not owned" in lowered or "not found" in lowered:
            raise HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=message) from exc
        if "already running" in lowered:
            raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=message) from exc
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=message) from exc


def _should_start_discovery_workers(db_path: str | None, start_workers: bool | None) -> bool:
    """Queued Next.js runs need claim_next_run workers; tests with temp DBs do not."""
    if start_workers is not None:
        return start_workers
    if db_path is not None:
        return False
    flag = (os.getenv("START_DISCOVERY_WORKERS") or "1").strip().lower()
    return flag not in {"0", "false", "no", "off"}


def create_app(db_path: str | None = None, *, start_workers: bool | None = None) -> FastAPI:
    _bind_account_database(db_path)
    enable_workers = _should_start_discovery_workers(db_path, start_workers)

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        if enable_workers:
            # Keep the existing discovery_worker / pipeline path; just start it here
            # so queued SaaS runs are claimed without a separate worker.py process.
            os.environ.setdefault("WORKER_SLOTS", "2")
            from discovery_worker import start_worker_pool

            start_worker_pool()
        yield

    app = FastAPI(title="StoreFinder Backend", lifespan=lifespan)
    allowed_origins_env = os.getenv("CORS_ALLOWED_ORIGINS")
    if allowed_origins_env:
        origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
    else:
        origins = [
            "http://127.0.0.1:8501",
            "http://localhost:8501",
            "http://127.0.0.1:3000",
            "http://localhost:3000",
            "https://storefinder.streamlit.app",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def get_current_user_id(
        auth: HTTPAuthorizationCredentials = Depends(security),
    ) -> str:
        """Accept JWT (API) or opaque user-id token (Streamlit account_db style)."""
        token = (auth.credentials or "").strip()
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

        user_id: str | None = None
        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
            user_id = str(payload.get("sub") or "").strip() or None
        except jwt.PyJWTError:
            user_id = None

        if not user_id:
            # Opaque Streamlit-style token: raw users.id
            user_id = token

        # Prove the user exists / token is usable against the shared service.
        _service_call("/projects", "GET", None, user_id)
        return user_id

    @app.post("/auth/signup")
    def signup(payload: SignupRequest):
        result = _service_call(
            "/auth/signup",
            "POST",
            {"name": payload.name, "email": str(payload.email), "password": payload.password},
            None,
        )
        user = result["user"]
        return {
            "id": user["id"],
            "token": create_auth_token(user_id=user["id"], email=user["email"]),
            "user": user,
        }

    @app.post("/auth/login")
    def login(payload: LoginRequest):
        result = _service_call(
            "/auth/login",
            "POST",
            {"email": str(payload.email), "password": payload.password},
            None,
        )
        user = result["user"]
        return {
            "token": create_auth_token(user_id=user["id"], email=user["email"]),
            "user": user,
        }

    @app.post("/auth/device")
    def device_login(payload: DeviceLoginRequest):
        device_id = (payload.device_id or "").strip()
        if not device_id:
            raise HTTPException(status_code=400, detail="device_id is required")
        email = f"{device_id}@device.local"
        try:
            result = _service_call(
                "/auth/login",
                "POST",
                {"email": email, "password": device_id},
                None,
            )
        except HTTPException as exc:
            if exc.status_code != 401:
                raise
            result = _service_call(
                "/auth/signup",
                "POST",
                {"name": "Anonymous Device", "email": email, "password": device_id},
                None,
            )
        user = result["user"]
        return {
            "token": create_auth_token(user_id=user["id"], email=user["email"]),
            "user": user,
        }

    @app.get("/projects")
    def list_projects(user_id: str = Depends(get_current_user_id)):
        return _service_call("/projects", "GET", None, user_id)

    @app.post("/projects")
    def create_project(payload: ProjectCreateRequest, user_id: str = Depends(get_current_user_id)):
        return _service_call("/projects", "POST", {"name": payload.name}, user_id)

    @app.post("/projects/{project_id}/runs")
    def create_run(
        project_id: str,
        payload: RunCreateRequest,
        user_id: str = Depends(get_current_user_id),
    ):
        body = {
            "name": payload.name or f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            "max_domains": payload.max_domains,
            "on_page": payload.on_page,
            "settings": payload.settings
            if payload.settings is not None
            else {"max_domains": payload.max_domains},
        }
        return _service_call(f"/projects/{project_id}/runs", "POST", body, user_id)

    @app.get("/projects/{project_id}/runs")
    def list_runs(project_id: str, user_id: str = Depends(get_current_user_id)):
        return _service_call(f"/projects/{project_id}/runs", "GET", None, user_id)

    @app.get("/projects/{project_id}/runs/{run_id}")
    def get_run(project_id: str, run_id: str, user_id: str = Depends(get_current_user_id)):
        return _service_call(f"/projects/{project_id}/runs/{run_id}", "GET", None, user_id)

    @app.post("/projects/{project_id}/leads")
    def add_lead(
        project_id: str,
        payload: LeadCreateRequest,
        user_id: str = Depends(get_current_user_id),
    ):
        return _service_call(
            f"/projects/{project_id}/leads",
            "POST",
            payload.model_dump(),
            user_id,
        )

    @app.get("/projects/{project_id}/leads")
    def list_leads(project_id: str, user_id: str = Depends(get_current_user_id)):
        return _service_call(f"/projects/{project_id}/leads", "GET", None, user_id)

    @app.post("/projects/{project_id}/rejected")
    def add_rejected(
        project_id: str,
        payload: RejectedCreateRequest,
        user_id: str = Depends(get_current_user_id),
    ):
        return _service_call(
            f"/projects/{project_id}/rejected",
            "POST",
            payload.model_dump(),
            user_id,
        )

    @app.get("/projects/{project_id}/rejected")
    def list_rejected(project_id: str, user_id: str = Depends(get_current_user_id)):
        return _service_call(f"/projects/{project_id}/rejected", "GET", None, user_id)

    return app


app = create_app()
