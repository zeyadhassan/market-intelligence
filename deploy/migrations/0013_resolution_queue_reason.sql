-- Preserve the resolver's fail-closed rationale for operator review.
ALTER TABLE resolution_queue
    ADD COLUMN IF NOT EXISTS reason TEXT NOT NULL DEFAULT 'unspecified legacy queue reason';

ALTER TABLE resolution_queue ALTER COLUMN reason DROP DEFAULT;
