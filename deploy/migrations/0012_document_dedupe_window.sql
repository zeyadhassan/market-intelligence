-- Bound near-duplicate seeding to an indexed per-source publication window.
CREATE INDEX IF NOT EXISTS document_source_published_at_idx
    ON document (source_id, published_at DESC);
