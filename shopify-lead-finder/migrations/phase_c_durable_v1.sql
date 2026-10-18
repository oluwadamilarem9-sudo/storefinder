-- Phase C durable storage migration (additive only).
-- Apply ONLY to a verified scratch database first.
-- Do NOT run against production until separate explicit approval after G0–G8.
-- Unique indexes must be created only after dedupe (see scripts/phase_c_dedupe.py).

-- Processed domains (per project)
CREATE TABLE IF NOT EXISTS processed_domains (
    id TEXT PRIMARY KEY,
    project_id TEXT NOT NULL REFERENCES projects(id),
    user_id TEXT NOT NULL REFERENCES users(id),
    domain TEXT NOT NULL,
    first_seen TEXT,
    last_checked TEXT,
    shopify_status TEXT,
    discovery_source TEXT,
    updated_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

-- Additive lead columns
ALTER TABLE leads ADD COLUMN IF NOT EXISTS freshness_evidence TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS shopify_status TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS contact_page TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS about_page TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS instagram TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS facebook TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS tiktok TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS linkedin TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS first_seen TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS last_checked TEXT;
ALTER TABLE leads ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- Additive rejected columns
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS freshness_level TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS freshness_score INTEGER;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS freshness_evidence TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS source TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS source_url TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS shopify_status TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS first_seen TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS last_checked TEXT;
ALTER TABLE rejected_candidates ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ;

-- Dedupe backup tables (filled by phase_c_dedupe.py before deleting duplicates)
CREATE TABLE IF NOT EXISTS leads_dedupe_backup (
    backup_id TEXT PRIMARY KEY,
    original_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT,
    domain TEXT NOT NULL,
    store_name TEXT,
    public_email TEXT,
    country TEXT,
    freshness_level TEXT,
    freshness_score INTEGER,
    source TEXT,
    created_at TIMESTAMPTZ,
    backed_up_at TIMESTAMPTZ DEFAULT NOW(),
    survivor_id TEXT,
    row_json TEXT
);

CREATE TABLE IF NOT EXISTS rejected_candidates_dedupe_backup (
    backup_id TEXT PRIMARY KEY,
    original_id TEXT NOT NULL,
    project_id TEXT NOT NULL,
    user_id TEXT NOT NULL,
    run_id TEXT,
    domain TEXT NOT NULL,
    store_name TEXT,
    public_email TEXT,
    reason TEXT,
    created_at TIMESTAMPTZ,
    backed_up_at TIMESTAMPTZ DEFAULT NOW(),
    survivor_id TEXT,
    row_json TEXT
);

-- Indexes that are safe before unique constraints
CREATE INDEX IF NOT EXISTS idx_leads_user_project ON leads (user_id, project_id);
CREATE INDEX IF NOT EXISTS idx_rejected_user_project ON rejected_candidates (user_id, project_id);
CREATE INDEX IF NOT EXISTS idx_rejected_project_reason ON rejected_candidates (project_id, reason);
CREATE INDEX IF NOT EXISTS idx_processed_user_project ON processed_domains (user_id, project_id);

-- UNIQUE indexes are intentionally NOT created here.
-- Run scripts/phase_c_dedupe.py --apply on scratch, verify reconciliation,
-- then run scripts/phase_c_add_unique_indexes.py on the same scratch target.
