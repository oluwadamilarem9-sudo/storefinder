import sys
from pathlib import Path

from fastapi.testclient import TestClient

from backend.app import create_app

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "shopify-lead-finder"))


def test_project_scoped_storage_paths_are_separate():
    from config import project_storage_paths

    a = project_storage_paths(user_id="user-1", project_id="project-1")
    b = project_storage_paths(user_id="user-1", project_id="project-2")
    c = project_storage_paths(user_id="user-2", project_id="project-1")

    assert a["db"] != b["db"]
    assert a["db"] != c["db"]
    assert "user-1" in str(a["db"])
    assert "project-1" in str(a["db"])


def test_user_can_sign_up_and_create_project(tmp_path):
    db_path = tmp_path / "storefinder_test.db"
    client = TestClient(create_app(db_path=str(db_path)))

    response = client.post(
        "/auth/signup",
        json={"name": "Alice", "email": "alice@example.com", "password": "secret123"},
    )
    assert response.status_code == 200, response.text
    token = response.json()["token"]
    assert token

    project = client.post(
        "/projects",
        json={"name": "Alice Project"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert project.status_code == 200, project.text
    project_id = project.json()["id"]
    assert project_id

    runs = client.get("/projects", headers={"Authorization": f"Bearer {token}"})
    assert runs.status_code == 200, runs.text
    assert len(runs.json()) == 1


def test_users_cannot_access_each_other_projects_and_leads(tmp_path):
    db_path = tmp_path / "storefinder_test.db"
    client = TestClient(create_app(db_path=str(db_path)))

    signup_a = client.post(
        "/auth/signup",
        json={"name": "Alice", "email": "alice@example.com", "password": "secret123"},
    )
    token_a = signup_a.json()["token"]

    signup_b = client.post(
        "/auth/signup",
        json={"name": "Bob", "email": "bob@example.com", "password": "secret123"},
    )
    token_b = signup_b.json()["token"]

    project_a = client.post(
        "/projects",
        json={"name": "Alice Project"},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    project_id = project_a.json()["id"]

    run_a = client.post(
        f"/projects/{project_id}/runs",
        json={"name": "Run 1", "max_domains": 10},
        headers={"Authorization": f"Bearer {token_a}"},
    )
    run_id = run_a.json()["id"]

    lead = client.post(
        f"/projects/{project_id}/leads",
        json={
            "domain": "example.com",
            "store_name": "Example Store",
            "public_email": "hello@example.com",
            "country": "US",
            "freshness_level": "HIGH",
            "freshness_score": 9,
            "source": "demo",
            "run_id": run_id,
        },
        headers={"Authorization": f"Bearer {token_a}"},
    )
    assert lead.status_code == 200, lead.text

    list_for_b = client.get("/projects", headers={"Authorization": f"Bearer {token_b}"})
    assert list_for_b.status_code == 200, list_for_b.text
    assert len(list_for_b.json()) == 0

    leads_for_b = client.get(
        f"/projects/{project_id}/leads",
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert leads_for_b.status_code == 403, leads_for_b.text
