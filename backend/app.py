from __future__ import annotations

import os
from datetime import datetime, timedelta, timezone
import uuid

import bcrypt
import jwt
from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, EmailStr
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, String, create_engine
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

JWT_SECRET = os.getenv("JWT_SECRET_KEY") or os.getenv("AUTH_SECRET") or "dev-secret-change-me-please-set-production-secret"
JWT_ALGORITHM = "HS256"
JWT_TTL_MINUTES = 60 * 24

Base = declarative_base()
security = HTTPBearer()


class User(Base):
    __tablename__ = "users"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    name = Column(String, nullable=False)
    email = Column(String, unique=True, nullable=False)
    password_hash = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    projects = relationship("Project", back_populates="owner")
    runs = relationship("DiscoveryRun", back_populates="owner")


class Project(Base):
    __tablename__ = "projects"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=False)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))

    owner = relationship("User", back_populates="projects")
    runs = relationship("DiscoveryRun", back_populates="project")
    leads = relationship("Lead", back_populates="project")


class DiscoveryRun(Base):
    __tablename__ = "discovery_runs"
    id = Column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    project_id = Column(String, ForeignKey("projects.id"), nullable=False)
    user_id = Column(String, ForeignKey("users.id"), nullable=False)
    name = Column(String, nullable=True)
    status = Column(String, default="queued", nullable=False)
    config_json = Column(JSON, default=dict)
    max_domains = Column(Integer, default=20)
    created_at = Column(DateTime, default=lambda: datetime.now(timezone.utc))
    started_at = Column(DateTime, nullable=True)
    completed_at = Column(DateTime, nullable=True)

    project = relationship("Project", back_populates="runs")
    owner = relationship("User", back_populates="runs")
    leads = relationship("Lead", back_populates="run")


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

    project = relationship("Project", back_populates="leads")
    run = relationship("DiscoveryRun", back_populates="leads")


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


class SignupRequest(BaseModel):
    name: str
    email: EmailStr
    password: str


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ProjectCreateRequest(BaseModel):
    name: str


class RunCreateRequest(BaseModel):
    name: str | None = None
    max_domains: int = 20


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
        return f"sqlite:///{db_path}"
    database_url = os.getenv("DATABASE_URL")
    if database_url:
        return database_url
    return "sqlite:///./storefinder.db"


def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt()).decode("utf-8")


def verify_password(password: str, password_hash: str) -> bool:
    try:
        return bcrypt.checkpw(password.encode("utf-8"), password_hash.encode("utf-8"))
    except (ValueError, TypeError):
        # Stored password hash may be missing or in an unexpected format.
        # Treat as authentication failure rather than crashing the app.
        return False


def create_auth_token(user: User) -> str:
    payload = {
        "sub": user.id,
        "email": user.email,
        "exp": datetime.now(timezone.utc) + timedelta(minutes=JWT_TTL_MINUTES),
    }
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="StoreFinder Backend")
    # Configure CORS from environment or sensible defaults for local and
    # Streamlit Community Cloud deployment. Set `CORS_ALLOWED_ORIGINS`
    # to a comma-separated list in production (e.g. https://storefinder.streamlit.app).
    allowed_origins_env = os.getenv("CORS_ALLOWED_ORIGINS")
    if allowed_origins_env:
        origins = [o.strip() for o in allowed_origins_env.split(",") if o.strip()]
    else:
        origins = [
            "http://127.0.0.1:8501",
            "http://localhost:8501",
            "https://storefinder.streamlit.app",
        ]
    app.add_middleware(
        CORSMiddleware,
        allow_origins=origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
    )
    database_url = get_db_url(db_path)
    # If using Postgres/Supabase ensure SSL is required when no explicit
    # sslmode is present. Accept both `postgresql://` and `postgres://`.
    if database_url.startswith(("postgresql://", "postgres://")):
        if "sslmode" not in database_url:
            if "?" in database_url:
                database_url = f"{database_url}&sslmode=require"
            else:
                database_url = f"{database_url}?sslmode=require"

    # For SQLite we need the special connect arg. For production engines
    # (Postgres, etc.) do not pass `check_same_thread`.
    if database_url.startswith("sqlite:"):
        engine = create_engine(database_url, connect_args={"check_same_thread": False})
    else:
        engine = create_engine(database_url, pool_pre_ping=True)
    Base.metadata.create_all(bind=engine)
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

    def get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def get_current_user(db: Session = Depends(get_db), auth: HTTPAuthorizationCredentials = Depends(security)) -> User:
        token = auth.credentials
        if not token:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Missing token")

        try:
            payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
        except jwt.PyJWTError as exc:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token") from exc

        user_id = payload.get("sub")
        if not user_id:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token missing subject")

        user = db.query(User).filter(User.id == user_id).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="User not found")
        return user

    def get_project_for_user(project_id: str, current_user: User, db: Session) -> Project:
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")
        return project

    @app.post("/auth/signup")
    def signup(payload: SignupRequest, db: Session = Depends(get_db)):
        existing = db.query(User).filter(User.email == payload.email.lower()).first()
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")

        user = User(
            name=payload.name,
            email=payload.email.lower(),
            password_hash=hash_password(payload.password),
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"id": user.id, "token": create_auth_token(user), "user": {"id": user.id, "name": user.name, "email": user.email}}

    @app.post("/auth/login")
    def login(payload: LoginRequest, db: Session = Depends(get_db)):
        user = db.query(User).filter(User.email == payload.email.lower()).first()
        if not user or not verify_password(payload.password, user.password_hash):
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"token": create_auth_token(user), "user": {"id": user.id, "name": user.name, "email": user.email}}

    @app.get("/projects")
    def list_projects(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        projects = db.query(Project).filter(Project.user_id == current_user.id).all()
        return [{"id": project.id, "name": project.name, "created_at": project.created_at.isoformat()} for project in projects]

    @app.post("/projects")
    def create_project(payload: ProjectCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = Project(user_id=current_user.id, name=payload.name)
        db.add(project)
        db.commit()
        db.refresh(project)
        return {"id": project.id, "name": project.name}

    @app.post("/projects/{project_id}/runs")
    def create_run(project_id: str, payload: RunCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = get_project_for_user(project_id, current_user, db)
        run = DiscoveryRun(
            project_id=project.id,
            user_id=current_user.id,
            name=payload.name or f"Run {datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M')}",
            max_domains=payload.max_domains,
            status="queued",
            config_json={"max_domains": payload.max_domains},
        )
        db.add(run)
        db.commit()
        db.refresh(run)
        return {"id": run.id, "name": run.name, "status": run.status}

    @app.get("/projects/{project_id}/runs")
    def list_runs(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = get_project_for_user(project_id, current_user, db)
        runs = db.query(DiscoveryRun).filter(DiscoveryRun.project_id == project.id, DiscoveryRun.user_id == current_user.id).all()
        return [{"id": run.id, "name": run.name, "status": run.status, "max_domains": run.max_domains} for run in runs]

    @app.post("/projects/{project_id}/leads")
    def add_lead(project_id: str, payload: LeadCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = get_project_for_user(project_id, current_user, db)

        lead = Lead(
            project_id=project.id,
            user_id=current_user.id,
            run_id=payload.run_id,
            domain=payload.domain,
            store_name=payload.store_name,
            public_email=payload.public_email,
            country=payload.country,
            freshness_level=payload.freshness_level,
            freshness_score=payload.freshness_score,
            source=payload.source,
        )
        db.add(lead)
        db.commit()
        db.refresh(lead)
        return {"id": lead.id, "domain": lead.domain}

    @app.post("/projects/{project_id}/rejected")
    def add_rejected(project_id: str, payload: RejectedCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = get_project_for_user(project_id, current_user, db)

        rejected = RejectedCandidate(
            project_id=project.id,
            user_id=current_user.id,
            run_id=payload.run_id,
            domain=payload.domain,
            store_name=payload.store_name,
            public_email=payload.public_email,
            reason=payload.reason,
        )
        db.add(rejected)
        db.commit()
        db.refresh(rejected)
        return {"id": rejected.id, "domain": rejected.domain}

    @app.get("/projects/{project_id}/leads")
    def list_leads(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = get_project_for_user(project_id, current_user, db)
        leads = db.query(Lead).filter(Lead.project_id == project.id, Lead.user_id == current_user.id).all()
        return [{
            "id": lead.id,
            "domain": lead.domain,
            "store_name": lead.store_name,
            "public_email": lead.public_email,
            "country": lead.country,
            "freshness_level": lead.freshness_level,
            "freshness_score": lead.freshness_score,
            "source": lead.source,
        } for lead in leads]

    @app.get("/projects/{project_id}/rejected")
    def list_rejected(project_id: str, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = get_project_for_user(project_id, current_user, db)
        rejected = db.query(RejectedCandidate).filter(RejectedCandidate.project_id == project.id, RejectedCandidate.user_id == current_user.id).all()
        return [{
            "id": item.id,
            "domain": item.domain,
            "store_name": item.store_name,
            "public_email": item.public_email,
            "reason": item.reason,
        } for item in rejected]

    return app


app = create_app()
