# Deploy StoreFinder on Render (Path A)

Host the Streamlit app on **Render** with **Render Postgres**.  
You get a URL like `https://storefinder.onrender.com` — not `*.streamlit.app`.  
No Supabase and no Streamlit Community Cloud for this path.

## What the Blueprint creates

`render.yaml` defines:

| Resource | Name | Role |
|---|---|---|
| Web service | `storefinder` | Runs `streamlit run shopify-lead-finder/app.py` |
| Postgres | `storefinder-db` | Account DB (`DATABASE_URL` wired automatically) |

`DATABASE_URL` is injected by Render. Do not paste Supabase URLs into this service.

## Deploy steps

1. Push this repo to GitHub (`main`).
2. Open [https://dashboard.render.com](https://dashboard.render.com) and sign in (GitHub is fine).
3. **New** → **Blueprint**.
4. Select the `storefinder` repository and branch `main`.
5. Confirm it detects `render.yaml`, then **Apply**.
6. Wait until Postgres is ready and the web service finishes its first deploy.
7. Open the service URL Render shows (ends with `.onrender.com`).
8. Sign up a **new test user**, create a project, confirm the app works.

## After it works

- Share the Render URL with users.
- Then turn off / delete the old Streamlit Community Cloud app.
- Leave Supabase alone until you no longer need any data there (optional dump/restore is a separate task).

## Notes

- Free web services can sleep when idle (first load after idle may be slow).
- `storefinder-db` uses the `basic-256mb` plan (paid). Change the plan in `render.yaml` or the dashboard if you need a different size.
- Keep `DISCOVERY_DURABLE_STORE` unset so durable writes stay on the default `sqlite` path until Phase C is approved.
- Local `storefinder_test` is only for your PC — it is not this Render database.

## Optional FastAPI backend

The separate FastAPI app under `backend/` is **not** part of this Blueprint.  
Account storage for Path A goes through Streamlit → `account_db.py` → Render Postgres.
