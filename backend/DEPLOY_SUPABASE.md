Migrating StoreFinder DB to Supabase

Overview
- This guide helps you create a Supabase Postgres database and switch the backend to use it.
- It includes optional data migration steps (pg_dump/pg_restore) to move existing data from your current Postgres to Supabase.

1) Create a Supabase project
- Go to https://app.supabase.com and sign in.
- Create a new project and choose a name (e.g. `storefinder-prod`).
- Choose region and password (store the password securely).
- Wait for the project to be provisioned.

2) Get the connection string
- In your Supabase project, go to Settings → Database → Connection Pooling / Connection string.
- Copy the `Connection string` (it will be `postgresql://...` or `postgres://...`).
- Supabase may show Internal and External endpoints. Use the external one for connections from your laptop; use the internal only if your backend is in the same cloud region with a private network (not typical). Supabase connection strings are public-facing but gated by username/password.

3) (Optional) Migrate existing data from Render Postgres
- If you have data in another Postgres (Render), use `pg_dump` and `pg_restore`.

On your local machine (requires `pg_dump` and `pg_restore`/`psql`):

```bash
# Dump from source (Render) — use the external connection string for access
pg_dump --format=custom --no-owner --no-acl -h <source_host> -U <source_user> -d <source_db> -f storefinder.dump

# Restore into Supabase
pg_restore --no-owner --no-acl -h <supabase_host> -U <supabase_user> -d postgres --clean storefinder.dump
```

Notes:
- Supabase default database name for connection is usually `postgres` — use the connection string exactly as provided.
- You may need to set `PGPASSWORD` environment variable before running `pg_dump`/`pg_restore` to avoid interactive prompts:

```bash
export PGPASSWORD="<password>"
pg_dump ...
pg_restore ...
```

4) Update backend config (set `DATABASE_URL`)
- Use the Supabase connection string as the backend `DATABASE_URL`. Ensure it contains credentials and host.
- I added `scripts/render_setup.sh` to help set env vars on Render; run it and paste the Supabase `DATABASE_URL` when prompted.

Example value to paste into the script prompt or Render env:

```
postgresql://<user>:<password>@db.<project>.supabase.co:5432/postgres?sslmode=require
```

5) Update Streamlit secrets
- In Streamlit Cloud > App Settings > Secrets, set `BACKEND_URL` to your backend HTTPS URL and `JWT_SECRET_KEY` to the same secret you set on the backend.

6) Verify
- Trigger a deploy for the backend (Render) after updating `DATABASE_URL`.
- Run the smoke test:

```bash
python backend/smoke_test.py https://<your-backend-host>
```

- In the Streamlit UI sign up, create a project, run discovery and confirm leads flow.

7) Optional: Use Supabase Auth (advanced)
- Supabase includes an Auth system; switching the app to use Supabase Auth requires code changes in the backend and frontend. For now keep using the app's own JWT/auth and only use Supabase as Postgres storage.

If you want, I can:
- Generate a prepared `pg_dump`/`pg_restore` command for your existing Render DB credentials.
- Walk you through the Supabase UI while you click.
- Update `render_setup.sh` to accept a `--supabase` flag to fetch the connection string from a file.

Tell me which of these you'd like next.