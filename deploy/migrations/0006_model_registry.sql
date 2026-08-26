-- Governed model artifacts and append-only promotion history.

ALTER TABLE model_call_log
    ADD COLUMN IF NOT EXISTS gpu_seconds DOUBLE PRECISION
    CHECK (gpu_seconds IS NULL OR gpu_seconds >= 0.0);

CREATE TABLE IF NOT EXISTS model_release (
    release_id                 UUID PRIMARY KEY,
    component                  TEXT NOT NULL
                               CHECK (component IN ('extraction', 'reasoning', 'embedding', 'reranker')),
    model_id                   TEXT NOT NULL CHECK (length(model_id) > 0),
    artifact_digest            TEXT NOT NULL CHECK (artifact_digest ~ '^[0-9a-f]{64}$'),
    prompt_version             TEXT NOT NULL CHECK (length(prompt_version) > 0),
    schema_version             TEXT NOT NULL CHECK (length(schema_version) > 0),
    evaluation_dataset_digest  TEXT NOT NULL
                               CHECK (evaluation_dataset_digest ~ '^[0-9a-f]{64}$'),
    evaluation_report_digest   TEXT NOT NULL
                               CHECK (evaluation_report_digest ~ '^[0-9a-f]{64}$'),
    quality_gate_passed        BOOLEAN NOT NULL,
    evaluated_at               TIMESTAMPTZ NOT NULL,
    created_at                 TIMESTAMPTZ NOT NULL,
    created_by                 TEXT NOT NULL CHECK (length(created_by) > 0),
    CHECK (evaluated_at <= created_at)
);

CREATE TABLE IF NOT EXISTS model_release_transition (
    transition_id   UUID PRIMARY KEY,
    release_id      UUID NOT NULL REFERENCES model_release (release_id),
    from_state      TEXT CHECK (
        from_state IS NULL OR from_state IN
        ('candidate', 'shadow', 'canary', 'active', 'retired', 'rejected')
    ),
    to_state        TEXT NOT NULL CHECK (
        to_state IN ('candidate', 'shadow', 'canary', 'active', 'retired', 'rejected')
    ),
    rollout_percent INT NOT NULL CHECK (rollout_percent BETWEEN 0 AND 100),
    occurred_at     TIMESTAMPTZ NOT NULL,
    actor           TEXT NOT NULL CHECK (length(actor) > 0),
    reason          TEXT NOT NULL CHECK (length(reason) > 0),
    CHECK (
        (to_state = 'canary' AND rollout_percent BETWEEN 1 AND 50)
        OR (to_state = 'active' AND rollout_percent = 100)
        OR (to_state NOT IN ('canary', 'active') AND rollout_percent = 0)
    )
);

CREATE INDEX IF NOT EXISTS model_release_component_idx
    ON model_release (component, created_at DESC);
CREATE INDEX IF NOT EXISTS model_release_transition_latest_idx
    ON model_release_transition (release_id, occurred_at DESC, transition_id DESC);

CREATE OR REPLACE FUNCTION enforce_model_release_transition()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    artifact model_release%ROWTYPE;
    previous model_release_transition%ROWTYPE;
    conflict_exists BOOLEAN;
BEGIN
    SELECT * INTO artifact FROM model_release WHERE release_id = NEW.release_id FOR UPDATE;
    -- Serialize different releases of the same component as well as retries
    -- of one release, so singleton active/canary checks cannot race.
    PERFORM pg_advisory_xact_lock(hashtext('model-registry:' || artifact.component));
    SELECT * INTO previous
    FROM model_release_transition
    WHERE release_id = NEW.release_id
    ORDER BY occurred_at DESC, transition_id DESC
    LIMIT 1;

    IF NOT FOUND THEN
        IF NEW.from_state IS NOT NULL OR NEW.to_state <> 'candidate' THEN
            RAISE EXCEPTION 'first model release state must be candidate';
        END IF;
    ELSE
        IF NEW.from_state IS DISTINCT FROM previous.to_state THEN
            RAISE EXCEPTION 'model release transition has stale from_state';
        END IF;
        IF NEW.occurred_at <= previous.occurred_at THEN
            RAISE EXCEPTION 'model release transition time must increase';
        END IF;
        IF NOT (
            (previous.to_state = 'candidate' AND NEW.to_state IN ('shadow', 'rejected'))
            OR (previous.to_state = 'shadow' AND NEW.to_state IN ('canary', 'rejected'))
            OR (previous.to_state = 'canary' AND NEW.to_state IN ('active', 'shadow', 'rejected'))
            OR (previous.to_state = 'active' AND NEW.to_state = 'retired')
        ) THEN
            RAISE EXCEPTION 'model release transition is not allowed';
        END IF;
    END IF;

    IF NEW.to_state IN ('shadow', 'canary', 'active') AND NOT artifact.quality_gate_passed THEN
        RAISE EXCEPTION 'failed model evaluation cannot enter a serving workflow';
    END IF;

    IF NEW.to_state IN ('canary', 'active') THEN
        WITH latest AS (
            SELECT DISTINCT ON (t.release_id) t.release_id, t.to_state
            FROM model_release_transition t
            JOIN model_release r USING (release_id)
            WHERE r.component = artifact.component AND t.release_id <> NEW.release_id
            ORDER BY t.release_id, t.occurred_at DESC, t.transition_id DESC
        )
        SELECT EXISTS (
            SELECT 1 FROM latest WHERE to_state = NEW.to_state
        ) INTO conflict_exists;
        IF conflict_exists THEN
            RAISE EXCEPTION 'component already has a % release', NEW.to_state;
        END IF;
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS model_release_transition_guard ON model_release_transition;
CREATE TRIGGER model_release_transition_guard
BEFORE INSERT ON model_release_transition
FOR EACH ROW EXECUTE FUNCTION enforce_model_release_transition();

DROP TRIGGER IF EXISTS model_release_append_only ON model_release;
CREATE TRIGGER model_release_append_only
BEFORE UPDATE OR DELETE ON model_release
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

DROP TRIGGER IF EXISTS model_release_transition_append_only ON model_release_transition;
CREATE TRIGGER model_release_transition_append_only
BEFORE UPDATE OR DELETE ON model_release_transition
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
