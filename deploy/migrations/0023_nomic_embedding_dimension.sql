-- Align the rebuildable retrieval vector space with nomic-embed-text:v1.5.
-- Existing vectors cannot be converted meaningfully. Chunks and their three
-- authority bridge projections are rebuildable from authoritative documents,
-- assertions, and evidence, so clear them without touching those authorities.
-- The projection worker will rebuild every document automatically.

DROP INDEX IF EXISTS document_chunk_embedding_idx;

TRUNCATE TABLE document_chunk CASCADE;

ALTER TABLE document_chunk
    ALTER COLUMN embedding TYPE vector(768)
    USING NULL::vector(768);

DELETE FROM retrieval_index_state;

CREATE INDEX document_chunk_embedding_idx
    ON document_chunk USING hnsw (embedding vector_cosine_ops);
