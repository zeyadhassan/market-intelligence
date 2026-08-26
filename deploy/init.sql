-- fi_intel evidence store. Postgres 16 + pgvector + pg_trgm.
-- Entitlement filtering is enforced here, in the data layer:
-- every retrieval query joins source_registry and filters by the caller's
-- entitlement group and barrier side. There is no prompt-level control.

CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;

-- ---------------------------------------------------------------------------
-- Source registry: what we are licensed to use, and who may see it.
-- ---------------------------------------------------------------------------
CREATE TABLE source_registry (
    source_id        TEXT PRIMARY KEY,
    display_name     TEXT NOT NULL,
    licence_group    TEXT NOT NULL,          -- entitlement group required
    barrier_side     TEXT NOT NULL DEFAULT 'public'
                     CHECK (barrier_side IN ('public', 'private')),
    licensed         BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at    TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- The synthetic corpus is a registered source like any other; tests and
-- demos ingest it through the same entitlement-checked path as vendors.
INSERT INTO source_registry (source_id, display_name, licence_group, barrier_side)
VALUES ('synthetic_wire', 'Synthetic wire (test corpus)', 'test', 'public'),
       ('synthetic_wire_private', 'Synthetic wire, private side (test corpus)',
        'test_private', 'private');

-- Open-web sources (fi_intel/sources/adapters/rss.py): freely published
-- government feeds, not licensed vendor content. licence_group is
-- deliberately distinct from any vendor group so the registry and audit
-- trail never conflates the two. They are disabled until explicitly placed
-- in a desk coverage universe; registration is not activation.
INSERT INTO source_registry (
    source_id, display_name, licence_group, barrier_side, licensed
)
VALUES ('sec_edgar_8k', 'SEC EDGAR - recent 8-K filings (open web)',
        'open_web_public', 'public', FALSE),
       ('fed_press_releases', 'Federal Reserve press releases (open web)',
        'open_web_public', 'public', FALSE);

-- Entitlement groups and the sources each may read. Retrieval joins this
-- table; there is no application-level bypass.
CREATE TABLE entitlement_grant (
    entitlement_group TEXT NOT NULL,
    source_id         TEXT NOT NULL REFERENCES source_registry (source_id),
    PRIMARY KEY (entitlement_group, source_id)
);

INSERT INTO entitlement_grant (entitlement_group, source_id) VALUES
    ('fi_gcc_public',  'synthetic_wire'),
    ('fi_gcc_private', 'synthetic_wire'),
    ('fi_gcc_private', 'synthetic_wire_private'),
    ('test',           'synthetic_wire'),
    ('test_private',   'synthetic_wire'),
    ('test_private',   'synthetic_wire_private'),
    ('open_web_public', 'sec_edgar_8k'),
    ('open_web_public', 'fed_press_releases');

-- ---------------------------------------------------------------------------
-- Canonical documents. No vendor field names may appear here.
-- ---------------------------------------------------------------------------
CREATE TABLE document (
    doc_id           TEXT NOT NULL,
    source_id        TEXT NOT NULL REFERENCES source_registry (source_id),
    content_hash     TEXT NOT NULL,
    title            TEXT NOT NULL,
    body             TEXT NOT NULL,
    language         TEXT NOT NULL DEFAULT 'en',
    document_class   TEXT NOT NULL,
    barrier_side     TEXT NOT NULL DEFAULT 'public'
                     CHECK (barrier_side IN ('public', 'private')),
    published_at     TIMESTAMPTZ NOT NULL,
    recorded_at      TIMESTAMPTZ NOT NULL,
    url              TEXT,
    mentioned_names  TEXT[] NOT NULL DEFAULT '{}',
    identifiers      JSONB NOT NULL DEFAULT '{}'::jsonb,
    metadata         JSONB NOT NULL DEFAULT '{}'::jsonb,
    PRIMARY KEY (source_id, doc_id),
    CHECK (recorded_at >= published_at)
);
-- Exact dedupe is idempotent on content hash within a source.
CREATE UNIQUE INDEX document_content_hash_key ON document (source_id, content_hash);
CREATE INDEX document_recorded_at_idx ON document (recorded_at);
CREATE INDEX document_title_trgm_idx ON document USING gin (title gin_trgm_ops);
CREATE INDEX document_identifiers_lei_idx ON document ((identifiers ->> 'lei'));

-- Near-duplicate linkage: same story carried by multiple wires.
-- The duplicate side has NO foreign key to document: a near-duplicate is
-- classified, not persisted, so there is no row to reference. Only the
-- canonical side is enforced.
CREATE TABLE document_duplicate (
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    canonical_source_id TEXT NOT NULL,
    canonical_doc_id TEXT NOT NULL,
    similarity       DOUBLE PRECISION NOT NULL CHECK (similarity BETWEEN 0 AND 1),
    detector         TEXT NOT NULL,          -- which dedupe pass made the call
    decided_at       TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, doc_id),
    FOREIGN KEY (canonical_source_id, canonical_doc_id)
        REFERENCES document (source_id, doc_id)
);

-- ---------------------------------------------------------------------------
-- Ingestion cursors: resumability without gap or duplicate.
-- ---------------------------------------------------------------------------
CREATE TABLE ingest_cursor (
    source_id        TEXT PRIMARY KEY REFERENCES source_registry (source_id),
    position         TEXT NOT NULL,
    updated_at       TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Chunks and embeddings for hybrid retrieval.
-- ---------------------------------------------------------------------------
CREATE TABLE document_chunk (
    chunk_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    chunk_index      INT NOT NULL,
    char_start       INT NOT NULL,
    char_end         INT NOT NULL,
    text             TEXT NOT NULL,
    search_vector    TSVECTOR GENERATED ALWAYS AS
                     (to_tsvector('simple', coalesce(text, ''))) STORED,
    embedding        vector(1024),
    -- Which embedder produced this row (mirrors EXTRACTOR_VERSION/
    -- PROMPT_VERSION's discipline of never caching model output without
    -- its version). Swapping embedders must be a tracked, deliberate
    -- re-index, not a silent mix of incomparable vector spaces.
    embed_model_version TEXT NOT NULL DEFAULT 'hashing-v1',
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (source_id, doc_id) REFERENCES document (source_id, doc_id),
    UNIQUE (source_id, doc_id, chunk_index),
    CHECK (char_end > char_start)
);
CREATE INDEX document_chunk_embedding_idx
    ON document_chunk USING hnsw (embedding vector_cosine_ops);
CREATE INDEX document_chunk_search_vector_idx
    ON document_chunk USING gin (search_vector);
CREATE INDEX document_chunk_embed_version_idx
    ON document_chunk (embed_model_version);

-- Exactly one completed, homogeneous retrieval index is active. Search
-- fails closed when its configured embedder/chunker does not match this row.
CREATE TABLE retrieval_index_state (
    index_name          TEXT PRIMARY KEY,
    embed_model_version TEXT NOT NULL,
    embedding_dim       INT NOT NULL CHECK (embedding_dim > 0),
    chunker_version     TEXT NOT NULL,
    status              TEXT NOT NULL CHECK (status IN ('building', 'ready', 'failed')),
    indexed_at          TIMESTAMPTZ NOT NULL
);

-- ---------------------------------------------------------------------------
-- Entity resolution. Every resolution records resolver and score;
-- borderline candidates queue for human review, never auto-merge.
-- ---------------------------------------------------------------------------
CREATE TABLE entity (
    entity_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    lei              TEXT UNIQUE,            -- primary key when known
    canonical_name   TEXT NOT NULL,
    jurisdiction     TEXT,
    sector           TEXT,
    created_at       TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- GLEIF parent/child hierarchy. Separate table so the hierarchy can be
-- reloaded from a new golden copy without touching resolution history.
CREATE TABLE entity_parent (
    child_lei        TEXT PRIMARY KEY REFERENCES entity (lei),
    parent_lei       TEXT NOT NULL
);

-- Resolutions reference the document the mention appeared in. No FK to
-- document: a mention can be resolved from a classified-but-not-persisted
-- near-duplicate, which has no document row. Provenance is still recorded
-- as (source_id, doc_id) text.
CREATE TABLE entity_resolution (
    resolution_id    BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    entity_id        BIGINT NOT NULL REFERENCES entity (entity_id),
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    mention_text     TEXT NOT NULL,
    resolver         TEXT NOT NULL,          -- e.g. 'exact_lei', 'blocked_fuzzy'
    score            DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    resolved_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Source-asserted identifiers remain on document; resolved identity is a
-- separate, provenanced, append-only link used by entity-scoped retrieval.
CREATE TABLE document_entity_link (
    document_entity_link_id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    resolution_id    BIGINT NOT NULL UNIQUE REFERENCES entity_resolution (resolution_id),
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    lei              TEXT NOT NULL REFERENCES entity (lei),
    resolver         TEXT NOT NULL,
    score            DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    recorded_at      TIMESTAMPTZ NOT NULL
);
CREATE INDEX document_entity_link_lookup_idx
    ON document_entity_link (source_id, doc_id, lei, recorded_at);

CREATE TABLE resolution_queue (
    queue_id         BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    mention_text     TEXT NOT NULL,
    candidate_entity_id BIGINT REFERENCES entity (entity_id),
    best_score       DOUBLE PRECISION CHECK (best_score BETWEEN 0 AND 1),
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'approved', 'rejected')),
    queued_at        TIMESTAMPTZ NOT NULL DEFAULT now(),
    decided_at       TIMESTAMPTZ
);

-- ---------------------------------------------------------------------------
-- Extraction review queue: out-of-vocabulary types are never
-- auto-admitted to the T-Box.
-- ---------------------------------------------------------------------------
CREATE TABLE proposed_type (
    proposal_id      BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    source_id        TEXT NOT NULL,
    doc_id           TEXT NOT NULL,
    proposed_name    TEXT NOT NULL,
    kind             TEXT NOT NULL CHECK (kind IN ('node', 'edge')),
    context_snippet  TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    status           TEXT NOT NULL DEFAULT 'pending'
                     CHECK (status IN ('pending', 'admitted', 'rejected')),
    proposed_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    FOREIGN KEY (source_id, doc_id) REFERENCES document (source_id, doc_id)
);

-- ---------------------------------------------------------------------------
-- Audit: every retrieval writes a row.
-- ---------------------------------------------------------------------------
CREATE TABLE access_log (
    access_id        BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           TEXT NOT NULL,
    principal        TEXT NOT NULL,
    entitlement_group TEXT NOT NULL,
    source_id        TEXT,
    doc_id           TEXT,
    operation        TEXT NOT NULL DEFAULT 'retrieval',
    result_count     INT NOT NULL DEFAULT 1 CHECK (result_count >= 0),
    accessed_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX access_log_run_idx ON access_log (run_id);

-- ---------------------------------------------------------------------------
-- LLM call accounting: cost/latency visibility for real extraction and
-- research model calls. NOT a compliance boundary like access_log above —
-- see fi_intel/governance/model_usage.py for why writes here are
-- best-effort rather than fail-closed.
-- ---------------------------------------------------------------------------
CREATE TABLE model_call_log (
    call_id          BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    run_id           TEXT NOT NULL,
    component        TEXT NOT NULL CHECK (component IN ('extract', 'research')),
    model            TEXT NOT NULL,
    input_tokens     INT NOT NULL,
    output_tokens    INT NOT NULL,
    cost_usd         DOUBLE PRECISION NOT NULL,
    latency_ms       DOUBLE PRECISION NOT NULL,
    subject_id       TEXT NOT NULL,          -- doc_id (extract) or signal_id (research)
    recorded_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX model_call_log_run_idx ON model_call_log (run_id);
