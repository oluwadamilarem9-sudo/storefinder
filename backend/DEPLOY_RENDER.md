# FastAPI on Render

The repo-root [`render.yaml`](../render.yaml) defines **`storefinder-api`** (this app), **`storefinder-worker`**, **`storefinder-web`**, and **`storefinder-db`**.

Full steps: [`DEPLOY_RENDER.md`](../DEPLOY_RENDER.md).  
Data move: [`docs/PRODUCTION_DATA_MIGRATION.md`](../docs/PRODUCTION_DATA_MIGRATION.md).

## API requirements

- Install: `pip install -r requirements.txt` (repo root — includes FastAPI + discovery + `psycopg2-binary`).
- Start: `python -m uvicorn backend.app:app --host 0.0.0.0 --port $PORT`
- Health: `GET /health`
- `START_DISCOVERY_WORKERS=0` on Render so only `worker.py` claims runs.
- `JWT_SECRET_KEY` must be set (Blueprint `generateValue`). The API refuses the local fallback when `RENDER` is set.
- Bearer auth is **JWT only** (opaque user-id tokens are rejected). Streamlit auth stays on `account_db.handle`.
- `ACCOUNT_DB_MAX_SESSIONS=4` on API and worker for the dedicated Render Postgres.

Smoke test (after deploy):

```text
python backend/smoke_test.py https://<storefinder-api-host>
```

Do not paste production credentials into chat or commit them.
