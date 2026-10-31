"""
Phase 1B: FastAPI delegates to services.account_service (local SQLite only).
"""

from __future__ import annotations

import sys
from pathlib import Path

import jwt
from fastapi.testclient import TestClient

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "shopify-lead-finder"))

from backend.app import JWT_ALGORITHM, JWT_SECRET, create_app, create_auth_token  # noqa: E402


def _client(tmp_path):
    return TestClient(create_app(db_path=str(tmp_path / "phase1b.db")))


def _signup(client, email: str, name: str = "User"):
    response = client.post(
        "/auth/signup",
        json={"name": name, "email": email, "password": "secret123"},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_signup_login_issue_jwt_not_raw_user_id(tmp_path):
    client = _client(tmp_path)
    created = _signup(client, "jwt@example.com", "Jwt")
    token = created["token"]
    assert token != created["user"]["id"]
    payload = jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    assert payload["sub"] == created["user"]["id"]

    login = client.post(
        "/auth/login",
        json={"email": "jwt@example.com", "password": "secret123"},
    )
    assert login.status_code == 200
    assert login.json()["user"]["id"] == created["user"]["id"]


def test_opaque_user_id_token_still_accepted(tmp_path):
    """Streamlit-style bearer token (raw users.id) must keep working on the API."""
    client = _client(tmp_path)
    created = _signup(client, "opaque@example.com")
    user_id = created["user"]["id"]
    projects = client.get("/projects", headers={"Authorization": f"Bearer {user_id}"})
    assert projects.status_code == 200
    assert projects.json() == []


def test_device_login_creates_and_reuses_user(tmp_path):
    client = _client(tmp_path)
    first = client.post("/auth/device", json={"device_id": "device-abc"})
    assert first.status_code == 200, first.text
    second = client.post("/auth/device", json={"device_id": "device-abc"})
    assert second.status_code == 200
    assert first.json()["user"]["id"] == second.json()["user"]["id"]


def test_projects_crud_and_ownership(tmp_path):
    client = _client(tmp_path)
    alice = _signup(client, "alice@example.com", "Alice")
    bob = _signup(client, "bob@example.com", "Bob")
    token_a = alice["token"]
    token_b = bob["token"]

    created = client.post(
        "/projects",
        json={"name": "Alice Only"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert created.status_code == 200
    project_id = created.json()["id"]

    listed_a = client.get("/projects", headers={"Authorization": f"Bearer {token_a}"})
    assert len(listed_a.json()) == 1

    listed_b = client.get("/projects", headers={"Authorization": f"Bearer {token_b}"})
    assert listed_b.json() == []

    denied = client.get(
        f"/projects/{project_id}/leads",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert denied.status_code == 403


def test_runs_leads_rejected_endpoints_and_ownership(tmp_path):
    client = _client(tmp_path)
    alice = _signup(client, "runs@example.com", "Alice")
    bob = _signup(client, "runs-b@example.com", "Bob")
    token_a = alice["token"]
    token_b = bob["token"]

    project = client.post(
        "/projects",
        json={"name": "P"},
        headers={"Authorization": f"Bearer {token_a}"},
    ).json()
    project_id = project["id"]

    run = client.post(
        f"/projects/{project_id}/runs",
        json={"name": "Cycle", "max_domains": 7},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert run.status_code == 200, run.text
    run_id = run.json()["id"]
    assert run.json()["status"] == "queued"

    runs = client.get(
        f"/projects/{project_id}/runs",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert runs.status_code == 200
    assert len(runs.json()) == 1
    assert runs.json()[0]["max_domains"] == 7

    detail = client.get(
        f"/projects/{project_id}/runs/{run_id}",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert detail.status_code == 200
    assert detail.json()["id"] == run_id
    assert "log" in detail.json()

    lead = client.post(
        f"/projects/{project_id}/leads",
        json={"domain": "shop.example", "store_name": "Shop", "run_id": run_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert lead.status_code == 200
    # Upsert: second post same domain stays one row
    client.post(
        f"/projects/{project_id}/leads",
        json={"domain": "shop.example", "public_email": "a@b.c", "run_id": run_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    leads = client.get(
        f"/projects/{project_id}/leads",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert len(leads.json()) == 1
    assert leads.json()[0]["public_email"] == "a@b.c"

    rejected = client.post(
        f"/projects/{project_id}/rejected",
        json={"domain": "bad.example", "reason": "password_page", "run_id": run_id},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert rejected.status_code == 200
    listed_rej = client.get(
        f"/projects/{project_id}/rejected",
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert len(listed_rej.json()) == 1

    assert (
        client.get(
            f"/projects/{project_id}/runs",
            headers={"Authorization": f"Bearer {token_b}"},
        ).status_code
        == 403
    )
    assert (
        client.post(
            f"/projects/{project_id}/leads",
            json={"domain": "x.example"},
            headers={"Authorization": f"Bearer {token_b}"},
        ).status_code
        == 403
    )
    assert (
        client.get(
            f"/projects/{project_id}/runs/{run_id}",
            headers={"Authorization": f"Bearer {token_b}"},
        ).status_code
        == 403
    )


def test_invalid_token_rejected(tmp_path):
    client = _client(tmp_path)
    response = client.get("/projects", headers={"Authorization": "Bearer not-a-real-token"})
    assert response.status_code == 401


def test_jwt_for_missing_user_rejected(tmp_path):
    client = _client(tmp_path)
    token = create_auth_token(user_id="missing-user-id", email="x@y.z")
    response = client.get("/projects", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
