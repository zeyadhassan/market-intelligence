CREATE TABLE IF NOT EXISTS document_entity_link (
    document_entity_link_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resolution_id    BIGINT NOT NULL UNIQUE REFERENCES entity_resolution (resolution_id),
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    lei              TEXT NOT NULL REFERENCES entity (lei),
    resolver         TEXT NOT NULL,
    score            DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    recorded_at      TIMESTAMPTZ NOT NULL
);

CREATE INDEX IF NOT EXISTS document_entity_link_lookup_idx
    ON document_entity_link (source_id, doc_id, lei, recorded_at);
