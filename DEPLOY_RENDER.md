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
| `DATABASE_URL` | From `storefinder-db` | Same DB as the worker. **New** Render Postgres — not Supabase |
| `JWT_SECRET_KEY` | Render `generateValue` | Required; API refuses the local dev fallback on Render |
| `START_DISCOVERY_WORKERS` | `0` | Prevents duplicate claimers alongside the worker service |
| `STOREFINDER_ENV` | `production` | Enables production secret checks |
| `ACCOUNT_DB_MAX_SESSIONS` | `4` | Per-process session cap for dedicated Postgres |
| `WEB_ORIGIN_HOST` | From `storefinder-web` host | Used for CORS if `CORS_ALLOWED_ORIGINS` is empty |
| `CORS_ALLOWED_ORIGINS` | Manual (`sync: false`) | Set to `https://<storefinder-web>.onrender.com` after first deploy (recommended) |

Auth: FastAPI accepts **JWT only**. Opaque user-id bearers are rejected. `/auth/device` is disabled unless `ENABLE_DEVICE_AUTH=1`. Streamlit continues to call `account_db.handle` with the user id and is unchanged.

### `storefinder-worker`

| Variable | Source | Notes |
|---|---|---|
| `DATABASE_URL` | From `storefinder-db` | **Must match** the API (same new DB) |
| `WORKER_SLOTS` | `2` | Threads inside one worker process |
| `ACCOUNT_DB_MAX_SESSIONS` | `4` | Separate process from the API; own session cap |
| `STOREFINDER_ENV` | `production` | Aligns with API |

### `storefinder-web`

| Variable | Source | Notes |
|---|---|---|
| `API_SERVICE_HOST` | From `storefinder-api` host | Used at build time |
| `NEXT_PUBLIC_API_URL` | Manual (`sync: false`) or build | Build injects `https://$API_SERVICE_HOST`; set the same URL in the dashboard so rebuilds stay correct |

Do **not** point these services at Supabase. Do **not** paste production credentials into the repo.

## Exact staged-deployment checklist (do not run until operator approval)

**Separate environment confirmation**

- [ ] Create a **new** Render Blueprint environment (do **not** Apply over Streamlit/Supabase).
- [ ] Confirm `storefinder-db` is a **new** Render Postgres (`basic-256mb`), not the existing Supabase project.
- [ ] Confirm Streamlit secrets / Supabase `DATABASE_URL` are **not** edited.
- [ ] Confirm API + worker both use `fromDatabase: storefinder-db` only.

**Pre-apply**

- [ ] `render.yaml` reviewed: API `START_DISCOVERY_WORKERS=0`, dedicated `storefinder-worker`, web `rootDir: web`.
- [ ] JWT: `JWT_SECRET_KEY` will be Render-generated (not the local fallback).
- [ ] Push the commit you intend to stage (operator decision). Do not deploy from this checklist alone.

**Apply**

- [ ] Render → **New** → **Blueprint** → this repo → Apply into the **new** environment.
- [ ] Wait until Postgres is healthy, then API `/health`, then worker running, then web.

**Post-apply env**

- [ ] `storefinder-api`: set `CORS_ALLOWED_ORIGINS=https://<storefinder-web-host>`.
- [ ] `storefinder-web`: set `NEXT_PUBLIC_API_URL=https://<storefinder-api-host>` and rebuild if the build-time URL was wrong.
- [ ] Confirm `ACCOUNT_DB_MAX_SESSIONS=4` on API and worker.

**Smoke (new empty DB)**

- [ ] Open web URL → **Sign up a new test user** (JWT issued; raw user id must not work as Bearer).
- [ ] Create a project → queue a short discovery run.
- [ ] Confirm worker claims the run (activity log updates) and leads/products appear.
- [ ] Open Research → watchlist / compare / saved filter once with sample data if present.
- [ ] Log out / second browser: confirm isolation (other user cannot see the project).

**Stop conditions**

- [ ] If smoke fails, leave Streamlit/Supabase traffic alone; fix or tear down only the **new** Render env.
- [ ] Do **not** migrate production data in this stage.

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
