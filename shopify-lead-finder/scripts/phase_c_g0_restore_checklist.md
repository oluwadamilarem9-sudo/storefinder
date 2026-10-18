# G0 — Backup and scratch restore checklist

**Do not mark G0 passed until every box has real evidence.**

## Who

- Backup + restore execution: product owner (or named designee)
- Verification sign-off: product owner
- Production migration later: product owner only (separate approval)

## Procedure

1. Take a Supabase backup **or** run `pg_dump --format=custom --no-owner --no-acl` against production (operator machine; do not commit the dump or credentials).
2. Create a **separate** scratch database (new Supabase branch/project or local Postgres). Set `SCRATCH_DATABASE_URL` only — never point migration scripts at `DATABASE_URL`.
3. Restore the dump into the scratch database.
4. Verify row counts match production for: `users`, `projects`, `discovery_runs`, `leads`, `rejected_candidates`.
5. Spot-check three projects’ lead/rejected counts.
6. Record evidence (counts + date + who verified) before any Phase C mutate on that scratch URL.

## Then on scratch only

```text
python scripts/phase_c_inventory.py
python scripts/phase_c_apply_migration.py
python scripts/phase_c_dedupe.py
python scripts/phase_c_dedupe.py --apply
python scripts/phase_c_add_unique_indexes.py
python scripts/phase_c_backfill_processed.py
```

## Explicit exclusions

- Orphan local `project_data/.../leads.db` UUID trees: do not import
- Global CLI `shopify-lead-finder/leads.db`: do not import
- Preserve those files; do not delete or modify them
