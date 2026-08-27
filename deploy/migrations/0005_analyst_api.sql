-- Policy-scoped analyst operations. Apply after 0004_replayable_ingestion.sql.
-- Transaction and schema_migration bookkeeping are owned by the migration runner.

CREATE TABLE principal_access (
    assignment_id   UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    subject         TEXT NOT NULL UNIQUE CHECK (length(subject) > 0),
    principal_id    TEXT NOT NULL UNIQUE CHECK (length(principal_id) > 0),
    entitlement_group TEXT NOT NULL CHECK (length(entitlement_group) > 0),
    barrier_side    TEXT NOT NULL CHECK (barrier_side IN ('public', 'private')),
    desks           TEXT[] NOT NULL CHECK (
                        cardinality(desks) > 0
                        AND array_position(desks, '') IS NULL
                        AND array_position(desks, NULL) IS NULL
                    ),
    roles           TEXT[] NOT NULL CHECK (
                        cardinality(roles) > 0
                        AND array_position(roles, NULL) IS NULL
                        AND roles <@ ARRAY[
                            'analyst', 'reviewer', 'publisher', 'operator', 'admin'
                        ]::TEXT[]
                    ),
    purposes        TEXT[] NOT NULL CHECK (
                        cardinality(purposes) > 0
                        AND array_position(purposes, NULL) IS NULL
                        AND purposes <@ ARRAY[
                            'market_intelligence', 'operations', 'evaluation'
                        ]::TEXT[]
                    ),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    valid_from      TIMESTAMPTZ NOT NULL DEFAULT now(),
    valid_until     TIMESTAMPTZ,
    revoked_at      TIMESTAMPTZ,
    revoked_by      TEXT,
    revocation_reason TEXT,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_by      TEXT NOT NULL CHECK (length(created_by) > 0),
    updated_at      TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_by      TEXT NOT NULL CHECK (length(updated_by) > 0),
    CHECK (valid_until IS NULL OR valid_until > valid_from),
    CHECK (
        (revoked_at IS NULL AND revoked_by IS NULL AND revocation_reason IS NULL) OR
        (revoked_at IS NOT NULL AND revoked_by IS NOT NULL
         AND revocation_reason IS NOT NULL AND length(revoked_by) > 0
         AND length(revocation_reason) > 0)
    ),
    CHECK (updated_at >= created_at)
);
CREATE INDEX principal_access_active_subject_idx
    ON principal_access (subject) WHERE active AND revoked_at IS NULL;
CREATE INDEX principal_access_group_idx
    ON principal_access (entitlement_group) WHERE active AND revoked_at IS NULL;

CREATE TABLE analyst_desk (
    desk            TEXT PRIMARY KEY CHECK (length(desk) > 0),
    display_name    TEXT NOT NULL CHECK (length(display_name) > 0),
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    created_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX analyst_desk_policy_idx ON analyst_desk (policy_id) WHERE active;

CREATE TABLE analyst_entity_identifier (
    entity_identifier_id UUID PRIMARY KEY,
    entity_id       UUID NOT NULL REFERENCES entity_identity (entity_id),
    scheme          TEXT NOT NULL CHECK (scheme = lower(scheme) AND length(scheme) > 0),
    value           TEXT NOT NULL CHECK (length(value) > 0),
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    recorded_at     TIMESTAMPTZ NOT NULL,
    recorded_by     TEXT NOT NULL CHECK (length(recorded_by) > 0),
    UNIQUE (entity_id, scheme, value)
);
CREATE UNIQUE INDEX analyst_entity_identifier_active_value_idx
    ON analyst_entity_identifier (scheme, value) WHERE active;
CREATE INDEX analyst_entity_identifier_entity_idx
    ON analyst_entity_identifier (entity_id, scheme) WHERE active;

CREATE TABLE analyst_signal_desk (
    signal_id       UUID NOT NULL REFERENCES intelligence_signal (signal_id),
    desk            TEXT NOT NULL REFERENCES analyst_desk (desk),
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    assigned_at     TIMESTAMPTZ NOT NULL,
    assigned_by     TEXT NOT NULL CHECK (length(assigned_by) > 0),
    active          BOOLEAN NOT NULL DEFAULT TRUE,
    PRIMARY KEY (signal_id, desk)
);
CREATE INDEX analyst_signal_desk_inbox_idx
    ON analyst_signal_desk (desk, signal_id) WHERE active;
CREATE INDEX analyst_signal_desk_policy_idx
    ON analyst_signal_desk (policy_id) WHERE active;

CREATE TABLE analyst_signal_feedback (
    feedback_id     UUID PRIMARY KEY,
    signal_id       UUID NOT NULL REFERENCES intelligence_signal (signal_id),
    verdict         TEXT NOT NULL CHECK (verdict IN (
                        'approve', 'reject', 'needs_review', 'useful',
                        'not_useful', 'wrong_entity', 'stale', 'already_known',
                        'wrong_evidence', 'wrong_materiality'
                    )),
    reason          TEXT NOT NULL CHECK (length(reason) > 0),
    principal_id    TEXT NOT NULL CHECK (length(principal_id) > 0),
    recorded_at     TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id)
);
CREATE INDEX analyst_signal_feedback_latest_idx
    ON analyst_signal_feedback (signal_id, recorded_at DESC, feedback_id DESC);
CREATE INDEX analyst_signal_feedback_principal_idx
    ON analyst_signal_feedback (principal_id, recorded_at DESC);

CREATE TABLE analyst_review_decision (
    review_id       UUID PRIMARY KEY,
    subject_type    TEXT NOT NULL CHECK (subject_type IN (
                        'signal', 'claim_candidate', 'entity_link'
                    )),
    subject_id      UUID NOT NULL,
    decision        TEXT NOT NULL CHECK (decision IN ('approve', 'reject')),
    reason          TEXT NOT NULL CHECK (length(reason) > 0),
    decided_by      TEXT NOT NULL CHECK (length(decided_by) > 0),
    decided_at      TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id)
);
CREATE INDEX analyst_review_decision_subject_idx
    ON analyst_review_decision (subject_type, subject_id, decided_at DESC, review_id DESC);
CREATE INDEX analyst_review_decision_actor_idx
    ON analyst_review_decision (decided_by, decided_at DESC);

CREATE TABLE analyst_run (
    run_id          UUID PRIMARY KEY,
    run_type        TEXT NOT NULL CHECK (run_type IN (
                        'brief', 'research', 'projection', 'detection', 'evaluation'
                    )),
    desk            TEXT REFERENCES analyst_desk (desk),
    status          TEXT NOT NULL CHECK (status IN (
                        'queued', 'running', 'completed',
                        'completed_with_errors', 'failed'
                    )),
    requested_by    TEXT NOT NULL CHECK (length(requested_by) > 0),
    started_at      TIMESTAMPTZ NOT NULL,
    finished_at     TIMESTAMPTZ,
    counters        JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(counters) = 'object'),
    error_summary   TEXT,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (finished_at IS NULL OR finished_at >= started_at),
    CHECK ((status IN ('queued', 'running')) = (finished_at IS NULL))
);
CREATE INDEX analyst_run_status_idx ON analyst_run (status, started_at DESC);
CREATE INDEX analyst_run_desk_idx ON analyst_run (desk, started_at DESC);
CREATE INDEX analyst_run_policy_idx ON analyst_run (policy_id, started_at DESC);

CREATE TABLE analyst_brief_request (
    brief_id        UUID PRIMARY KEY,
    run_id          UUID NOT NULL UNIQUE REFERENCES analyst_run (run_id),
    desk            TEXT NOT NULL REFERENCES analyst_desk (desk),
    as_of           TIMESTAMPTZ NOT NULL,
    requested_by    TEXT NOT NULL CHECK (length(requested_by) > 0),
    requested_at    TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id)
);
CREATE INDEX analyst_brief_request_desk_idx
    ON analyst_brief_request (desk, requested_at DESC);
CREATE INDEX analyst_brief_request_policy_idx
    ON analyst_brief_request (policy_id, requested_at DESC);

CREATE TABLE analyst_brief_publication (
    publication_id  UUID PRIMARY KEY,
    brief_id        UUID NOT NULL REFERENCES analyst_brief_request (brief_id),
    html            TEXT NOT NULL CHECK (length(html) > 0),
    coverage_complete BOOLEAN NOT NULL,
    published_by    TEXT NOT NULL CHECK (length(published_by) > 0),
    published_at    TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id)
);
CREATE INDEX analyst_brief_publication_latest_idx
    ON analyst_brief_publication (brief_id, published_at DESC, publication_id DESC);

-- These tables carry policy_id on every sensitive row and supporting indexes,
-- so deployment can add role-specific RLS policies without changing data shape.
-- Application SQL also filters policy explicitly; it does not rely on table-owner
-- RLS bypass behavior.

CREATE OR REPLACE FUNCTION enforce_analyst_signal_desk_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    signal_policy_id UUID;
    desk_policy_id UUID;
BEGIN
    SELECT policy_id INTO STRICT signal_policy_id
    FROM intelligence_signal WHERE signal_id = NEW.signal_id;
    SELECT policy_id INTO STRICT desk_policy_id
    FROM analyst_desk WHERE desk = NEW.desk;
    PERFORM assert_policy_not_wider(NEW.policy_id, signal_policy_id);
    PERFORM assert_policy_not_wider(NEW.policy_id, desk_policy_id);
    RETURN NEW;
END $$;

CREATE TRIGGER analyst_signal_desk_policy_guard
BEFORE INSERT OR UPDATE ON analyst_signal_desk
FOR EACH ROW EXECUTE FUNCTION enforce_analyst_signal_desk_policy();

CREATE OR REPLACE FUNCTION enforce_analyst_run_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    desk_policy_id UUID;
BEGIN
    IF NEW.desk IS NOT NULL THEN
        SELECT policy_id INTO STRICT desk_policy_id
        FROM analyst_desk WHERE desk = NEW.desk;
        PERFORM assert_policy_not_wider(NEW.policy_id, desk_policy_id);
    END IF;
    RETURN NEW;
END $$;

CREATE TRIGGER analyst_run_policy_guard
BEFORE INSERT OR UPDATE ON analyst_run
FOR EACH ROW EXECUTE FUNCTION enforce_analyst_run_policy();

CREATE OR REPLACE FUNCTION enforce_analyst_review_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    subject_policy_id UUID;
BEGIN
    CASE NEW.subject_type
        WHEN 'signal' THEN
            SELECT policy_id INTO STRICT subject_policy_id
            FROM intelligence_signal WHERE signal_id = NEW.subject_id;
        WHEN 'claim_candidate' THEN
            SELECT policy_id INTO STRICT subject_policy_id
            FROM claim_candidate WHERE candidate_id = NEW.subject_id;
        WHEN 'entity_link' THEN
            SELECT policy_id INTO STRICT subject_policy_id
            FROM entity_link_decision WHERE decision_id = NEW.subject_id;
        ELSE
            RAISE EXCEPTION 'unsupported analyst review subject type';
    END CASE;
    PERFORM assert_policy_not_wider(NEW.policy_id, subject_policy_id);
    RETURN NEW;
END $$;

CREATE TRIGGER analyst_review_policy_guard
BEFORE INSERT ON analyst_review_decision
FOR EACH ROW EXECUTE FUNCTION enforce_analyst_review_policy();

CREATE OR REPLACE FUNCTION enforce_analyst_brief_request_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    run_policy_id UUID;
    desk_policy_id UUID;
BEGIN
    SELECT policy_id INTO STRICT run_policy_id
    FROM analyst_run WHERE run_id = NEW.run_id;
    SELECT policy_id INTO STRICT desk_policy_id
    FROM analyst_desk WHERE desk = NEW.desk;
    PERFORM assert_policy_not_wider(NEW.policy_id, run_policy_id);
    PERFORM assert_policy_not_wider(NEW.policy_id, desk_policy_id);
    RETURN NEW;
END $$;

CREATE TRIGGER analyst_brief_request_policy_guard
BEFORE INSERT ON analyst_brief_request
FOR EACH ROW EXECUTE FUNCTION enforce_analyst_brief_request_policy();

CREATE TRIGGER analyst_entity_identifier_policy_guard
BEFORE INSERT ON analyst_entity_identifier
FOR EACH ROW EXECUTE FUNCTION enforce_referenced_policy(
    'entity_id', 'entity_identity', 'entity_id'
);

CREATE TRIGGER analyst_feedback_policy_guard
BEFORE INSERT ON analyst_signal_feedback
FOR EACH ROW EXECUTE FUNCTION enforce_referenced_policy(
    'signal_id', 'intelligence_signal', 'signal_id'
);

CREATE TRIGGER analyst_brief_publication_policy_guard
BEFORE INSERT ON analyst_brief_publication
FOR EACH ROW EXECUTE FUNCTION enforce_referenced_policy(
    'brief_id', 'analyst_brief_request', 'brief_id'
);

CREATE OR REPLACE FUNCTION reject_analyst_history_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

CREATE TRIGGER analyst_signal_feedback_append_only
BEFORE UPDATE OR DELETE ON analyst_signal_feedback
FOR EACH ROW EXECUTE FUNCTION reject_analyst_history_mutation();

CREATE TRIGGER analyst_review_decision_append_only
BEFORE UPDATE OR DELETE ON analyst_review_decision
FOR EACH ROW EXECUTE FUNCTION reject_analyst_history_mutation();

CREATE TRIGGER analyst_brief_request_append_only
BEFORE UPDATE OR DELETE ON analyst_brief_request
FOR EACH ROW EXECUTE FUNCTION reject_analyst_history_mutation();

CREATE TRIGGER analyst_brief_publication_append_only
BEFORE UPDATE OR DELETE ON analyst_brief_publication
FOR EACH ROW EXECUTE FUNCTION reject_analyst_history_mutation();
