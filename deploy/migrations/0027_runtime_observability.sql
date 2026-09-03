-- Durable, payload-safe runtime visibility for the operator control room.

CREATE TABLE IF NOT EXISTS runtime_worker_state_v1 (
    worker_id                 TEXT PRIMARY KEY,
    worker_type               TEXT NOT NULL CHECK (worker_type IN (
                                'scheduler','source','projection','analysis',
                                'search','delivery'
                              )),
    status                    TEXT NOT NULL CHECK (status IN (
                                'starting','working','idle','failed','stopped'
                              )),
    operation                 TEXT NOT NULL,
    loop_run_id               TEXT NOT NULL,
    process_started_at        TIMESTAMPTZ NOT NULL,
    iteration_started_at      TIMESTAMPTZ,
    iteration_finished_at     TIMESTAMPTZ,
    last_success_at           TIMESTAMPTZ,
    last_failure_at           TIMESTAMPTZ,
    safe_error_summary        TEXT,
    heartbeat_at              TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS runtime_worker_state_type_v1_idx
    ON runtime_worker_state_v1 (worker_type, heartbeat_at DESC);

CREATE TABLE IF NOT EXISTS runtime_event_v1 (
    event_id                  BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    category                  TEXT NOT NULL CHECK (category IN ('worker','model')),
    component                 TEXT NOT NULL,
    operation                 TEXT NOT NULL,
    status                    TEXT NOT NULL CHECK (status IN (
                                'started','working','idle','succeeded','failed',
                                'timed_out','refused','malformed','stopped'
                              )),
    correlation_id            UUID,
    worker_id                 TEXT,
    run_id                    TEXT,
    subject_digest            CHAR(64) CHECK (
                                subject_digest IS NULL OR
                                subject_digest ~ '^[0-9a-f]{64}$'
                              ),
    message                   TEXT NOT NULL,
    duration_ms               DOUBLE PRECISION CHECK (
                                duration_ms IS NULL OR duration_ms >= 0
                              ),
    safe_error_summary        TEXT,
    occurred_at               TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX IF NOT EXISTS runtime_event_recent_v1_idx
    ON runtime_event_v1 (occurred_at DESC, event_id DESC);
CREATE INDEX IF NOT EXISTS runtime_event_correlation_v1_idx
    ON runtime_event_v1 (correlation_id, occurred_at DESC, event_id DESC)
    WHERE correlation_id IS NOT NULL;

DROP TRIGGER IF EXISTS immutable_runtime_event_v1 ON runtime_event_v1;
CREATE TRIGGER immutable_runtime_event_v1
BEFORE UPDATE OR DELETE ON runtime_event_v1
FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation();
