# Deploy StoreFinder on Render (Next.js + FastAPI + worker)

Target production stack:

| Resource | Name | Role |
|---|---|---|
| Postgres | `storefinder-db` | Shared account DB (`DATABASE_URL`) |
| Web | `storefinder-api` | FastAPI (`backend.app:app`). **No** embedded discovery workers |
| Worker | `storefinder-worker` | Always-on `shopify-lead-finder/worker.py` (claims queued runs) |
| Web | `storefinder-web` | Next.js UI (`web/`) |

Blueprint: repo-root [`render.yaml`](render.yaml).

This replaces the older Streamlit-only Path A Blueprint. **Do not Apply this file over a live Streamlit or Supabase production service.** Leave those alone until a deliberate cutover. Create a **new** Render Blueprint / environment when you are ready to deploy.

## Environment variables

### `storefinder-api`

| Variable | Source | Notes |
|---|---|---|
| `DATABASE_URL` | From `storefinder-db` | Same DB as the worker |
| `JWT_SECRET_KEY` | Render `generateValue` | Required; API refuses the local dev fallback on Render |
| `START_DISCOVERY_WORKERS` | `0` | Prevents duplicate claimers alongside the worker service |
| `WEB_ORIGIN_HOST` | From `storefinder-web` host | Used for CORS if `CORS_ALLOWED_ORIGINS` is empty |
| `CORS_ALLOWED_ORIGINS` | Manual (`sync: false`) | Set to `https://<storefinder-web>.onrender.com` after first deploy (recommended) |

### `storefinder-worker`

| Variable | Source | Notes |
|---|---|---|
| `DATABASE_URL` | From `storefinder-db` | **Must match** the API |
| `WORKER_SLOTS` | `2` | Threads inside one worker process |

### `storefinder-web`

| Variable | Source | Notes |
|---|---|---|
| `API_SERVICE_HOST` | From `storefinder-api` host | Used at build time |
| `NEXT_PUBLIC_API_URL` | Manual (`sync: false`) or build | Build injects `https://$API_SERVICE_HOST`; set the same URL in the dashboard so rebuilds stay correct |

Do **not** point these services at Supabase. Do **not** paste production credentials into the repo.

## Deploy steps (when you choose to deploy)

1. Push `main` only when you intend to deploy (operator decision).
2. Render Dashboard → **New** → **Blueprint** → select this repo → confirm `render.yaml` → **Apply** into a **new** environment.
3. Wait for Postgres, then API, worker, and web.
4. In `storefinder-api`, set `CORS_ALLOWED_ORIGINS` to the Next.js HTTPS origin.
5. In `storefinder-web`, confirm `NEXT_PUBLIC_API_URL` is the API HTTPS origin; rebuild web if needed.
6. Open the web URL → sign up a **new test user** → queue a short discovery run → confirm the worker claims it and leads appear.

## Local development (unchanged intent)

- API with SQLite + embedded workers (default when not on Render): see [`web/README.md`](web/README.md).
- Or set `START_DISCOVERY_WORKERS=0` and run `python shopify-lead-finder/worker.py` separately.
- Production Render always uses the dedicated worker.

## Data migration

See [`docs/PRODUCTION_DATA_MIGRATION.md`](docs/PRODUCTION_DATA_MIGRATION.md). **Do not migrate until that checklist is executed and signed off.**

## Rollback (deploy-level)

- Keep Streamlit/Supabase untouched until cutover verification passes.
- If the new stack fails, leave traffic on the old app; do not delete old services or databases as part of first deploy.
- Render service rollback / redeploy previous commit if the new Blueprint environment misbehaves.

## Notes

- API and worker use **starter** plans so discovery does not depend on a sleeping free web dyno.
- Keep `DISCOVERY_DURABLE_STORE` unset unless Phase C durable Postgres writes are approved.
- Local `project_data/` SQLite trees are per-process scratch; account truth is `DATABASE_URL`.
