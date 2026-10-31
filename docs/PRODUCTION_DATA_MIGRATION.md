# Production data migration (account database)

**Status: documentation only. Do not run against production until explicitly approved.**

Goal: move existing account data (users, projects, runs, leads, rejected) onto Render Postgres used by FastAPI + `worker.py`, without losing rows.

Source of truth for SaaS data is the **account Postgres** (today: production Supabase or whatever currently backs Streamlit).  
Not migration sources: local `project_data/**/leads.db`, global CLI `leads.db`, or `output/*.csv`.

## Preconditions

- [ ] Operator approval to migrate (separate from deploy-prep).
- [ ] Scratch database created (**new** DB only). Set `SCRATCH_DATABASE_URL` only.
- [ ] Never point Phase C / restore scripts at live `DATABASE_URL` until scratch verification passes.
- [ ] Streamlit and Supabase left running and unchanged until cutover.

## 1. Backup production

On an operator machine (credentials stay local; do not commit dumps):

```bash
pg_dump --format=custom --no-owner --no-acl --file=storefinder_account_YYYYMMDD.dump "$PRODUCTION_DATABASE_URL"
```

Or take a provider console backup (Supabase/Render) and download it.

Record: dump filename, timestamp, who took it, approximate DB size.

## 2. Restore into scratch

```bash
# Create empty scratch DB first, then:
pg_restore --no-owner --no-acl --dbname="$SCRATCH_DATABASE_URL" storefinder_account_YYYYMMDD.dump
```

If the dump is SQL plain format, use `psql` instead of `pg_restore`.

## 3. Row-count verification (scratch vs production)

Compare at least:

| Table | Prod count | Scratch count | Match? |
|---|---|---|---|
| `users` | | | |
| `projects` | | | |
| `discovery_runs` | | | |
| `leads` | | | |
| `rejected_candidates` | | | |
| `processed_domains` (if present) | | | |

Spot-check three projects: lead count and rejected count each.

Optional inventory (scratch only):

```bash
set SCRATCH_DATABASE_URL=...
python shopify-lead-finder/scripts/phase_c_inventory.py
```

## 4. Application smoke on scratch (optional, pre-cutover)

Point a **non-production** API + worker at `SCRATCH_DATABASE_URL` (temporary env only).  
Sign in with a known user, open a project, confirm leads list. Do not use production JWT secrets in chat logs.

## 5. Cutover (only after approval)

1. Take a **fresh** production dump immediately before cutover.
2. Restore that dump into the Render `storefinder-db` (or promote a verified scratch copy).
3. Confirm API and worker `DATABASE_URL` both reference that Render database.
4. Keep Supabase/Streamlit read-only or idle until New stack is verified; do not delete them yet.
5. Re-check row counts on Render vs the fresh dump.

## 6. Rollback

If cutover fails:

1. Point users back to the previous app (Streamlit/Supabase) — it was never deleted.
2. Do not drop Supabase or overwrite it with a partial Render dump.
3. Keep the pre-cutover dump; restore Supabase from that dump only if something wrote to it during a failed experiment (normally it stays untouched).
4. Fix the Render environment separately; retry cutover only with a new verified dump.

## Explicit non-goals

- No Phase C durable-store mutations on production in this document.
- No deletion of Streamlit, Supabase, or old Render Streamlit services as part of migration prep.
- No import of orphan local SQLite project trees.
