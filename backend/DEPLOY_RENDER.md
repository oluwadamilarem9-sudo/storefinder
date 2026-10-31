# Optional: FastAPI backend on Render

Path A for StoreFinder is the **Streamlit app + Render Postgres** defined in the repo-root [`render.yaml`](../render.yaml). See [`DEPLOY_RENDER.md`](../DEPLOY_RENDER.md).

This file is only if you also want a separate FastAPI service under `backend/`. That service is **not** created by the current Blueprint.

## If you add a backend service manually

Environment variables:

- `DATABASE_URL` — same Render Postgres as the Streamlit app (or its own DB)
- `JWT_SECRET_KEY` — long random secret (Render encrypted env)
- `CORS_ALLOWED_ORIGINS` — your Render Streamlit URL, e.g. `https://storefinder.onrender.com`

Smoke test:

```text
python backend/smoke_test.py https://<your-backend-host>
```

Do not paste production credentials into chat or commit them.
