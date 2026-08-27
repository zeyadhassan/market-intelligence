-- Bounded, versioned hybrid retrieval. Apply after 0002.

ALTER TABLE document_chunk
    ADD COLUMN IF NOT EXISTS embed_model_version TEXT NOT NULL DEFAULT 'hashing-v1';

ALTER TABLE document_chunk
    ADD COLUMN IF NOT EXISTS search_vector TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('simple', coalesce(text, ''))) STORED;

CREATE INDEX IF NOT EXISTS document_chunk_search_vector_idx
    ON document_chunk USING gin (search_vector);
CREATE INDEX IF NOT EXISTS document_chunk_embed_version_idx
    ON document_chunk (embed_model_version);
CREATE INDEX IF NOT EXISTS document_identifiers_lei_idx
    ON document ((identifiers ->> 'lei'));

CREATE TABLE IF NOT EXISTS retrieval_index_state (
    index_name          TEXT PRIMARY KEY,
    embed_model_version TEXT NOT NULL,
    embedding_dim       INT NOT NULL CHECK (embedding_dim > 0),
    chunker_version     TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed')),
    indexed_at          TIMESTAMPTZ NOT NULL
);

-- Do not infer an active version from legacy rows. An explicit re-index is
-- required before search can trust that model, dimension, and chunker match.
