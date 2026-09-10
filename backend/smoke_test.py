"""Simple smoke test for StoreFinder backend.

Usage:
  python backend/smoke_test.py https://your-backend-url

This script will sign up a temporary user, log in, create a project, and list projects.
"""
import sys
import uuid
import httpx


def run(base_url: str):
    client = httpx.Client(base_url=base_url, timeout=10.0)
    email = f"smoketest+{uuid.uuid4().hex[:8]}@example.com"
    password = "ChangeMe123!"
    name = "Smoke Tester"

    print("Signing up...", email)
    r = client.post("/auth/signup", json={"name": name, "email": email, "password": password})
    r.raise_for_status()
    data = r.json()
    token = data.get("token")
    print("Signup token received.")

    headers = {"Authorization": f"Bearer {token}"}
    print("Creating project...")
    r = client.post("/projects", json={"name": "Smoke Project"}, headers=headers)
    r.raise_for_status()
    proj = r.json()
    print("Project created:", proj)

    print("Listing projects...")
    r = client.get("/projects", headers=headers)
    r.raise_for_status()
    print(r.json())


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python backend/smoke_test.py https://<backend-host>")
        raise SystemExit(2)
    run(sys.argv[1].rstrip("/"))
