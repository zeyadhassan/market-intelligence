-- Support bounded per-document model diagnostics on the Stage One page.

CREATE INDEX IF NOT EXISTS model_call_subject_idx
    ON model_call_log (subject_id, recorded_at DESC);
