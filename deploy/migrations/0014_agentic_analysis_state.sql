-- Durable, append-only state for the unified analysis and investigation path.

CREATE TABLE IF NOT EXISTS analysis_run_v3 (
    run_id                  TEXT PRIMARY KEY,
    run_type               TEXT NOT NULL,
    mode                   TEXT NOT NULL CHECK (mode IN (
                               'fixture', 'live_poc', 'shadow', 'pilot', 'production'
                           )),
    principal_id           TEXT NOT NULL,
    authorization_scope    TEXT NOT NULL,
    policy_version         TEXT NOT NULL,
    temporal_pin           TIMESTAMPTZ NOT NULL,
    input_manifest_digest  CHAR(64) NOT NULL CHECK (input_manifest_digest ~ '^[0-9a-f]{64}$'),
    created_at             TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_run_transition_v3 (
    transition_id          CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    run_id                 TEXT NOT NULL REFERENCES analysis_run_v3 (run_id),
    state                  TEXT NOT NULL CHECK (state IN (
                               'queued', 'running', 'supported', 'contradicted',
                               'abstained', 'held', 'deferred', 'failed_retryable',
                               'failed_terminal', 'published'
                           )),
    safe_error_summary     TEXT,
    occurred_at            TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_scope_job_v3 (
    job_id                 CHAR(64) PRIMARY KEY CHECK (job_id ~ '^[0-9a-f]{64}$'),
    run_id                 TEXT NOT NULL REFERENCES analysis_run_v3 (run_id),
    topic_id               TEXT NOT NULL,
    scope_digest           CHAR(64) NOT NULL CHECK (scope_digest ~ '^[0-9a-f]{64}$'),
    idempotency_key        TEXT NOT NULL UNIQUE,
    created_at             TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS detector_execution_v3 (
    execution_id           CHAR(64) PRIMARY KEY CHECK (execution_id ~ '^[0-9a-f]{64}$'),
    job_id                 CHAR(64) NOT NULL REFERENCES analysis_scope_job_v3 (job_id),
    pattern_name           TEXT NOT NULL,
    pattern_version        TEXT NOT NULL,
    state                  TEXT NOT NULL,
    coverage_decision      JSONB NOT NULL,
    input_digest           CHAR(64) NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    output_digest          CHAR(64) NOT NULL CHECK (output_digest ~ '^[0-9a-f]{64}$'),
    started_at             TIMESTAMPTZ NOT NULL,
    finished_at            TIMESTAMPTZ
);

CREATE TABLE IF NOT EXISTS investigation_v3 (
    investigation_id       CHAR(64) PRIMARY KEY CHECK (investigation_id ~ '^[0-9a-f]{64}$'),
    run_id                 TEXT NOT NULL,
    signal_id              TEXT NOT NULL,
    policy_version         TEXT NOT NULL,
    started_at             TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id, signal_id, policy_version)
);

CREATE TABLE IF NOT EXISTS investigation_transition_v3 (
    transition_id          CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    investigation_id       CHAR(64) NOT NULL REFERENCES investigation_v3 (investigation_id),
    state                  TEXT NOT NULL,
    stop_reason            TEXT,
    missing_coverage       TEXT[] NOT NULL DEFAULT '{}',
    occurred_at            TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS investigation_step_v3 (
    step_id                CHAR(64) PRIMARY KEY CHECK (step_id ~ '^[0-9a-f]{64}$'),
    investigation_id       CHAR(64) NOT NULL REFERENCES investigation_v3 (investigation_id),
    sequence               INTEGER NOT NULL CHECK (sequence > 0),
    operation              TEXT NOT NULL,
    status                 TEXT NOT NULL,
    input_payload          JSONB NOT NULL,
    output_payload         JSONB NOT NULL,
    input_digest           CHAR(64) NOT NULL CHECK (input_digest ~ '^[0-9a-f]{64}$'),
    output_digest          CHAR(64) NOT NULL CHECK (output_digest ~ '^[0-9a-f]{64}$'),
    started_at             TIMESTAMPTZ NOT NULL,
    finished_at            TIMESTAMPTZ NOT NULL,
    duration_ms            DOUBLE PRECISION NOT NULL CHECK (duration_ms >= 0),
    error_type             TEXT,
    safe_error_summary     TEXT,
    UNIQUE (investigation_id, sequence)
);

CREATE TABLE IF NOT EXISTS validation_decision_v3 (
    decision_id            CHAR(64) PRIMARY KEY CHECK (decision_id ~ '^[0-9a-f]{64}$'),
    investigation_id       CHAR(64) NOT NULL REFERENCES investigation_v3 (investigation_id),
    claim_id               TEXT NOT NULL,
    validator_version      TEXT NOT NULL,
    status                 TEXT NOT NULL,
    field_evidence         JSONB NOT NULL,
    reasons                TEXT[] NOT NULL DEFAULT '{}',
    decided_at             TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS result_version_v3 (
    result_version_id      CHAR(64) PRIMARY KEY CHECK (result_version_id ~ '^[0-9a-f]{64}$'),
    logical_result_id      CHAR(64) NOT NULL CHECK (logical_result_id ~ '^[0-9a-f]{64}$'),
    investigation_id       CHAR(64) NOT NULL REFERENCES investigation_v3 (investigation_id),
    output_hash            CHAR(64) NOT NULL CHECK (output_hash ~ '^[0-9a-f]{64}$'),
    manifest               JSONB NOT NULL,
    publication_state      TEXT NOT NULL,
    created_at             TIMESTAMPTZ NOT NULL,
    UNIQUE (logical_result_id, output_hash)
);

CREATE TABLE IF NOT EXISTS result_exposure_v3 (
    exposure_id            CHAR(64) PRIMARY KEY CHECK (exposure_id ~ '^[0-9a-f]{64}$'),
    result_version_id      CHAR(64) NOT NULL REFERENCES result_version_v3 (result_version_id),
    principal_id           TEXT NOT NULL,
    channel                TEXT NOT NULL,
    exposed_at             TIMESTAMPTZ NOT NULL,
    UNIQUE (result_version_id, principal_id, channel)
);

CREATE TABLE IF NOT EXISTS result_evaluation_v3 (
    evaluation_id          CHAR(64) PRIMARY KEY CHECK (evaluation_id ~ '^[0-9a-f]{64}$'),
    exposure_id            CHAR(64) NOT NULL REFERENCES result_exposure_v3 (exposure_id),
    verdict                TEXT NOT NULL,
    rationale              TEXT NOT NULL DEFAULT '',
    evaluator_id           TEXT NOT NULL,
    recorded_at            TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS analysis_dead_letter_v3 (
    dead_letter_id         CHAR(64) PRIMARY KEY CHECK (dead_letter_id ~ '^[0-9a-f]{64}$'),
    run_id                 TEXT NOT NULL,
    stage                  TEXT NOT NULL,
    subject_id             TEXT NOT NULL,
    retryable              BOOLEAN NOT NULL,
    attempt_count          INTEGER NOT NULL CHECK (attempt_count > 0),
    safe_error_summary     TEXT NOT NULL,
    payload_digest         CHAR(64) NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    quarantined_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id, stage, subject_id, payload_digest)
);

CREATE INDEX IF NOT EXISTS investigation_step_trace_idx
    ON investigation_step_v3 (investigation_id, sequence);
CREATE INDEX IF NOT EXISTS analysis_dead_letter_retry_idx
    ON analysis_dead_letter_v3 (retryable, quarantined_at);

-- Immutable records may be inserted again only with byte-equivalent content.
CREATE OR REPLACE FUNCTION reject_agentic_record_mutation()
RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% records are immutable', TG_TABLE_NAME;
END
$$;

DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'analysis_run_v3', 'analysis_run_transition_v3', 'analysis_scope_job_v3',
        'detector_execution_v3', 'investigation_v3', 'investigation_transition_v3',
        'investigation_step_v3', 'validation_decision_v3', 'result_version_v3',
        'result_exposure_v3', 'result_evaluation_v3', 'analysis_dead_letter_v3'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS immutable_agentic_record ON %I', table_name);
        EXECUTE format(
            'CREATE TRIGGER immutable_agentic_record BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation()',
            table_name
        );
    END LOOP;
END
$$;
