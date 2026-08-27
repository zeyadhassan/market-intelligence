-- Preserve zero-result retrieval and graph probes in the compliance trail.
ALTER TABLE access_log ALTER COLUMN source_id DROP NOT NULL;
ALTER TABLE access_log ALTER COLUMN doc_id DROP NOT NULL;
ALTER TABLE access_log
    ADD COLUMN IF NOT EXISTS operation TEXT NOT NULL DEFAULT 'retrieval';
ALTER TABLE access_log
    ADD COLUMN IF NOT EXISTS result_count INT NOT NULL DEFAULT 1;

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM pg_constraint WHERE conname = 'access_log_result_count_nonnegative'
    ) THEN
        ALTER TABLE access_log ADD CONSTRAINT access_log_result_count_nonnegative
            CHECK (result_count >= 0);
    END IF;
END $$;
