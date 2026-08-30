-- Arabic/English lexical normalization aligned with the in-process ranker.

CREATE OR REPLACE FUNCTION normalize_retrieval_text_v1(value TEXT)
RETURNS TEXT
LANGUAGE sql
IMMUTABLE
PARALLEL SAFE
RETURNS NULL ON NULL INPUT
AS $$
    SELECT regexp_replace(
        translate(lower(value), 'أإآٱىـ', 'ااااي'),
        '[ؐ-ًؚ-ٰٟۖ-ۭ]',
        '',
        'g'
    )
$$;

ALTER TABLE document_chunk
    ADD COLUMN IF NOT EXISTS normalized_search_vector TSVECTOR
    GENERATED ALWAYS AS (
        to_tsvector('simple', normalize_retrieval_text_v1(coalesce(text, '')))
    ) STORED;

CREATE INDEX IF NOT EXISTS document_chunk_normalized_search_vector_idx
    ON document_chunk USING gin (normalized_search_vector);
