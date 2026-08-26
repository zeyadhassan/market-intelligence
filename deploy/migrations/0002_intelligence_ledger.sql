-- Evidence-ledger foundation. Apply after deploy/init.sql.
--
-- This migration is additive: the current document and graph pipeline can run
-- while callers move to the versioned ledger. PostgreSQL owns immutable
-- evidence and lifecycle history; graph/search stores consume the outbox.

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS schema_migration (
    version         TEXT PRIMARY KEY,
    filename        TEXT NOT NULL,
    checksum        CHAR(64) NOT NULL CHECK (checksum ~ '^[0-9a-f]{64}$'),
    description     TEXT NOT NULL,
    applied_at      TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS access_policy (
    policy_id       UUID PRIMARY KEY,
    barrier_side    TEXT NOT NULL CHECK (barrier_side IN ('public', 'private')),
    allowed_entitlement_groups TEXT[] NOT NULL,
    semantic_key    CHAR(64) NOT NULL UNIQUE
                    CHECK (semantic_key ~ '^[0-9a-f]{64}$'),
    created_at      TIMESTAMPTZ NOT NULL
);

CREATE TABLE IF NOT EXISTS policy_lineage (
    derived_policy_id UUID NOT NULL REFERENCES access_policy (policy_id),
    input_policy_id UUID NOT NULL REFERENCES access_policy (policy_id),
    reason          TEXT NOT NULL CHECK (length(reason) > 0),
    recorded_at     TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (derived_policy_id, input_policy_id),
    CHECK (derived_policy_id <> input_policy_id)
);

CREATE TABLE IF NOT EXISTS raw_asset (
    raw_asset_id    UUID PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source_registry (source_id),
    external_id     TEXT NOT NULL,
    source_revision TEXT NOT NULL,
    object_uri      TEXT NOT NULL,
    content_hash    CHAR(64) NOT NULL CHECK (content_hash ~ '^[0-9a-f]{64}$'),
    media_type      TEXT NOT NULL,
    fetched_at      TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    metadata        JSONB NOT NULL DEFAULT '{}'::jsonb,
    UNIQUE (source_id, external_id, source_revision)
);
CREATE INDEX IF NOT EXISTS raw_asset_fetch_idx
    ON raw_asset (source_id, fetched_at DESC);

CREATE TABLE IF NOT EXISTS document_identity (
    document_id     UUID PRIMARY KEY,
    source_id       TEXT NOT NULL REFERENCES source_registry (source_id),
    external_id     TEXT NOT NULL,
    created_at      TIMESTAMPTZ NOT NULL,
    current_version_id UUID,
    UNIQUE (source_id, external_id)
);

CREATE TABLE IF NOT EXISTS document_version (
    document_version_id UUID PRIMARY KEY,
    document_id     UUID NOT NULL REFERENCES document_identity (document_id),
    raw_asset_id    UUID NOT NULL UNIQUE REFERENCES raw_asset (raw_asset_id),
    version_number  INTEGER NOT NULL CHECK (version_number > 0),
    source_revision TEXT NOT NULL,
    normalized_object_uri TEXT NOT NULL,
    normalized_text_hash CHAR(64) NOT NULL
                    CHECK (normalized_text_hash ~ '^[0-9a-f]{64}$'),
    title           TEXT NOT NULL CHECK (length(title) > 0),
    language        TEXT NOT NULL,
    document_class  TEXT NOT NULL,
    published_at    TIMESTAMPTZ NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL CHECK (recorded_at >= published_at),
    parser_version  TEXT NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    supersedes_version_id UUID,
    UNIQUE (document_id, version_number),
    UNIQUE (document_id, document_version_id),
    FOREIGN KEY (document_id, supersedes_version_id)
        REFERENCES document_version (document_id, document_version_id),
    CHECK (
        (version_number = 1 AND supersedes_version_id IS NULL) OR
        (version_number > 1 AND supersedes_version_id IS NOT NULL)
    ),
    CHECK (supersedes_version_id IS NULL OR
           supersedes_version_id <> document_version_id)
);
CREATE INDEX IF NOT EXISTS document_version_recorded_idx
    ON document_version (recorded_at DESC);
CREATE INDEX IF NOT EXISTS document_version_policy_idx
    ON document_version (policy_id, recorded_at DESC);

DO $$
BEGIN
    ALTER TABLE document_identity
        ADD CONSTRAINT document_identity_current_version_fk
        FOREIGN KEY (document_id, current_version_id)
        REFERENCES document_version (document_id, document_version_id)
        DEFERRABLE INITIALLY DEFERRED;
EXCEPTION
    WHEN duplicate_object THEN NULL;
END $$;

CREATE TABLE IF NOT EXISTS entity_identity (
    entity_id       UUID PRIMARY KEY,
    entity_type     TEXT NOT NULL CHECK (length(entity_type) > 0),
    canonical_name  TEXT NOT NULL CHECK (length(canonical_name) > 0),
    created_at      TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id)
);
CREATE INDEX IF NOT EXISTS entity_identity_name_idx
    ON entity_identity (lower(canonical_name));

CREATE TABLE IF NOT EXISTS mention (
    mention_id      UUID PRIMARY KEY,
    document_version_id UUID NOT NULL
                    REFERENCES document_version (document_version_id),
    kind            TEXT NOT NULL CHECK (kind IN (
                        'organization', 'person', 'instrument',
                        'jurisdiction', 'identifier', 'other'
                    )),
    surface         TEXT NOT NULL CHECK (length(surface) > 0),
    char_start      INTEGER NOT NULL CHECK (char_start >= 0),
    char_end        INTEGER NOT NULL,
    extractor_bundle_version TEXT NOT NULL,
    recorded_at     TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (char_end > char_start)
);
CREATE INDEX IF NOT EXISTS mention_document_idx
    ON mention (document_version_id, char_start);

CREATE TABLE IF NOT EXISTS entity_link_decision (
    decision_id     UUID PRIMARY KEY,
    mention_id      UUID NOT NULL REFERENCES mention (mention_id),
    status          TEXT NOT NULL CHECK (status IN (
                        'linked', 'review_required', 'abstained', 'rejected'
                    )),
    entity_id       UUID REFERENCES entity_identity (entity_id),
    candidate_entity_ids UUID[] NOT NULL DEFAULT '{}',
    confidence      DOUBLE PRECISION CHECK (confidence BETWEEN 0 AND 1),
    resolver_version TEXT NOT NULL,
    reason          TEXT NOT NULL CHECK (length(reason) > 0),
    decided_at      TIMESTAMPTZ NOT NULL,
    decided_by      TEXT NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (
        (status = 'linked' AND entity_id IS NOT NULL AND confidence IS NOT NULL) OR
        (status <> 'linked' AND entity_id IS NULL)
    )
);
CREATE INDEX IF NOT EXISTS entity_link_mention_idx
    ON entity_link_decision (mention_id, decided_at DESC);

CREATE TABLE IF NOT EXISTS evidence_span (
    evidence_span_id UUID PRIMARY KEY,
    document_version_id UUID NOT NULL
                    REFERENCES document_version (document_version_id),
    char_start      INTEGER NOT NULL CHECK (char_start >= 0),
    char_end        INTEGER NOT NULL,
    quote           TEXT NOT NULL CHECK (length(quote) > 0),
    quote_hash      CHAR(64) NOT NULL CHECK (quote_hash ~ '^[0-9a-f]{64}$'),
    page_number     INTEGER CHECK (page_number > 0),
    section         TEXT,
    recorded_at     TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (char_end > char_start),
    CHECK (quote_hash = encode(digest(convert_to(quote, 'UTF8'), 'sha256'), 'hex'))
);
CREATE INDEX IF NOT EXISTS evidence_span_document_idx
    ON evidence_span (document_version_id, char_start);

CREATE TABLE IF NOT EXISTS claim_candidate (
    candidate_id    UUID PRIMARY KEY,
    document_version_id UUID NOT NULL
                    REFERENCES document_version (document_version_id),
    subject_mention_id UUID NOT NULL REFERENCES mention (mention_id),
    predicate       TEXT NOT NULL CHECK (length(predicate) > 0),
    object_json     JSONB NOT NULL CHECK (jsonb_typeof(object_json) = 'object'),
    qualifiers      JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(qualifiers) = 'object'),
    event_time      TIMESTAMPTZ,
    valid_from      TIMESTAMPTZ,
    valid_to        TIMESTAMPTZ,
    extractor_bundle_version TEXT NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    recorded_at     TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (valid_to IS NULL OR valid_from IS NULL OR valid_to > valid_from)
);
CREATE INDEX IF NOT EXISTS claim_candidate_document_idx
    ON claim_candidate (document_version_id, predicate);

CREATE TABLE IF NOT EXISTS claim_candidate_evidence (
    candidate_id    UUID NOT NULL REFERENCES claim_candidate (candidate_id),
    evidence_span_id UUID NOT NULL REFERENCES evidence_span (evidence_span_id),
    PRIMARY KEY (candidate_id, evidence_span_id)
);

CREATE TABLE IF NOT EXISTS knowledge_assertion (
    assertion_id    UUID PRIMARY KEY,
    candidate_id    UUID NOT NULL UNIQUE REFERENCES claim_candidate (candidate_id),
    subject_entity_id UUID NOT NULL REFERENCES entity_identity (entity_id),
    predicate       TEXT NOT NULL CHECK (length(predicate) > 0),
    object_json     JSONB NOT NULL CHECK (jsonb_typeof(object_json) = 'object'),
    qualifiers      JSONB NOT NULL DEFAULT '{}'::jsonb
                    CHECK (jsonb_typeof(qualifiers) = 'object'),
    event_time      TIMESTAMPTZ,
    valid_from      TIMESTAMPTZ NOT NULL,
    valid_to        TIMESTAMPTZ,
    recorded_at     TIMESTAMPTZ NOT NULL,
    confidence      DOUBLE PRECISION NOT NULL CHECK (confidence BETWEEN 0 AND 1),
    ontology_version TEXT NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    supersedes_assertion_id UUID REFERENCES knowledge_assertion (assertion_id),
    CHECK (valid_to IS NULL OR valid_to > valid_from),
    CHECK (supersedes_assertion_id IS NULL OR
           supersedes_assertion_id <> assertion_id)
);
CREATE INDEX IF NOT EXISTS knowledge_assertion_subject_idx
    ON knowledge_assertion (subject_entity_id, predicate, valid_from DESC);
CREATE INDEX IF NOT EXISTS knowledge_assertion_recorded_idx
    ON knowledge_assertion (recorded_at DESC);

CREATE TABLE IF NOT EXISTS knowledge_assertion_evidence (
    assertion_id    UUID NOT NULL REFERENCES knowledge_assertion (assertion_id),
    evidence_span_id UUID NOT NULL REFERENCES evidence_span (evidence_span_id),
    PRIMARY KEY (assertion_id, evidence_span_id)
);

CREATE TABLE IF NOT EXISTS claim_decision (
    decision_id     UUID PRIMARY KEY,
    candidate_id    UUID NOT NULL REFERENCES claim_candidate (candidate_id),
    decision        TEXT NOT NULL CHECK (decision IN (
                        'accepted', 'rejected', 'review_required'
                    )),
    assertion_id    UUID REFERENCES knowledge_assertion (assertion_id),
    reasons         TEXT[] NOT NULL CHECK (cardinality(reasons) > 0),
    validator_bundle_version TEXT NOT NULL,
    decided_at      TIMESTAMPTZ NOT NULL,
    decided_by      TEXT NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (
        (decision = 'accepted' AND assertion_id IS NOT NULL) OR
        (decision <> 'accepted' AND assertion_id IS NULL)
    )
);
CREATE UNIQUE INDEX IF NOT EXISTS claim_decision_accepted_once_idx
    ON claim_decision (candidate_id) WHERE decision = 'accepted';
CREATE INDEX IF NOT EXISTS claim_decision_candidate_idx
    ON claim_decision (candidate_id, decided_at DESC);

CREATE TABLE IF NOT EXISTS intelligence_signal (
    signal_id       UUID PRIMARY KEY,
    pattern_id      TEXT NOT NULL,
    pattern_version TEXT NOT NULL,
    subject_entity_id UUID NOT NULL REFERENCES entity_identity (entity_id),
    scope_key       TEXT NOT NULL DEFAULT '',
    created_at      TIMESTAMPTZ NOT NULL,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    UNIQUE (pattern_id, pattern_version, subject_entity_id, scope_key)
);

CREATE TABLE IF NOT EXISTS signal_transition (
    transition_id   UUID PRIMARY KEY,
    signal_id       UUID NOT NULL REFERENCES intelligence_signal (signal_id),
    from_status     TEXT CHECK (from_status IN (
                        'candidate', 'confirmed', 'reviewed', 'published',
                        'suppressed', 'expired', 'withdrawn'
                    )),
    to_status       TEXT NOT NULL CHECK (to_status IN (
                        'candidate', 'confirmed', 'reviewed', 'published',
                        'suppressed', 'expired', 'withdrawn'
                    )),
    occurred_at     TIMESTAMPTZ NOT NULL,
    as_of           TIMESTAMPTZ NOT NULL,
    score           DOUBLE PRECISION CHECK (score BETWEEN 0 AND 1),
    reason          TEXT NOT NULL CHECK (length(reason) > 0),
    actor           TEXT NOT NULL CHECK (length(actor) > 0),
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (from_status IS NULL OR from_status <> to_status)
);
CREATE INDEX IF NOT EXISTS signal_transition_history_idx
    ON signal_transition (signal_id, occurred_at, transition_id);

CREATE TABLE IF NOT EXISTS signal_transition_assertion (
    transition_id   UUID NOT NULL REFERENCES signal_transition (transition_id),
    assertion_id    UUID NOT NULL REFERENCES knowledge_assertion (assertion_id),
    PRIMARY KEY (transition_id, assertion_id)
);

CREATE TABLE IF NOT EXISTS transactional_outbox (
    event_id        UUID PRIMARY KEY,
    event_type      TEXT NOT NULL
                    CHECK (event_type ~ '^[a-z][a-z0-9_.-]*\.v[1-9][0-9]*$'),
    aggregate_type  TEXT NOT NULL,
    aggregate_id    UUID NOT NULL,
    aggregate_version INTEGER NOT NULL CHECK (aggregate_version > 0),
    occurred_at     TIMESTAMPTZ NOT NULL,
    correlation_id  UUID NOT NULL,
    causation_id    UUID,
    policy_id       UUID NOT NULL REFERENCES access_policy (policy_id),
    payload         JSONB NOT NULL CHECK (jsonb_typeof(payload) = 'object'),
    published_at    TIMESTAMPTZ,
    publish_attempts INTEGER NOT NULL DEFAULT 0 CHECK (publish_attempts >= 0),
    last_error      TEXT,
    UNIQUE (aggregate_type, aggregate_id, aggregate_version, event_type)
);
CREATE INDEX IF NOT EXISTS transactional_outbox_pending_idx
    ON transactional_outbox (occurred_at, event_id) WHERE published_at IS NULL;

-- A derived row may only narrow the audience of its inputs. A different
-- policy ID also needs an explicit lineage edge, making classification changes
-- explainable rather than an application convention.
CREATE OR REPLACE FUNCTION assert_policy_not_wider(
    output_policy_id UUID,
    input_policy_id UUID
) RETURNS VOID LANGUAGE plpgsql AS $$
DECLARE
    output_policy access_policy%ROWTYPE;
    input_policy access_policy%ROWTYPE;
BEGIN
    SELECT p.* INTO STRICT output_policy FROM access_policy p
    WHERE p.policy_id = assert_policy_not_wider.output_policy_id;
    SELECT p.* INTO STRICT input_policy FROM access_policy p
    WHERE p.policy_id = assert_policy_not_wider.input_policy_id;

    IF NOT output_policy.allowed_entitlement_groups
           <@ input_policy.allowed_entitlement_groups THEN
        RAISE EXCEPTION 'derived policy widens its input audience';
    END IF;
    IF input_policy.barrier_side = 'private'
       AND output_policy.barrier_side <> 'private' THEN
        RAISE EXCEPTION 'derived policy crosses the private barrier';
    END IF;
    IF output_policy_id <> input_policy_id AND NOT EXISTS (
        SELECT 1 FROM policy_lineage lineage
        WHERE lineage.derived_policy_id = assert_policy_not_wider.output_policy_id
          AND lineage.input_policy_id = assert_policy_not_wider.input_policy_id
    ) THEN
        RAISE EXCEPTION 'derived policy is missing lineage';
    END IF;
END $$;

CREATE OR REPLACE FUNCTION enforce_referenced_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    referenced_id UUID;
    input_policy_id UUID;
BEGIN
    referenced_id := (to_jsonb(NEW) ->> TG_ARGV[0])::UUID;
    EXECUTE format(
        'SELECT policy_id FROM %I WHERE %I = $1', TG_ARGV[1], TG_ARGV[2]
    ) INTO STRICT input_policy_id USING referenced_id;
    PERFORM assert_policy_not_wider(NEW.policy_id, input_policy_id);
    RETURN NEW;
END $$;

DO $$
DECLARE
    trigger_spec TEXT[];
BEGIN
    FOREACH trigger_spec SLICE 1 IN ARRAY ARRAY[
        ARRAY['document_version', 'raw_asset_id', 'raw_asset', 'raw_asset_id'],
        ARRAY['mention', 'document_version_id', 'document_version', 'document_version_id'],
        ARRAY['evidence_span', 'document_version_id', 'document_version', 'document_version_id'],
        ARRAY['claim_candidate', 'document_version_id', 'document_version', 'document_version_id'],
        ARRAY['entity_link_decision', 'mention_id', 'mention', 'mention_id'],
        ARRAY['claim_decision', 'candidate_id', 'claim_candidate', 'candidate_id'],
        ARRAY['knowledge_assertion', 'candidate_id', 'claim_candidate', 'candidate_id'],
        ARRAY['signal_transition', 'signal_id', 'intelligence_signal', 'signal_id']
    ]
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS policy_derivation_guard ON %I', trigger_spec[1]
        );
        EXECUTE format(
            'CREATE TRIGGER policy_derivation_guard BEFORE INSERT ON %I '
            'FOR EACH ROW EXECUTE FUNCTION enforce_referenced_policy(%L, %L, %L)',
            trigger_spec[1], trigger_spec[2], trigger_spec[3], trigger_spec[4]
        );
    END LOOP;
END $$;

CREATE OR REPLACE FUNCTION enforce_candidate_evidence_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    output_policy_id UUID;
    input_policy_id UUID;
    candidate_document_id UUID;
    evidence_document_id UUID;
BEGIN
    SELECT policy_id, document_version_id
    INTO STRICT output_policy_id, candidate_document_id
    FROM claim_candidate WHERE candidate_id = NEW.candidate_id;
    SELECT policy_id, document_version_id
    INTO STRICT input_policy_id, evidence_document_id
    FROM evidence_span WHERE evidence_span_id = NEW.evidence_span_id;
    IF candidate_document_id <> evidence_document_id THEN
        RAISE EXCEPTION 'candidate evidence comes from another document version';
    END IF;
    PERFORM assert_policy_not_wider(output_policy_id, input_policy_id);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS claim_candidate_evidence_guard ON claim_candidate_evidence;
CREATE TRIGGER claim_candidate_evidence_guard
BEFORE INSERT ON claim_candidate_evidence
FOR EACH ROW EXECUTE FUNCTION enforce_candidate_evidence_policy();

CREATE OR REPLACE FUNCTION enforce_assertion_evidence_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    output_policy_id UUID;
    input_policy_id UUID;
BEGIN
    SELECT policy_id INTO STRICT output_policy_id
    FROM knowledge_assertion WHERE assertion_id = NEW.assertion_id;
    SELECT policy_id INTO STRICT input_policy_id
    FROM evidence_span WHERE evidence_span_id = NEW.evidence_span_id;
    PERFORM assert_policy_not_wider(output_policy_id, input_policy_id);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS knowledge_assertion_evidence_guard
    ON knowledge_assertion_evidence;
CREATE TRIGGER knowledge_assertion_evidence_guard
BEFORE INSERT ON knowledge_assertion_evidence
FOR EACH ROW EXECUTE FUNCTION enforce_assertion_evidence_policy();

CREATE OR REPLACE FUNCTION enforce_signal_assertion_policy()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    output_policy_id UUID;
    input_policy_id UUID;
BEGIN
    SELECT policy_id INTO STRICT output_policy_id
    FROM signal_transition WHERE transition_id = NEW.transition_id;
    SELECT policy_id INTO STRICT input_policy_id
    FROM knowledge_assertion WHERE assertion_id = NEW.assertion_id;
    PERFORM assert_policy_not_wider(output_policy_id, input_policy_id);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS signal_transition_assertion_guard
    ON signal_transition_assertion;
CREATE TRIGGER signal_transition_assertion_guard
BEFORE INSERT ON signal_transition_assertion
FOR EACH ROW EXECUTE FUNCTION enforce_signal_assertion_policy();

CREATE OR REPLACE FUNCTION enforce_entity_link_candidates()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM unnest(NEW.candidate_entity_ids) AS candidate(entity_id)
        LEFT JOIN entity_identity entity USING (entity_id)
        WHERE entity.entity_id IS NULL
    ) THEN
        RAISE EXCEPTION 'entity-link candidate does not exist';
    END IF;
    IF NEW.status = 'linked'
       AND cardinality(NEW.candidate_entity_ids) > 0
       AND NOT NEW.entity_id = ANY(NEW.candidate_entity_ids) THEN
        RAISE EXCEPTION 'linked entity is absent from candidate set';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS entity_link_candidate_guard ON entity_link_decision;
CREATE TRIGGER entity_link_candidate_guard
BEFORE INSERT ON entity_link_decision
FOR EACH ROW EXECUTE FUNCTION enforce_entity_link_candidates();

-- The current-version pointer is locked while a correction is appended. This
-- prevents skipped revisions and two concurrent corrections from both winning.
CREATE OR REPLACE FUNCTION enforce_document_version_chain()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    current_id UUID;
    current_number INTEGER;
    persisted JSONB;
BEGIN
    SELECT to_jsonb(v) INTO persisted FROM document_version v
    WHERE document_version_id = NEW.document_version_id;
    IF persisted IS NOT NULL THEN
        IF persisted IS DISTINCT FROM to_jsonb(NEW) THEN
            RAISE EXCEPTION 'document version ID has conflicting immutable content';
        END IF;
        RETURN NEW;
    END IF;

    SELECT d.current_version_id INTO STRICT current_id
    FROM document_identity d WHERE d.document_id = NEW.document_id FOR UPDATE;
    IF current_id IS NULL THEN
        IF NEW.version_number <> 1 OR NEW.supersedes_version_id IS NOT NULL THEN
            RAISE EXCEPTION 'new document must start at version 1';
        END IF;
        RETURN NEW;
    END IF;

    SELECT v.version_number INTO STRICT current_number
    FROM document_version v WHERE v.document_version_id = current_id;
    IF NEW.version_number <> current_number + 1
       OR NEW.supersedes_version_id <> current_id THEN
        RAISE EXCEPTION 'document correction does not supersede the current version';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS document_version_chain_guard ON document_version;
CREATE TRIGGER document_version_chain_guard
BEFORE INSERT ON document_version
FOR EACH ROW EXECUTE FUNCTION enforce_document_version_chain();

-- Accepted assertions must use the latest reviewed entity links. The model is
-- never allowed to mint or substitute canonical entity IDs.
CREATE OR REPLACE FUNCTION enforce_assertion_candidate()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    subject_mention_id UUID;
    candidate_predicate TEXT;
    candidate_object JSONB;
    link_status TEXT;
    resolved_entity_id UUID;
    object_mention_id UUID;
BEGIN
    SELECT c.subject_mention_id, c.predicate, c.object_json
    INTO STRICT subject_mention_id, candidate_predicate, candidate_object
    FROM claim_candidate c WHERE c.candidate_id = NEW.candidate_id;

    IF NEW.predicate <> candidate_predicate THEN
        RAISE EXCEPTION 'assertion predicate differs from its candidate';
    END IF;
    SELECT status, entity_id INTO link_status, resolved_entity_id
    FROM entity_link_decision
    WHERE mention_id = subject_mention_id
    ORDER BY decided_at DESC, decision_id DESC LIMIT 1;
    IF link_status IS DISTINCT FROM 'linked'
       OR resolved_entity_id IS DISTINCT FROM NEW.subject_entity_id THEN
        RAISE EXCEPTION 'assertion subject is not the resolved claim mention';
    END IF;

    IF candidate_object ->> 'kind' = 'entity_mention' THEN
        object_mention_id := (candidate_object ->> 'entity_mention_id')::UUID;
        SELECT status, entity_id INTO link_status, resolved_entity_id
        FROM entity_link_decision
        WHERE mention_id = object_mention_id
        ORDER BY decided_at DESC, decision_id DESC LIMIT 1;
        IF link_status IS DISTINCT FROM 'linked'
           OR NEW.object_json ->> 'kind' <> 'entity_mention'
           OR resolved_entity_id IS DISTINCT FROM
              (NEW.object_json ->> 'entity_id')::UUID THEN
            RAISE EXCEPTION 'assertion object is not the resolved claim mention';
        END IF;
    ELSIF NEW.object_json -> 'kind' IS DISTINCT FROM candidate_object -> 'kind'
       OR NEW.object_json -> 'value' IS DISTINCT FROM candidate_object -> 'value' THEN
        RAISE EXCEPTION 'assertion object differs from its candidate';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS knowledge_assertion_candidate_guard ON knowledge_assertion;
CREATE TRIGGER knowledge_assertion_candidate_guard
BEFORE INSERT ON knowledge_assertion
FOR EACH ROW EXECUTE FUNCTION enforce_assertion_candidate();

CREATE OR REPLACE FUNCTION enforce_claim_decision_assertion()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    assertion_candidate_id UUID;
BEGIN
    IF NEW.decision = 'accepted' THEN
        SELECT candidate_id INTO STRICT assertion_candidate_id
        FROM knowledge_assertion WHERE assertion_id = NEW.assertion_id;
        IF assertion_candidate_id <> NEW.candidate_id THEN
            RAISE EXCEPTION 'claim decision and assertion candidates differ';
        END IF;
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS claim_decision_assertion_guard ON claim_decision;
CREATE TRIGGER claim_decision_assertion_guard
BEFORE INSERT ON claim_decision
FOR EACH ROW EXECUTE FUNCTION enforce_claim_decision_assertion();

CREATE OR REPLACE FUNCTION require_candidate_evidence()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF NOT EXISTS (
        SELECT 1 FROM claim_candidate_evidence
        WHERE candidate_id = NEW.candidate_id
    ) THEN
        RAISE EXCEPTION 'claim candidate has no evidence';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS claim_candidate_evidence_required ON claim_candidate;
CREATE CONSTRAINT TRIGGER claim_candidate_evidence_required
AFTER INSERT ON claim_candidate
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_candidate_evidence();

CREATE OR REPLACE FUNCTION require_matching_assertion_evidence()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF EXISTS (
        (SELECT evidence_span_id FROM claim_candidate_evidence
         WHERE candidate_id = NEW.candidate_id
         EXCEPT
         SELECT evidence_span_id FROM knowledge_assertion_evidence
         WHERE assertion_id = NEW.assertion_id)
        UNION ALL
        (SELECT evidence_span_id FROM knowledge_assertion_evidence
         WHERE assertion_id = NEW.assertion_id
         EXCEPT
         SELECT evidence_span_id FROM claim_candidate_evidence
         WHERE candidate_id = NEW.candidate_id)
    ) THEN
        RAISE EXCEPTION 'assertion evidence differs from its candidate';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS knowledge_assertion_evidence_required
    ON knowledge_assertion;
CREATE CONSTRAINT TRIGGER knowledge_assertion_evidence_required
AFTER INSERT ON knowledge_assertion
DEFERRABLE INITIALLY DEFERRED
FOR EACH ROW EXECUTE FUNCTION require_matching_assertion_evidence();

-- ON CONFLICT is idempotent only when the repeated ID carries identical
-- immutable content. This guard prevents silent acceptance of ID collisions.
CREATE OR REPLACE FUNCTION enforce_uuid_insert_idempotency()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    persisted JSONB;
    key_value TEXT;
BEGIN
    key_value := to_jsonb(NEW) ->> TG_ARGV[0];
    EXECUTE format(
        'SELECT to_jsonb(t) FROM %I t WHERE %I::text = $1',
        TG_TABLE_NAME, TG_ARGV[0]
    ) INTO persisted USING key_value;
    IF persisted IS NOT NULL AND persisted IS DISTINCT FROM to_jsonb(NEW) THEN
        RAISE EXCEPTION '% ID has conflicting immutable content', TG_TABLE_NAME;
    END IF;
    RETURN NEW;
END $$;

DO $$
DECLARE
    trigger_spec TEXT[];
BEGIN
    FOREACH trigger_spec SLICE 1 IN ARRAY ARRAY[
        ARRAY['access_policy', 'policy_id'],
        ARRAY['raw_asset', 'raw_asset_id'],
        ARRAY['entity_identity', 'entity_id'],
        ARRAY['mention', 'mention_id'],
        ARRAY['entity_link_decision', 'decision_id'],
        ARRAY['evidence_span', 'evidence_span_id'],
        ARRAY['claim_candidate', 'candidate_id'],
        ARRAY['claim_decision', 'decision_id'],
        ARRAY['knowledge_assertion', 'assertion_id']
    ]
    LOOP
        EXECUTE format(
            'DROP TRIGGER IF EXISTS ledger_idempotent_insert ON %I', trigger_spec[1]
        );
        EXECUTE format(
            'CREATE TRIGGER ledger_idempotent_insert BEFORE INSERT ON %I '
            'FOR EACH ROW EXECUTE FUNCTION enforce_uuid_insert_idempotency(%L)',
            trigger_spec[1], trigger_spec[2]
        );
    END LOOP;
END $$;

-- Serialize transitions on the stable signal row and reject invalid lifecycle
-- changes even when a writer bypasses the Python repository.
CREATE OR REPLACE FUNCTION enforce_signal_transition()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    previous_status TEXT;
    previous_time TIMESTAMPTZ;
    persisted JSONB;
    signal_policy_id UUID;
BEGIN
    SELECT to_jsonb(t) INTO persisted FROM signal_transition t
    WHERE transition_id = NEW.transition_id;
    IF persisted IS NOT NULL THEN
        IF persisted IS DISTINCT FROM to_jsonb(NEW) THEN
            RAISE EXCEPTION 'signal transition ID has conflicting immutable content';
        END IF;
        RETURN NEW;
    END IF;

    SELECT policy_id INTO STRICT signal_policy_id FROM intelligence_signal
    WHERE signal_id = NEW.signal_id FOR UPDATE;
    IF NEW.policy_id <> signal_policy_id THEN
        RAISE EXCEPTION 'signal and transition policies differ';
    END IF;

    SELECT to_status, occurred_at INTO previous_status, previous_time
    FROM signal_transition
    WHERE signal_id = NEW.signal_id
    ORDER BY occurred_at DESC, transition_id DESC
    LIMIT 1;

    IF NEW.from_status IS DISTINCT FROM previous_status THEN
        RAISE EXCEPTION 'stale signal transition: expected %, got %',
            previous_status, NEW.from_status;
    END IF;

    IF NOT (
        (previous_status IS NULL AND NEW.to_status = 'candidate') OR
        (previous_status = 'candidate' AND
            NEW.to_status IN ('confirmed', 'suppressed', 'expired')) OR
        (previous_status = 'confirmed' AND
            NEW.to_status IN ('reviewed', 'suppressed', 'expired', 'withdrawn')) OR
        (previous_status = 'reviewed' AND
            NEW.to_status IN ('published', 'suppressed', 'withdrawn')) OR
        (previous_status = 'published' AND NEW.to_status = 'withdrawn') OR
        (previous_status IN ('suppressed', 'expired', 'withdrawn') AND
            NEW.to_status = 'candidate')
    ) THEN
        RAISE EXCEPTION 'invalid signal transition: % -> %',
            previous_status, NEW.to_status;
    END IF;
    IF previous_time IS NOT NULL AND NEW.occurred_at <= previous_time THEN
        RAISE EXCEPTION 'signal transition time must increase';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS signal_transition_guard ON signal_transition;
CREATE TRIGGER signal_transition_guard
BEFORE INSERT ON signal_transition
FOR EACH ROW EXECUTE FUNCTION enforce_signal_transition();

-- Raw evidence, normalized versions, decisions, assertions, and transition
-- history are append-only. Corrections create new rows with supersession links.
CREATE OR REPLACE FUNCTION reject_ledger_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'access_policy', 'policy_lineage', 'raw_asset', 'document_version',
        'mention', 'entity_link_decision', 'evidence_span', 'claim_candidate',
        'claim_candidate_evidence', 'claim_decision', 'knowledge_assertion',
        'knowledge_assertion_evidence',
        'signal_transition', 'signal_transition_assertion'
    ]
    LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS ledger_append_only ON %I', table_name);
        EXECUTE format(
            'CREATE TRIGGER ledger_append_only BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation()',
            table_name
        );
    END LOOP;
END $$;

-- Stable signal identity fields never change. Its effective policy may only
-- move to an explicitly linked, more restrictive policy as evidence changes.
CREATE OR REPLACE FUNCTION protect_signal_identity()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        NEW.pattern_id, NEW.pattern_version, NEW.subject_entity_id,
        NEW.scope_key, NEW.created_at
    ) IS DISTINCT FROM ROW(
        OLD.pattern_id, OLD.pattern_version, OLD.subject_entity_id,
        OLD.scope_key, OLD.created_at
    ) THEN
        RAISE EXCEPTION 'stable signal identity fields are immutable';
    END IF;
    PERFORM assert_policy_not_wider(NEW.policy_id, OLD.policy_id);
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS intelligence_signal_update_guard ON intelligence_signal;
CREATE TRIGGER intelligence_signal_update_guard
BEFORE UPDATE ON intelligence_signal
FOR EACH ROW EXECUTE FUNCTION protect_signal_identity();

DROP TRIGGER IF EXISTS intelligence_signal_delete_guard ON intelligence_signal;
CREATE TRIGGER intelligence_signal_delete_guard
BEFORE DELETE ON intelligence_signal
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

-- Outbox delivery metadata may change, but the event envelope and payload may not.
CREATE OR REPLACE FUNCTION protect_outbox_payload()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(
        NEW.event_type, NEW.aggregate_type, NEW.aggregate_id,
        NEW.aggregate_version, NEW.occurred_at, NEW.correlation_id,
        NEW.causation_id, NEW.policy_id, NEW.payload
    ) IS DISTINCT FROM ROW(
        OLD.event_type, OLD.aggregate_type, OLD.aggregate_id,
        OLD.aggregate_version, OLD.occurred_at, OLD.correlation_id,
        OLD.causation_id, OLD.policy_id, OLD.payload
    ) THEN
        RAISE EXCEPTION 'transactional_outbox event payload is immutable';
    END IF;
    RETURN NEW;
END $$;

CREATE OR REPLACE FUNCTION enforce_outbox_idempotent_insert()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    persisted transactional_outbox%ROWTYPE;
BEGIN
    SELECT * INTO persisted FROM transactional_outbox
    WHERE event_id = NEW.event_id;
    IF FOUND AND ROW(
        persisted.event_type, persisted.aggregate_type, persisted.aggregate_id,
        persisted.aggregate_version, persisted.occurred_at,
        persisted.correlation_id, persisted.causation_id,
        persisted.policy_id, persisted.payload
    ) IS DISTINCT FROM ROW(
        NEW.event_type, NEW.aggregate_type, NEW.aggregate_id,
        NEW.aggregate_version, NEW.occurred_at,
        NEW.correlation_id, NEW.causation_id, NEW.policy_id, NEW.payload
    ) THEN
        RAISE EXCEPTION 'outbox event ID has conflicting immutable content';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS transactional_outbox_insert_guard ON transactional_outbox;
CREATE TRIGGER transactional_outbox_insert_guard
BEFORE INSERT ON transactional_outbox
FOR EACH ROW EXECUTE FUNCTION enforce_outbox_idempotent_insert();

DROP TRIGGER IF EXISTS transactional_outbox_payload_guard ON transactional_outbox;
CREATE TRIGGER transactional_outbox_payload_guard
BEFORE UPDATE ON transactional_outbox
FOR EACH ROW EXECUTE FUNCTION protect_outbox_payload();
