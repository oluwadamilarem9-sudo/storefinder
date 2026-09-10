Deploying StoreFinder backend to Render

Overview
- This guide shows how to deploy the FastAPI backend to Render using the existing `render.yaml` and a managed Postgres database.

Preconditions
- Your repository is connected to Render via GitHub (recommended).
- `render.yaml` is present in the repo root and defines `storefinder-backend` and `storefinder-db` (Postgres).

Environment variables (required)
- `DATABASE_URL` (provided by Render when you add the postgres service)
- `JWT_SECRET_KEY` (set to a long, random secret)
- `CORS_ALLOWED_ORIGINS` (comma-separated origins, e.g. `https://storefinder.streamlit.app`)
- Optional: `LOG_LEVEL`, `SENTRY_DSN`, other operational vars

Render deployment steps
1. In Render dashboard, create a new "Web Service" and connect your GitHub repo.
2. Choose the branch you want to deploy (e.g., `main`).
3. If using `render.yaml`, enable "Pull from `render.yaml`" so Render creates services from the manifest.
4. Add the managed Postgres service in Render (or let `render.yaml` create it).
5. After Postgres is created, Render provides a `DATABASE_URL` — attach it as the backend's environment variable.
6. Set `JWT_SECRET_KEY` and `CORS_ALLOWED_ORIGINS` in the backend service's Environment section.
7. Trigger a deploy. Monitor build logs for dependency installs and `uvicorn` server start on port 10000 (Render handles routing).

Testing after deploy
- Update your Streamlit `BACKEND_URL` to the backend's https URL (e.g., `https://storefinder-backend.onrender.com`).
- Sign up via the Streamlit UI and check the backend logs for the new user creation request.
- Verify that creating a project and running discovery stores leads on the backend (check Postgres tables).

Smoke test script
- After the backend is deployed you can run the included smoke test to validate basic API functionality:

```
python backend/smoke_test.py https://<your-backend-host>
```

It will create a temporary user and project and print the created project and project list.

Notes on persistence and discovery
- Streamlit runs must POST leads/rejected candidates to the backend endpoints so data is persisted in Postgres. Ensure `shopify-lead-finder/_sync_run_results_to_backend()` is configured to use `BACKEND_URL` and send Authorization header with the user's token.
- Do NOT rely on local SQLite files in Streamlit Cloud for production persistence.

Rollback and secrets
- Rotate `JWT_SECRET_KEY` only with care; existing tokens will be invalidated if you change it.
- Keep `JWT_SECRET_KEY` secret — use Render's encrypted environment variables.

If you'd like, I can prepare the Render environment variable values and a checklist for the exact values to paste into Render, or I can generate a one-line `curl` smoke test to run once you have the backend URL.
