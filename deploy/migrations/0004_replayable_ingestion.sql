-- Replayable ingestion control plane. Requires 0002_intelligence_ledger.sql.

CREATE TABLE IF NOT EXISTS ingest_run_v2 (
    run_id          UUID PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source_registry (source_id),
    status          TEXT NOT NULL CHECK (status IN (
                        'running', 'completed', 'completed_with_errors', 'failed'
                    )),
    requested_by    TEXT NOT NULL CHECK (length(requested_by) > 0),
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (
        (status = 'running' AND finished_at IS NULL) OR
        (status <> 'running' AND finished_at IS NOT NULL)
    )
);
CREATE INDEX IF NOT EXISTS ingest_run_source_idx
    ON ingest_run_v2 (source_id, started_at DESC);

CREATE TABLE IF NOT EXISTS ingest_job_v2 (
    job_id          UUID PRIMARY KEY,
    run_id          UUID NOT NULL REFERENCES ingest_run_v2 (run_id),
    raw_asset_id    UUID NOT NULL,
    source_id       TEXT NOT NULL REFERENCES source_registry (source_id),
    external_id     TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    content_hash    CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    media_type      TEXT NOT NULL,
    headers         JSONB NOT NULL DEFAULT '[]'::jsonb
                    CHECK (jsonb_typeof(headers) = 'array'),
    fetched_at      TIMESTAMPTZ NOT NULL,
    source_published_at TIMESTAMPTZ,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    status          TEXT NOT NULL CHECK (status IN (
                        'received', 'raw_archived', 'canonicalized',
                        'committed', 'not_novel', 'quarantined'
                    )),
    archive_uri     TEXT,
    result_document_version_id UUID
                    REFERENCES document_version (document_version_id),
    attempt         INTEGER NOT NULL CHECK (attempt > 0),
    started_at      TIMESTAMPTZ NOT NULL,
    updated_at      TIMESTAMPTZ NOT NULL CHECK (updated_at >= started_at),
    UNIQUE (run_id, raw_asset_id),
    CHECK (
        status IN ('received', 'quarantined') OR archive_uri IS NOT NULL
    ),
    CHECK (
        status NOT IN ('committed', 'not_novel') OR
        result_document_version_id IS NOT NULL
    )
);
CREATE INDEX IF NOT EXISTS ingest_job_run_idx
    ON ingest_job_v2 (run_id, started_at, job_id);
CREATE INDEX IF NOT EXISTS ingest_job_source_item_idx
    ON ingest_job_v2 (source_id, external_id, source_revision);

CREATE TABLE IF NOT EXISTS ingest_job_transition_v2 (
    transition_id   BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    job_id          UUID NOT NULL REFERENCES ingest_job_v2 (job_id),
    from_status     TEXT CHECK (from_status IN (
                        'received', 'raw_archived', 'canonicalized',
                        'committed', 'not_novel', 'quarantined'
                    )),
    to_status       TEXT NOT NULL CHECK (to_status IN (
                        'received', 'raw_archived', 'canonicalized',
                        'committed', 'not_novel', 'quarantined'
                    )),
    occurred_at     TIMESTAMPTZ NOT NULL,
    detail          TEXT NOT NULL DEFAULT '',
    UNIQUE (job_id, to_status, occurred_at),
    CHECK (from_status IS NULL OR from_status <> to_status)
);
CREATE INDEX IF NOT EXISTS ingest_job_transition_history_idx
    ON ingest_job_transition_v2 (job_id, occurred_at, transition_id);

CREATE TABLE IF NOT EXISTS ingest_watermark_v2 (
    source_id       TEXT NOT NULL REFERENCES source_registry (source_id),
    partition_key   TEXT NOT NULL DEFAULT 'default',
    position        TEXT NOT NULL CHECK (length(position) > 0),
    sequence_number BIGINT NOT NULL CHECK (sequence_number >= 0),
    observed_at     TIMESTAMPTZ NOT NULL,
    run_id          UUID NOT NULL REFERENCES ingest_run_v2 (run_id),
    job_id          UUID NOT NULL REFERENCES ingest_job_v2 (job_id),
    PRIMARY KEY (source_id, partition_key)
);

CREATE TABLE IF NOT EXISTS ingest_quarantine_v2 (
    quarantine_id   UUID PRIMARY KEY,
    job_id          UUID NOT NULL REFERENCES ingest_job_v2 (job_id),
    stage           TEXT NOT NULL CHECK (length(stage) > 0),
    error_type      TEXT NOT NULL CHECK (length(error_type) > 0),
    message         TEXT NOT NULL CHECK (length(message) > 0),
    retryable       BOOLEAN NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS ingest_quarantine_job_idx
    ON ingest_quarantine_v2 (job_id, recorded_at);

CREATE OR REPLACE FUNCTION protect_ingest_run_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.source_id, NEW.requested_by, NEW.started_at, NEW.policy_id)
       IS DISTINCT FROM
       ROW(OLD.source_id, OLD.requested_by, OLD.started_at, OLD.policy_id) THEN
        RAISE EXCEPTION 'ingest run identity is immutable';
    END IF;
    IF OLD.status <> 'running' AND ROW(NEW.status, NEW.finished_at)
       IS DISTINCT FROM ROW(OLD.status, OLD.finished_at) THEN
        RAISE EXCEPTION 'terminal ingest run cannot transition again';
    END IF;
    IF NEW.status <> 'running' AND NEW.finished_at <= NEW.started_at THEN
        RAISE EXCEPTION 'ingest run finish time must follow start time';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS ingest_run_update_guard ON ingest_run_v2;
CREATE TRIGGER ingest_run_update_guard
BEFORE UPDATE ON ingest_run_v2
FOR EACH ROW EXECUTE FUNCTION protect_ingest_run_update();

CREATE OR REPLACE FUNCTION protect_ingest_job_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        NEW.run_id, NEW.raw_asset_id, NEW.source_id, NEW.external_id,
        NEW.source_revision, NEW.content_hash, NEW.media_type, NEW.headers,
        NEW.fetched_at, NEW.source_published_at, NEW.policy_id,
        NEW.attempt, NEW.started_at
    ) IS DISTINCT FROM ROW(
        OLD.run_id, OLD.raw_asset_id, OLD.source_id, OLD.external_id,
        OLD.source_revision, OLD.content_hash, OLD.media_type, OLD.headers,
        OLD.fetched_at, OLD.source_published_at, OLD.policy_id,
        OLD.attempt, OLD.started_at
    ) THEN
        RAISE EXCEPTION 'ingest job identity and source envelope are immutable';
    END IF;
    IF NOT (
        (OLD.status = 'received' AND
            NEW.status IN ('raw_archived', 'quarantined')) OR
        (OLD.status = 'raw_archived' AND
            NEW.status IN ('canonicalized', 'quarantined')) OR
        (OLD.status = 'canonicalized' AND
            NEW.status IN ('committed', 'not_novel', 'quarantined'))
    ) THEN
        RAISE EXCEPTION 'invalid ingest job transition: % -> %', OLD.status, NEW.status;
    END IF;
    IF NEW.updated_at <= OLD.updated_at THEN
        RAISE EXCEPTION 'ingest job transition time must increase';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS ingest_job_update_guard ON ingest_job_v2;
CREATE TRIGGER ingest_job_update_guard
BEFORE UPDATE ON ingest_job_v2
FOR EACH ROW EXECUTE FUNCTION protect_ingest_job_update();

CREATE OR REPLACE FUNCTION enforce_ingest_job_run()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    source TEXT;
    policy UUID;
    run_status TEXT;
BEGIN
    SELECT source_id, policy_id, status INTO STRICT source, policy, run_status
    FROM ingest_run_v2 WHERE run_id = NEW.run_id;
    IF run_status <> 'running' THEN
        RAISE EXCEPTION 'new ingest job requires a running run';
    END IF;
    IF NEW.source_id <> source OR NEW.policy_id <> policy THEN
        RAISE EXCEPTION 'ingest job source or policy differs from its run';
    END IF;
    IF NEW.status <> 'received' THEN
        RAISE EXCEPTION 'new ingest job must start as received';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS ingest_job_run_guard ON ingest_job_v2;
CREATE TRIGGER ingest_job_run_guard
BEFORE INSERT ON ingest_job_v2
FOR EACH ROW EXECUTE FUNCTION enforce_ingest_job_run();

-- Updates with the same sequence are replay-safe only when the opaque source
-- position is identical. Lower sequence numbers can never move a cursor back.
CREATE OR REPLACE FUNCTION enforce_watermark_monotonicity()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.sequence_number < OLD.sequence_number THEN
        RAISE EXCEPTION 'ingest watermark cannot move backwards';
    END IF;
    IF NEW.sequence_number = OLD.sequence_number
       AND NEW.position <> OLD.position THEN
        RAISE EXCEPTION 'watermark sequence has conflicting position';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS ingest_watermark_guard ON ingest_watermark_v2;
CREATE TRIGGER ingest_watermark_guard
BEFORE UPDATE ON ingest_watermark_v2
FOR EACH ROW EXECUTE FUNCTION enforce_watermark_monotonicity();

DROP TRIGGER IF EXISTS ingest_job_transition_append_only
    ON ingest_job_transition_v2;
CREATE TRIGGER ingest_job_transition_append_only
BEFORE UPDATE OR DELETE ON ingest_job_transition_v2
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

DROP TRIGGER IF EXISTS ingest_quarantine_append_only ON ingest_quarantine_v2;
CREATE TRIGGER ingest_quarantine_append_only
BEFORE UPDATE OR DELETE ON ingest_quarantine_v2
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
