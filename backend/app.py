from __future__ import annotations

from fastapi import Depends, FastAPI, HTTPException, Header, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, EmailStr
from sqlalchemy import create_engine, String
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker
from sqlalchemy import Column, DateTime, ForeignKey, Integer, JSON, Text, UniqueConstraint, func
from datetime import datetime, timedelta, timezone
import uuid

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
    return "sqlite:///./storefinder.db"


def create_app(db_path: str | None = None) -> FastAPI:
    app = FastAPI(title="StoreFinder Backend")
    engine = create_engine(get_db_url(db_path), connect_args={"check_same_thread": False})
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

        user = db.query(User).filter(User.email == token).first()
        if not user:
            raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")
        return user

    @app.post("/auth/signup")
    def signup(payload: SignupRequest, db: Session = Depends(get_db)):
        existing = db.query(User).filter(User.email == payload.email.lower()).first()
        if existing:
            raise HTTPException(status_code=400, detail="User already exists")

        user = User(
            name=payload.name,
            email=payload.email.lower(),
            password_hash=f"hashed:{payload.password}",
        )
        db.add(user)
        db.commit()
        db.refresh(user)
        return {"id": user.id, "token": user.email, "user": {"id": user.id, "name": user.name, "email": user.email}}

    @app.post("/auth/login")
    def login(payload: LoginRequest, db: Session = Depends(get_db)):
        user = db.query(User).filter(User.email == payload.email.lower()).first()
        if not user or user.password_hash != f"hashed:{payload.password}":
            raise HTTPException(status_code=401, detail="Invalid credentials")
        return {"token": user.email, "user": {"id": user.id, "name": user.name, "email": user.email}}

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
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")
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
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")
        runs = db.query(DiscoveryRun).filter(DiscoveryRun.project_id == project_id).all()
        return [{"id": run.id, "name": run.name, "status": run.status, "max_domains": run.max_domains} for run in runs]

    @app.post("/projects/{project_id}/leads")
    def add_lead(project_id: str, payload: LeadCreateRequest, current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")

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
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")

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
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")
        leads = db.query(Lead).filter(Lead.project_id == project_id).all()
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
        project = db.query(Project).filter(Project.id == project_id, Project.user_id == current_user.id).first()
        if not project:
            raise HTTPException(status_code=403, detail="Project not found or not owned by this user")
        rejected = db.query(RejectedCandidate).filter(RejectedCandidate.project_id == project_id).all()
        return [{
            "id": item.id,
            "domain": item.domain,
            "store_name": item.store_name,
            "public_email": item.public_email,
            "reason": item.reason,
        } for item in rejected]

    return app


app = create_app()
