-- Align the rebuildable retrieval vector space with
-- nvidia/llama-3.2-nv-embedqa-1b-v2. Existing vectors cannot be converted
-- meaningfully, so clear only the rebuildable chunk/index projections. The
-- projection worker rebuilds them from authoritative documents. HNSW's
-- full-precision vector operator class is limited to 2,000 dimensions, so
-- retain full-precision storage and use a half-precision expression index.
DROP INDEX IF EXISTS document_chunk_embedding_idx;

TRUNCATE TABLE document_chunk CASCADE;

ALTER TABLE document_chunk
    ALTER COLUMN embedding TYPE vector(2048)
    USING NULL::vector(2048);

DELETE FROM retrieval_index_state;

CREATE INDEX document_chunk_embedding_idx
    ON document_chunk USING hnsw
    ((embedding::halfvec(2048)) halfvec_cosine_ops);
