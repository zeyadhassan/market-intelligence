-- Separate factual completeness from source uptime and register entailment models.

ALTER TABLE model_release DROP CONSTRAINT IF EXISTS model_release_component_check;
ALTER TABLE model_release
    ADD CONSTRAINT model_release_component_check
    CHECK (component IN (
        'extraction', 'reasoning', 'embedding', 'reranker', 'entailment'
    ));

CREATE TABLE IF NOT EXISTS factual_coverage_contract_v3 (
    contract_id             CHAR(64) PRIMARY KEY CHECK (contract_id ~ '^[0-9a-f]{64}$'),
    pattern_name            TEXT NOT NULL,
    entity_key              TEXT NOT NULL,
    subject_key             TEXT NOT NULL,
    required_source_ids     TEXT[] NOT NULL,
    source_classes          TEXT[] NOT NULL,
    window_start            TIMESTAMPTZ NOT NULL,
    window_end              TIMESTAMPTZ NOT NULL,
    state                   TEXT NOT NULL CHECK (state IN (
                                'complete', 'partial', 'delayed', 'dark',
                                'unauthorized', 'unknown'
                            )),
    reconciled_at           TIMESTAMPTZ NOT NULL,
    policy_version          TEXT NOT NULL,
    CHECK (window_end > window_start),
    CHECK (reconciled_at <= window_end),
    UNIQUE (pattern_name, entity_key, subject_key, window_start, policy_version)
);

CREATE INDEX IF NOT EXISTS factual_coverage_lookup_idx
    ON factual_coverage_contract_v3 (
        pattern_name, entity_key, subject_key, window_start, window_end
    );

CREATE TABLE IF NOT EXISTS outbox_handler_checkpoint_v3 (
    handler_name            TEXT NOT NULL,
    event_id                UUID NOT NULL REFERENCES transactional_outbox (event_id),
    payload_digest          CHAR(64) NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    completed_at            TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (handler_name, event_id)
);

CREATE TRIGGER immutable_factual_coverage_contract
BEFORE UPDATE OR DELETE ON factual_coverage_contract_v3
FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation();

CREATE TRIGGER immutable_outbox_handler_checkpoint
BEFORE UPDATE OR DELETE ON outbox_handler_checkpoint_v3
FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation();
