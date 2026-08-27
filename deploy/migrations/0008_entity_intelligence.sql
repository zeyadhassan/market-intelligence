-- Governed entity reference data, conservative resolution, and durable link provenance.

CREATE TABLE IF NOT EXISTS entity_profile_v2 (
    entity_id      UUID PRIMARY KEY REFERENCES entity_identity (entity_id),
    jurisdiction   CHAR(2) CHECK (jurisdiction IS NULL OR jurisdiction ~ '^[A-Z]{2}$'),
    sector         TEXT CHECK (sector IS NULL OR length(sector) > 0)
);

CREATE TABLE IF NOT EXISTS entity_identifier_v2 (
    identifier_id    UUID PRIMARY KEY,
    entity_id        UUID NOT NULL REFERENCES entity_identity (entity_id),
    scheme           TEXT NOT NULL CHECK (scheme IN ('lei', 'bic', 'cik', 'isin', 'ticker', 'internal')),
    value            TEXT NOT NULL CHECK (length(value) > 0),
    normalized_value TEXT NOT NULL CHECK (length(normalized_value) > 0),
    scope            TEXT NOT NULL DEFAULT '',
    effective_from   TIMESTAMPTZ NOT NULL,
    effective_to     TIMESTAMPTZ,
    recorded_at      TIMESTAMPTZ NOT NULL,
    source_id        TEXT NOT NULL CHECK (length(source_id) > 0),
    source_record_id TEXT NOT NULL CHECK (length(source_record_id) > 0),
    policy_id        UUID NOT NULL REFERENCES access_policy (policy_id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (recorded_at >= effective_from),
    CHECK (
        (scheme IN ('ticker', 'internal') AND length(scope) > 0)
        OR (scheme NOT IN ('ticker', 'internal') AND scope = '')
    )
);
CREATE INDEX IF NOT EXISTS entity_identifier_match_v2_idx
    ON entity_identifier_v2 (scheme, scope, normalized_value, effective_from, effective_to);
CREATE UNIQUE INDEX IF NOT EXISTS entity_identifier_current_v2_unique_idx
    ON entity_identifier_v2 (scheme, scope, normalized_value)
    WHERE effective_to IS NULL;

CREATE TABLE IF NOT EXISTS entity_name_v2 (
    name_id            UUID PRIMARY KEY,
    entity_id          UUID NOT NULL REFERENCES entity_identity (entity_id),
    kind               TEXT NOT NULL CHECK (kind IN ('legal', 'alias')),
    name               TEXT NOT NULL CHECK (length(name) > 0),
    normalized_name    TEXT NOT NULL CHECK (length(normalized_name) > 0),
    language           TEXT NOT NULL CHECK (language ~ '^[a-z]{2,3}(-[a-z0-9]{2,8})*$'),
    effective_from     TIMESTAMPTZ NOT NULL,
    effective_to       TIMESTAMPTZ,
    recorded_at        TIMESTAMPTZ NOT NULL,
    source_id          TEXT NOT NULL CHECK (length(source_id) > 0),
    source_record_id   TEXT NOT NULL CHECK (length(source_record_id) > 0),
    policy_id          UUID NOT NULL REFERENCES access_policy (policy_id),
    supersedes_name_id UUID REFERENCES entity_name_v2 (name_id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (recorded_at >= effective_from),
    CHECK (supersedes_name_id IS NULL OR supersedes_name_id <> name_id)
);
CREATE INDEX IF NOT EXISTS entity_name_match_v2_idx
    ON entity_name_v2 (normalized_name, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS entity_relationship_v2 (
    relationship_id              UUID PRIMARY KEY,
    kind                         TEXT NOT NULL CHECK (kind IN ('parent_of', 'issuer_of', 'successor_of')),
    subject_entity_id            UUID NOT NULL REFERENCES entity_identity (entity_id),
    object_entity_id             UUID NOT NULL REFERENCES entity_identity (entity_id),
    effective_from               TIMESTAMPTZ NOT NULL,
    effective_to                 TIMESTAMPTZ,
    recorded_at                  TIMESTAMPTZ NOT NULL,
    source_id                    TEXT NOT NULL CHECK (length(source_id) > 0),
    source_record_id             TEXT NOT NULL CHECK (length(source_record_id) > 0),
    policy_id                    UUID NOT NULL REFERENCES access_policy (policy_id),
    supersedes_relationship_id   UUID REFERENCES entity_relationship_v2 (relationship_id),
    CHECK (subject_entity_id <> object_entity_id),
    CHECK (effective_to IS NULL OR effective_to > effective_from),
    CHECK (recorded_at >= effective_from),
    CHECK (supersedes_relationship_id IS NULL OR supersedes_relationship_id <> relationship_id)
);
CREATE INDEX IF NOT EXISTS entity_relationship_subject_v2_idx
    ON entity_relationship_v2 (subject_entity_id, kind, effective_from, effective_to);
CREATE INDEX IF NOT EXISTS entity_relationship_object_v2_idx
    ON entity_relationship_v2 (object_entity_id, kind, effective_from, effective_to);

CREATE TABLE IF NOT EXISTS entity_resolution_v2 (
    resolution_id             UUID PRIMARY KEY,
    mention_id                UUID NOT NULL REFERENCES mention (mention_id),
    mention_context           JSONB NOT NULL CHECK (jsonb_typeof(mention_context) = 'object'),
    disposition               TEXT NOT NULL CHECK (disposition IN ('auto_link', 'review_required', 'abstained')),
    recommended_candidate_id  UUID,
    score                     DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    margin                    DOUBLE PRECISION NOT NULL CHECK (margin BETWEEN 0 AND 1),
    reason                    TEXT NOT NULL CHECK (length(reason) > 0),
    resolver_version          TEXT NOT NULL CHECK (length(resolver_version) > 0),
    policy_version            TEXT NOT NULL CHECK (length(policy_version) > 0),
    resolved_at               TIMESTAMPTZ NOT NULL,
    policy_id                 UUID NOT NULL REFERENCES access_policy (policy_id)
);

CREATE TABLE IF NOT EXISTS entity_resolution_candidate_v2 (
    candidate_id            UUID PRIMARY KEY,
    resolution_id           UUID NOT NULL REFERENCES entity_resolution_v2 (resolution_id),
    mention_id              UUID NOT NULL REFERENCES mention (mention_id),
    entity_id               UUID NOT NULL REFERENCES entity_identity (entity_id),
    score                   DOUBLE PRECISION NOT NULL CHECK (score BETWEEN 0 AND 1),
    contributions           JSONB NOT NULL CHECK (jsonb_typeof(contributions) = 'array'),
    matched_identifier_ids  UUID[] NOT NULL DEFAULT '{}',
    matched_name_ids        UUID[] NOT NULL DEFAULT '{}',
    blocked_reasons         TEXT[] NOT NULL DEFAULT '{}',
    resolver_version        TEXT NOT NULL CHECK (length(resolver_version) > 0),
    policy_version          TEXT NOT NULL CHECK (length(policy_version) > 0),
    generated_at            TIMESTAMPTZ NOT NULL,
    policy_id               UUID NOT NULL REFERENCES access_policy (policy_id),
    UNIQUE (resolution_id, entity_id),
    CHECK (cardinality(blocked_reasons) = 0 OR score = 0.0)
);
CREATE INDEX IF NOT EXISTS entity_resolution_candidate_review_v2_idx
    ON entity_resolution_candidate_v2 (resolution_id, score DESC, entity_id);

ALTER TABLE entity_resolution_v2
    ADD CONSTRAINT entity_resolution_recommended_candidate_v2_fk
    FOREIGN KEY (recommended_candidate_id)
    REFERENCES entity_resolution_candidate_v2 (candidate_id)
    DEFERRABLE INITIALLY DEFERRED;

ALTER TABLE entity_link_decision
    ADD COLUMN IF NOT EXISTS entity_link_id UUID,
    ADD COLUMN IF NOT EXISTS resolution_candidate_id UUID
        REFERENCES entity_resolution_candidate_v2 (candidate_id),
    ADD COLUMN IF NOT EXISTS supersedes_entity_link_id UUID,
    ADD COLUMN IF NOT EXISTS invalidates_entity_link_id UUID;

ALTER TABLE entity_link_decision
    DROP CONSTRAINT IF EXISTS entity_link_decision_status_check,
    DROP CONSTRAINT IF EXISTS entity_link_decision_check;

ALTER TABLE entity_link_decision
    ADD CONSTRAINT entity_link_decision_status_v2_check
        CHECK (status IN ('linked', 'review_required', 'abstained', 'rejected', 'invalidated')),
    ADD CONSTRAINT entity_link_decision_shape_v2_check CHECK (
        (
            status = 'linked'
            AND entity_link_id IS NOT NULL
            AND entity_id IS NOT NULL
            AND confidence IS NOT NULL
            AND invalidates_entity_link_id IS NULL
        )
        OR (
            status = 'invalidated'
            AND invalidates_entity_link_id IS NOT NULL
            AND entity_link_id IS NULL
            AND entity_id IS NULL
            AND confidence IS NULL
            AND supersedes_entity_link_id IS NULL
        )
        OR (
            status IN ('review_required', 'abstained', 'rejected')
            AND entity_link_id IS NULL
            AND entity_id IS NULL
            AND supersedes_entity_link_id IS NULL
            AND invalidates_entity_link_id IS NULL
        )
    ),
    ADD CONSTRAINT entity_link_decision_link_v2_unique UNIQUE (entity_link_id),
    ADD CONSTRAINT entity_link_decision_not_self_superseding_v2_check
        CHECK (supersedes_entity_link_id IS NULL OR supersedes_entity_link_id <> entity_link_id);

ALTER TABLE entity_link_decision
    ADD CONSTRAINT entity_link_decision_supersedes_v2_fk
        FOREIGN KEY (supersedes_entity_link_id)
        REFERENCES entity_link_decision (entity_link_id),
    ADD CONSTRAINT entity_link_decision_invalidates_v2_fk
        FOREIGN KEY (invalidates_entity_link_id)
        REFERENCES entity_link_decision (entity_link_id);

CREATE INDEX IF NOT EXISTS entity_link_active_v2_idx
    ON entity_link_decision (mention_id, decided_at DESC, decision_id DESC)
    WHERE status IN ('linked', 'invalidated');

ALTER TABLE knowledge_assertion
    ADD COLUMN IF NOT EXISTS subject_entity_link_id UUID,
    ADD COLUMN IF NOT EXISTS object_entity_link_id UUID;

ALTER TABLE knowledge_assertion
    ADD CONSTRAINT knowledge_assertion_subject_link_v2_fk
        FOREIGN KEY (subject_entity_link_id)
        REFERENCES entity_link_decision (entity_link_id),
    ADD CONSTRAINT knowledge_assertion_object_link_v2_fk
        FOREIGN KEY (object_entity_link_id)
        REFERENCES entity_link_decision (entity_link_id);

CREATE OR REPLACE FUNCTION active_entity_link_v2(
    requested_link_id UUID,
    requested_mention_id UUID
) RETURNS TABLE(entity_id UUID) LANGUAGE SQL STABLE AS $$
    SELECT linked.entity_id
    FROM entity_link_decision linked
    WHERE linked.entity_link_id = requested_link_id
      AND linked.mention_id = requested_mention_id
      AND linked.status = 'linked'
      AND NOT EXISTS (
          SELECT 1 FROM entity_link_decision closed
          WHERE closed.mention_id = linked.mention_id
            AND closed.decided_at >= linked.decided_at
            AND (
                closed.invalidates_entity_link_id = linked.entity_link_id
                OR closed.supersedes_entity_link_id = linked.entity_link_id
            )
      );
$$;

CREATE OR REPLACE FUNCTION enforce_entity_link_candidates()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    active_link_id UUID;
    candidate_mention_id UUID;
    candidate_entity_id UUID;
BEGIN
    IF EXISTS (
        SELECT 1 FROM unnest(NEW.candidate_entity_ids) AS candidate(entity_id)
        LEFT JOIN entity_identity entity USING (entity_id)
        WHERE entity.entity_id IS NULL
    ) THEN
        RAISE EXCEPTION 'entity-link candidate does not exist';
    END IF;

    IF NEW.resolution_candidate_id IS NOT NULL THEN
        SELECT mention_id, entity_id INTO STRICT candidate_mention_id, candidate_entity_id
        FROM entity_resolution_candidate_v2
        WHERE candidate_id = NEW.resolution_candidate_id;
        IF candidate_mention_id <> NEW.mention_id THEN
            RAISE EXCEPTION 'resolution candidate belongs to another mention';
        END IF;
        IF NEW.status = 'linked' AND candidate_entity_id <> NEW.entity_id THEN
            RAISE EXCEPTION 'linked entity differs from resolution candidate';
        END IF;
    END IF;

    IF NEW.status = 'linked'
       AND cardinality(NEW.candidate_entity_ids) > 0
       AND NOT NEW.entity_id = ANY(NEW.candidate_entity_ids) THEN
        RAISE EXCEPTION 'linked entity is absent from candidate set';
    END IF;

    SELECT linked.entity_link_id INTO active_link_id
    FROM entity_link_decision linked
    WHERE linked.mention_id = NEW.mention_id
      AND linked.status = 'linked'
      AND NOT EXISTS (
          SELECT 1 FROM entity_link_decision closed
          WHERE closed.mention_id = linked.mention_id
            AND closed.decided_at >= linked.decided_at
            AND (
                closed.invalidates_entity_link_id = linked.entity_link_id
                OR closed.supersedes_entity_link_id = linked.entity_link_id
            )
      )
    ORDER BY linked.decided_at DESC, linked.decision_id DESC LIMIT 1;

    IF NEW.status = 'linked' THEN
        IF active_link_id IS NOT NULL
           AND NEW.supersedes_entity_link_id IS DISTINCT FROM active_link_id THEN
            RAISE EXCEPTION 'new entity link must supersede the active link';
        END IF;
        IF active_link_id IS NULL AND NEW.supersedes_entity_link_id IS NOT NULL THEN
            RAISE EXCEPTION 'superseded entity link is not active';
        END IF;
    ELSIF NEW.status = 'invalidated'
          AND NEW.invalidates_entity_link_id IS DISTINCT FROM active_link_id THEN
        RAISE EXCEPTION 'invalidated entity link is not active';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS entity_link_candidate_guard ON entity_link_decision;
CREATE TRIGGER entity_link_candidate_guard
BEFORE INSERT ON entity_link_decision
FOR EACH ROW EXECUTE FUNCTION enforce_entity_link_candidates();

CREATE OR REPLACE FUNCTION enforce_assertion_candidate()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
DECLARE
    subject_mention_id UUID;
    subject_mention_kind TEXT;
    candidate_predicate TEXT;
    candidate_object JSONB;
    resolved_entity_id UUID;
    object_mention_id UUID;
    object_mention_kind TEXT;
BEGIN
    SELECT c.subject_mention_id, m.kind, c.predicate, c.object_json
    INTO STRICT subject_mention_id, subject_mention_kind, candidate_predicate, candidate_object
    FROM claim_candidate c JOIN mention m ON m.mention_id = c.subject_mention_id
    WHERE c.candidate_id = NEW.candidate_id;

    IF NEW.predicate <> candidate_predicate THEN
        RAISE EXCEPTION 'assertion predicate differs from its candidate';
    END IF;

    IF NEW.ontology_version LIKE 'fi-ontology-v2%'
       AND subject_mention_kind IN ('organization', 'instrument')
       AND NEW.subject_entity_link_id IS NULL THEN
        RAISE EXCEPTION 'v2 organization/instrument assertion requires subject entity_link_id';
    END IF;

    IF NEW.subject_entity_link_id IS NOT NULL THEN
        SELECT entity_id INTO resolved_entity_id
        FROM active_entity_link_v2(NEW.subject_entity_link_id, subject_mention_id);
    ELSE
        SELECT entity_id INTO resolved_entity_id
        FROM entity_link_decision
        WHERE mention_id = subject_mention_id AND status = 'linked'
        ORDER BY decided_at DESC, decision_id DESC LIMIT 1;
    END IF;
    IF resolved_entity_id IS DISTINCT FROM NEW.subject_entity_id THEN
        RAISE EXCEPTION 'assertion subject is not the active resolved claim mention';
    END IF;

    IF candidate_object ->> 'kind' = 'entity_mention' THEN
        object_mention_id := (candidate_object ->> 'entity_mention_id')::UUID;
        SELECT kind INTO STRICT object_mention_kind FROM mention WHERE mention_id = object_mention_id;
        IF NEW.ontology_version LIKE 'fi-ontology-v2%'
           AND object_mention_kind IN ('organization', 'instrument')
           AND NEW.object_entity_link_id IS NULL THEN
            RAISE EXCEPTION 'v2 organization/instrument assertion requires object entity_link_id';
        END IF;
        IF NEW.object_entity_link_id IS NOT NULL THEN
            SELECT entity_id INTO resolved_entity_id
            FROM active_entity_link_v2(NEW.object_entity_link_id, object_mention_id);
        ELSE
            SELECT entity_id INTO resolved_entity_id
            FROM entity_link_decision
            WHERE mention_id = object_mention_id AND status = 'linked'
            ORDER BY decided_at DESC, decision_id DESC LIMIT 1;
        END IF;
        IF NEW.object_json ->> 'kind' <> 'entity_mention'
           OR resolved_entity_id IS DISTINCT FROM (NEW.object_json ->> 'entity_id')::UUID THEN
            RAISE EXCEPTION 'assertion object is not the active resolved claim mention';
        END IF;
    ELSIF NEW.object_json -> 'kind' IS DISTINCT FROM candidate_object -> 'kind'
       OR NEW.object_json -> 'value' IS DISTINCT FROM candidate_object -> 'value' THEN
        RAISE EXCEPTION 'assertion object differs from its candidate';
    ELSIF NEW.object_entity_link_id IS NOT NULL THEN
        RAISE EXCEPTION 'literal assertion object cannot carry entity_link_id';
    END IF;
    RETURN NEW;
END $$;

DROP TRIGGER IF EXISTS knowledge_assertion_candidate_guard ON knowledge_assertion;
CREATE TRIGGER knowledge_assertion_candidate_guard
BEFORE INSERT ON knowledge_assertion
FOR EACH ROW EXECUTE FUNCTION enforce_assertion_candidate();

CREATE OR REPLACE FUNCTION reject_entity_intelligence_mutation()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END $$;

DO $$
DECLARE
    table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
        'entity_identifier_v2', 'entity_name_v2', 'entity_relationship_v2',
        'entity_resolution_v2', 'entity_resolution_candidate_v2', 'entity_link_decision'
    ] LOOP
        EXECUTE format('DROP TRIGGER IF EXISTS append_only_guard ON %I', table_name);
        EXECUTE format(
            'CREATE TRIGGER append_only_guard BEFORE UPDATE OR DELETE ON %I '
            'FOR EACH ROW EXECUTE FUNCTION reject_entity_intelligence_mutation()',
            table_name
        );
    END LOOP;
END $$;
