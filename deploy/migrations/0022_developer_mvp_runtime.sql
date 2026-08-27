-- Canonical developer-MVP workers, exact retrieval lineage, opportunity/read
-- models, routed search, and sandbox delivery state.

-- -------------------------------------------------------------------------
-- Versioned governed product/source catalog
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS analysis_topic_v4 (
    topic_id                 TEXT NOT NULL,
    version                  TEXT NOT NULL,
    display_name             TEXT NOT NULL,
    description              TEXT NOT NULL,
    owner                    TEXT NOT NULL,
    pattern_names            TEXT[] NOT NULL CHECK (cardinality(pattern_names) > 0),
    required_source_ids      TEXT[] NOT NULL CHECK (cardinality(required_source_ids) > 0),
    freshness_seconds        INTEGER NOT NULL CHECK (freshness_seconds > 0),
    detector_policy_version  TEXT NOT NULL,
    retrieval_policy_version TEXT NOT NULL,
    lifecycle_policy_version TEXT NOT NULL,
    display_order            INTEGER NOT NULL CHECK (display_order >= 0),
    active                   BOOLEAN NOT NULL,
    created_at               TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (topic_id, version)
);

INSERT INTO analysis_topic_v4 (
    topic_id, version, display_name, description, owner, pattern_names,
    required_source_ids, freshness_seconds, detector_policy_version,
    retrieval_policy_version, lifecycle_policy_version, display_order, active,
    created_at
) VALUES
    (
      'upcoming-maturities', 'topic-v1', 'Upcoming maturities',
      'Funding needs where complete coverage found no announced refinancing.',
      'fi_gcc', ARRAY['maturity_wall_no_refi','at1_call_approaching_no_refi'],
      ARRAY['sa_sama_news','sa_cma_announcements','ae_cbuae_news','ae_cma_updates',
            'qa_qcb_news','qa_qfma_news','kw_cbk_press','kw_cbk_announcements',
            'bh_cbb_media','bh_bourse_announcements','om_cbo_news','om_fsa_news'],
      86400, 'detector-policy-v1', 'daily-hybrid-v1', 'opportunity-lifecycle-v1',
      10, TRUE, TIMESTAMPTZ '2026-08-27 00:00:00+00'
    ),
    (
      'ratings-capital-pressure', 'topic-v1', 'Rating and capital pressure',
      'Rating deterioration combined with a material capital movement.',
      'fi_gcc', ARRAY['negative_rating_action_with_capital_decline'],
      ARRAY['sa_sama_news','sa_cma_announcements','ae_cbuae_news','ae_cma_updates',
            'qa_qcb_news','qa_qfma_news','kw_cbk_press','kw_cbk_announcements',
            'bh_cbb_media','bh_bourse_announcements','om_cbo_news','om_fsa_news'],
      86400, 'detector-policy-v1', 'daily-hybrid-v1', 'opportunity-lifecycle-v1',
      20, TRUE, TIMESTAMPTZ '2026-08-27 00:00:00+00'
    )
ON CONFLICT DO NOTHING;

-- Complete the operational registrations that the source worker records
-- observations against. Values mirror the checked-in catalog defaults.
INSERT INTO source_registration_v2 (
    source_id, catalog_version, display_name, source_kind, discovery_url,
    allowed_origins, cadence_seconds, freshness_sla_seconds,
    silence_sla_seconds, expected_min_items, expected_max_items,
    licence_group, licence_class, raw_retention_days, barrier_side,
    allowed_entitlement_groups, max_feed_bytes, max_detail_bytes,
    request_timeout_seconds, max_attempts, max_redirects, cursor_history_limit
)
SELECT sr.source_id, 'gcc-official-source-catalog-v1', sr.display_name,
       'feed_detail', sr.url, sr.origins, 900, 3600, 86400, 1, 26,
       'open_web_public', 'open_government', 2555, 'public',
       ARRAY['fi_gcc_private','fi_gcc_public','open_web_public'],
       16777216, 16777216, 15, 3, 3, 1000
FROM (VALUES
 ('sa_sama_news','Saudi Central Bank news','https://sama.gov.sa/en-US/MediaCenter/News/pages/allnews.aspx',ARRAY['https://sama.gov.sa','https://www.sama.gov.sa']::text[]),
 ('sa_cma_announcements','Saudi Capital Market Authority announcements','https://cma.org.sa/en/market/news/Pages/default.aspx',ARRAY['https://cma.org.sa','https://www.cma.org.sa','https://cma.gov.sa','https://www.cma.gov.sa']::text[]),
 ('ae_cbuae_news','Central Bank of the UAE news','https://www.centralbank.ae/en/news-and-publications/news-and-insights/',ARRAY['https://www.centralbank.ae']::text[]),
 ('ae_cma_updates','UAE Capital Market Authority updates','https://www.uaecma.gov.ae/en/',ARRAY['https://www.uaecma.gov.ae']::text[]),
 ('qa_qcb_news','Qatar Central Bank news','https://www.qcb.gov.qa/en/News/Pages/default.aspx',ARRAY['https://www.qcb.gov.qa']::text[]),
 ('qa_qfma_news','Qatar Financial Markets Authority news','https://www.qfma.org.qa/English/MediaCenter/News/Pages/default.aspx',ARRAY['https://www.qfma.org.qa']::text[]),
 ('kw_cbk_press','Central Bank of Kuwait press releases','https://www.cbk.gov.kw/en/cbk-news/announcements-and-press-releases/press-releases',ARRAY['https://www.cbk.gov.kw']::text[]),
 ('kw_cbk_announcements','Central Bank of Kuwait announcements','https://www.cbk.gov.kw/en/cbk-news/announcements-and-press-releases/announcements',ARRAY['https://www.cbk.gov.kw']::text[]),
 ('bh_cbb_media','Central Bank of Bahrain media centre','https://www.cbb.gov.bh/media-center/',ARRAY['https://www.cbb.gov.bh']::text[]),
 ('bh_bourse_announcements','Bahrain Bourse company announcements','https://bahrainbourse.com/en/news%20and%20events/CompanyAnnouncements',ARRAY['https://bahrainbourse.com','https://www.bahrainbourse.com']::text[]),
 ('om_cbo_news','Central Bank of Oman news','https://cbo.gov.om/Pages/home.aspx',ARRAY['https://cbo.gov.om','https://www.cbo.gov.om']::text[]),
 ('om_fsa_news','Oman Financial Services Authority news','https://fsa.gov.om/Home/News/',ARRAY['https://fsa.gov.om','https://www.fsa.gov.om']::text[])
) AS sr(source_id, display_name, url, origins)
ON CONFLICT DO NOTHING;

-- -------------------------------------------------------------------------
-- Exact chunk-to-authority bridge
-- -------------------------------------------------------------------------
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS document_version_id UUID
    REFERENCES document_version (document_version_id);
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS policy_id UUID
    REFERENCES access_policy (policy_id);
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS chunker_version TEXT;
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS section_path TEXT[] NOT NULL DEFAULT '{}';
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS content_hash CHAR(64);
ALTER TABLE document_chunk ADD COLUMN IF NOT EXISTS canonical_lineage BOOLEAN NOT NULL DEFAULT FALSE;

CREATE TABLE IF NOT EXISTS document_chunk_entity_v4 (
    chunk_id                 BIGINT NOT NULL REFERENCES document_chunk (chunk_id) ON DELETE CASCADE,
    entity_id               UUID NOT NULL REFERENCES entity_identity (entity_id),
    resolution_confidence   DOUBLE PRECISION NOT NULL CHECK (resolution_confidence BETWEEN 0 AND 1),
    policy_id               UUID NOT NULL REFERENCES access_policy (policy_id),
    recorded_at             TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (chunk_id, entity_id)
);
CREATE TABLE IF NOT EXISTS document_chunk_assertion_v4 (
    chunk_id                 BIGINT NOT NULL REFERENCES document_chunk (chunk_id) ON DELETE CASCADE,
    assertion_id             UUID NOT NULL REFERENCES knowledge_assertion (assertion_id),
    policy_id                UUID NOT NULL REFERENCES access_policy (policy_id),
    PRIMARY KEY (chunk_id, assertion_id)
);
CREATE TABLE IF NOT EXISTS document_chunk_evidence_v4 (
    chunk_id                 BIGINT NOT NULL REFERENCES document_chunk (chunk_id) ON DELETE CASCADE,
    evidence_span_id         UUID NOT NULL REFERENCES evidence_span (evidence_span_id),
    policy_id                UUID NOT NULL REFERENCES access_policy (policy_id),
    PRIMARY KEY (chunk_id, evidence_span_id)
);
CREATE INDEX IF NOT EXISTS document_chunk_version_v4_idx
    ON document_chunk (document_version_id, chunk_index) WHERE canonical_lineage;
CREATE INDEX IF NOT EXISTS document_chunk_entity_v4_lookup
    ON document_chunk_entity_v4 (entity_id, chunk_id);
CREATE INDEX IF NOT EXISTS document_chunk_assertion_v4_lookup
    ON document_chunk_assertion_v4 (assertion_id, chunk_id);

-- -------------------------------------------------------------------------
-- Durable source/document/analysis workers
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS document_processing_job_v4 (
    document_version_id       UUID PRIMARY KEY REFERENCES document_version (document_version_id),
    event_id                  UUID NOT NULL UNIQUE REFERENCES transactional_outbox (event_id),
    state                     TEXT NOT NULL CHECK (state IN (
                                'queued','running','complete','held',
                                'retryable_failed','terminal_failed'
                              )),
    attempt_count             INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at           TIMESTAMPTZ NOT NULL,
    lease_owner               TEXT,
    lease_expires_at          TIMESTAMPTZ,
    safe_error_summary        TEXT,
    processed_at              TIMESTAMPTZ,
    updated_at                TIMESTAMPTZ NOT NULL,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);

CREATE TABLE IF NOT EXISTS analysis_job_v4 (
    job_id                    CHAR(64) PRIMARY KEY CHECK (job_id ~ '^[0-9a-f]{64}$'),
    idempotency_key           TEXT NOT NULL UNIQUE,
    business_date             DATE NOT NULL,
    topic_ids                 TEXT[] NOT NULL CHECK (cardinality(topic_ids) > 0),
    authorization_scope       TEXT NOT NULL,
    principal_snapshot        JSONB NOT NULL,
    input_manifest            JSONB NOT NULL,
    frozen_manifest           JSONB,
    frozen_at                 TIMESTAMPTZ,
    temporal_pin              TIMESTAMPTZ NOT NULL,
    state                     TEXT NOT NULL CHECK (state IN (
                                'queued','running','complete','partial','held','deferred',
                                'retryable_failed','terminal_failed'
                              )),
    run_id                    TEXT,
    attempt_count             INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at           TIMESTAMPTZ NOT NULL,
    lease_owner               TEXT,
    lease_expires_at          TIMESTAMPTZ,
    requested_at              TIMESTAMPTZ NOT NULL,
    updated_at                TIMESTAMPTZ NOT NULL,
    safe_error_summary        TEXT,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS analysis_job_v4_claim_idx
    ON analysis_job_v4 (next_attempt_at, requested_at, job_id)
    WHERE state IN ('queued','deferred','retryable_failed','running');

CREATE TABLE IF NOT EXISTS analysis_job_transition_v4 (
    transition_id             CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    job_id                    CHAR(64) NOT NULL REFERENCES analysis_job_v4 (job_id),
    from_state                TEXT,
    to_state                  TEXT NOT NULL,
    worker_id                 TEXT,
    safe_detail               TEXT,
    occurred_at               TIMESTAMPTZ NOT NULL
);

-- -------------------------------------------------------------------------
-- Logical opportunity and read projection
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS logical_opportunity_v4 (
    logical_opportunity_id    CHAR(64) PRIMARY KEY CHECK (logical_opportunity_id ~ '^[0-9a-f]{64}$'),
    topic_id                  TEXT NOT NULL,
    entity_id                 TEXT NOT NULL,
    subject_key               TEXT NOT NULL,
    authorization_scope       TEXT NOT NULL,
    lifecycle_policy_version  TEXT NOT NULL,
    created_at                TIMESTAMPTZ NOT NULL,
    UNIQUE (topic_id, entity_id, subject_key, authorization_scope, lifecycle_policy_version)
);
CREATE TABLE IF NOT EXISTS opportunity_transition_v4 (
    transition_id             CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    logical_opportunity_id    CHAR(64) NOT NULL REFERENCES logical_opportunity_v4,
    result_version_id         CHAR(64) REFERENCES result_version_v3,
    state                     TEXT NOT NULL CHECK (state IN (
                                'new','updated','unchanged','weakened','contradicted',
                                'resolved','suppressed','held'
                              )),
    material_change           JSONB NOT NULL,
    condition_outcome         TEXT NOT NULL CHECK (condition_outcome IN (
                                'active','condition_resolved','unknown'
                              )),
    commercial_outcome        TEXT NOT NULL CHECK (commercial_outcome IN (
                                'opportunity_won','opportunity_lost','not_actionable',
                                'unknown_outcome'
                              )),
    occurred_at               TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS opportunity_transition_v4_latest
    ON opportunity_transition_v4 (logical_opportunity_id, occurred_at DESC, transition_id DESC);

CREATE TABLE IF NOT EXISTS daily_topic_read_model_v4 (
    topic_id                  TEXT NOT NULL,
    business_date             DATE NOT NULL,
    authorization_scope       TEXT NOT NULL,
    analysis_job_id           CHAR(64) NOT NULL REFERENCES analysis_job_v4,
    run_id                    TEXT,
    state                     TEXT NOT NULL,
    coverage_summary          JSONB NOT NULL,
    latest_source_time        TIMESTAMPTZ,
    lifecycle_counts          JSONB NOT NULL,
    ordered_result_version_ids TEXT[] NOT NULL,
    result_lifecycle          JSONB NOT NULL,
    safe_message              TEXT NOT NULL,
    materialized_at           TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (topic_id, business_date, authorization_scope)
);

-- -------------------------------------------------------------------------
-- Asynchronous governed search
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS search_job_v4 (
    search_id                 CHAR(64) PRIMARY KEY CHECK (search_id ~ '^[0-9a-f]{64}$'),
    idempotency_key           TEXT NOT NULL UNIQUE,
    principal_snapshot        JSONB NOT NULL,
    authorization_scope       TEXT NOT NULL,
    query_text                TEXT NOT NULL CHECK (length(query_text) BETWEEN 1 AND 2000),
    plan                      JSONB NOT NULL,
    temporal_pin              TIMESTAMPTZ NOT NULL,
    state                     TEXT NOT NULL CHECK (state IN (
                                'queued','running','complete','held',
                                'retryable_failed','terminal_failed'
                              )),
    answer                    JSONB,
    attempt_count             INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    next_attempt_at           TIMESTAMPTZ NOT NULL,
    lease_owner               TEXT,
    lease_expires_at          TIMESTAMPTZ,
    requested_at              TIMESTAMPTZ NOT NULL,
    updated_at                TIMESTAMPTZ NOT NULL,
    safe_error_summary        TEXT,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
CREATE INDEX IF NOT EXISTS search_job_v4_claim_idx
    ON search_job_v4 (next_attempt_at, requested_at, search_id)
    WHERE state IN ('queued','retryable_failed','running');
CREATE TABLE IF NOT EXISTS search_step_v4 (
    step_id                   CHAR(64) PRIMARY KEY CHECK (step_id ~ '^[0-9a-f]{64}$'),
    search_id                 CHAR(64) NOT NULL REFERENCES search_job_v4,
    sequence                  INTEGER NOT NULL CHECK (sequence > 0),
    operation                 TEXT NOT NULL,
    request_payload           JSONB NOT NULL,
    response_payload          JSONB NOT NULL,
    status                    TEXT NOT NULL,
    occurred_at               TIMESTAMPTZ NOT NULL,
    UNIQUE (search_id, sequence)
);

-- -------------------------------------------------------------------------
-- Development notification and idempotent sandbox delivery
-- -------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS notification_destination_v4 (
    destination_id            CHAR(64) PRIMARY KEY CHECK (destination_id ~ '^[0-9a-f]{64}$'),
    principal_id              TEXT NOT NULL,
    channel                   TEXT NOT NULL CHECK (channel = 'email'),
    destination_ciphertext    TEXT NOT NULL,
    destination_fingerprint   CHAR(64) NOT NULL CHECK (destination_fingerprint ~ '^[0-9a-f]{64}$'),
    verified_at               TIMESTAMPTZ NOT NULL,
    active                    BOOLEAN NOT NULL,
    UNIQUE (principal_id, channel, destination_fingerprint)
);
CREATE TABLE IF NOT EXISTS notification_preference_transition_v4 (
    transition_id             CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    principal_id              TEXT NOT NULL,
    destination_id            CHAR(64) NOT NULL REFERENCES notification_destination_v4,
    timezone_name             TEXT NOT NULL,
    local_send_time           TIME NOT NULL,
    frequency                 TEXT NOT NULL CHECK (frequency IN ('daily','weekdays','paused')),
    topic_ids                 TEXT[] NOT NULL,
    include_nothing_new       BOOLEAN NOT NULL,
    link_only                 BOOLEAN NOT NULL,
    unsubscribed              BOOLEAN NOT NULL,
    occurred_at               TIMESTAMPTZ NOT NULL
);
CREATE INDEX IF NOT EXISTS notification_preference_v4_latest
    ON notification_preference_transition_v4
       (principal_id, occurred_at DESC, transition_id DESC);

CREATE TABLE IF NOT EXISTS digest_v4 (
    digest_id                 CHAR(64) PRIMARY KEY CHECK (digest_id ~ '^[0-9a-f]{64}$'),
    principal_id              TEXT NOT NULL,
    destination_id            CHAR(64) NOT NULL REFERENCES notification_destination_v4,
    local_business_date       DATE NOT NULL,
    authorization_scope       TEXT NOT NULL,
    digest_version            TEXT NOT NULL,
    subject                   TEXT NOT NULL,
    text_body                 TEXT NOT NULL,
    html_body                 TEXT NOT NULL,
    state                     TEXT NOT NULL CHECK (state IN ('queued','rendered','suppressed')),
    created_at                TIMESTAMPTZ NOT NULL,
    UNIQUE (principal_id, local_business_date, authorization_scope, digest_version)
);
CREATE TABLE IF NOT EXISTS digest_item_v4 (
    digest_id                 CHAR(64) NOT NULL REFERENCES digest_v4,
    position                  INTEGER NOT NULL CHECK (position > 0),
    topic_id                  TEXT NOT NULL,
    result_version_id         CHAR(64) REFERENCES result_version_v3,
    lifecycle_state           TEXT NOT NULL,
    coverage_notice           TEXT,
    PRIMARY KEY (digest_id, position),
    CHECK (result_version_id IS NOT NULL OR coverage_notice IS NOT NULL)
);
CREATE TABLE IF NOT EXISTS delivery_attempt_v4 (
    attempt_id                CHAR(64) PRIMARY KEY CHECK (attempt_id ~ '^[0-9a-f]{64}$'),
    digest_id                 CHAR(64) NOT NULL REFERENCES digest_v4,
    idempotency_key           TEXT NOT NULL UNIQUE,
    provider                  TEXT NOT NULL,
    state                     TEXT NOT NULL CHECK (state IN (
                                'queued','sending','accepted','observed_delivered',
                                'retryable_failed','permanent_failed','suppressed',
                                'acceptance_unknown'
                              )),
    provider_reference        TEXT,
    attempt_count             INTEGER NOT NULL CHECK (attempt_count > 0),
    safe_error_summary        TEXT,
    next_attempt_at           TIMESTAMPTZ,
    lease_owner               TEXT,
    lease_expires_at          TIMESTAMPTZ,
    updated_at                TIMESTAMPTZ NOT NULL,
    CHECK ((lease_owner IS NULL) = (lease_expires_at IS NULL))
);
CREATE TABLE IF NOT EXISTS delivery_transition_v4 (
    transition_id             CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    attempt_id                CHAR(64) NOT NULL REFERENCES delivery_attempt_v4,
    state                     TEXT NOT NULL,
    provider_reference        TEXT,
    safe_detail               TEXT,
    occurred_at               TIMESTAMPTZ NOT NULL
);

-- Append-only governance/history records.
DO $$
DECLARE table_name TEXT;
BEGIN
    FOREACH table_name IN ARRAY ARRAY[
      'analysis_topic_v4', 'document_chunk_entity_v4',
      'document_chunk_assertion_v4', 'document_chunk_evidence_v4',
      'analysis_job_transition_v4', 'logical_opportunity_v4',
      'opportunity_transition_v4', 'search_step_v4',
      'notification_preference_transition_v4', 'digest_v4', 'digest_item_v4',
      'delivery_transition_v4'
    ] LOOP
      EXECUTE format('DROP TRIGGER IF EXISTS immutable_mvp_record ON %I', table_name);
      EXECUTE format(
        'CREATE TRIGGER immutable_mvp_record BEFORE UPDATE OR DELETE ON %I '
        'FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation()', table_name
      );
    END LOOP;
END
$$;
