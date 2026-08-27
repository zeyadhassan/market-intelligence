-- Complete model-call outcome and immutable release lineage accounting.

ALTER TABLE model_call_log
    ADD COLUMN IF NOT EXISTS status TEXT NOT NULL DEFAULT 'succeeded'
    CHECK (status IN ('succeeded', 'failed', 'timed_out', 'refused', 'malformed'));
ALTER TABLE model_call_log ADD COLUMN IF NOT EXISTS error_type TEXT;
ALTER TABLE model_call_log ADD COLUMN IF NOT EXISTS release_id UUID;
ALTER TABLE model_call_log ADD COLUMN IF NOT EXISTS artifact_digest CHAR(64);
ALTER TABLE model_call_log ADD COLUMN IF NOT EXISTS prompt_version TEXT;
ALTER TABLE model_call_log ADD COLUMN IF NOT EXISTS schema_version TEXT;

ALTER TABLE model_call_log DROP CONSTRAINT IF EXISTS model_call_artifact_digest_format;
ALTER TABLE model_call_log ADD CONSTRAINT model_call_artifact_digest_format
    CHECK (artifact_digest IS NULL OR artifact_digest ~ '^[0-9a-f]{64}$');

CREATE INDEX IF NOT EXISTS model_call_outcome_idx
    ON model_call_log (run_id, component, status, recorded_at);
