-- Server-owned run completion and publication coverage.

CREATE TABLE IF NOT EXISTS analysis_run_completion_v3 (
    completion_id          CHAR(64) PRIMARY KEY CHECK (completion_id ~ '^[0-9a-f]{64}$'),
    run_id                 TEXT NOT NULL REFERENCES analysis_run_v3 (run_id),
    required_source_ids    TEXT[] NOT NULL,
    completed_source_ids   TEXT[] NOT NULL,
    required_job_ids       TEXT[] NOT NULL,
    completed_job_ids      TEXT[] NOT NULL,
    coverage_reasons       TEXT[] NOT NULL DEFAULT '{}',
    complete               BOOLEAN GENERATED ALWAYS AS (
        cardinality(required_source_ids) > 0
        AND required_source_ids <@ completed_source_ids
        AND required_job_ids <@ completed_job_ids
        AND cardinality(coverage_reasons) = 0
    ) STORED,
    computed_at            TIMESTAMPTZ NOT NULL,
    UNIQUE (run_id, completion_id)
);

CREATE INDEX IF NOT EXISTS analysis_run_completion_latest_idx
    ON analysis_run_completion_v3 (run_id, computed_at DESC, completion_id DESC);

CREATE TRIGGER immutable_analysis_run_completion
BEFORE UPDATE OR DELETE ON analysis_run_completion_v3
FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation();

CREATE TABLE IF NOT EXISTS topic_subscription_transition_v3 (
    transition_id          CHAR(64) PRIMARY KEY CHECK (transition_id ~ '^[0-9a-f]{64}$'),
    principal_id           TEXT NOT NULL,
    topic_id               TEXT NOT NULL,
    active                 BOOLEAN NOT NULL,
    occurred_at            TIMESTAMPTZ NOT NULL,
    UNIQUE (principal_id, topic_id, occurred_at)
);

CREATE INDEX IF NOT EXISTS topic_subscription_latest_idx
    ON topic_subscription_transition_v3 (
        principal_id, topic_id, occurred_at DESC, transition_id DESC
    );

CREATE TRIGGER immutable_topic_subscription_transition
BEFORE UPDATE OR DELETE ON topic_subscription_transition_v3
FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation();

INSERT INTO source_registry (
    source_id, display_name, licence_group, barrier_side, licensed
) VALUES
    ('sa_sama_news', 'Saudi Central Bank news', 'open_web_public', 'public', TRUE),
    ('sa_cma_announcements', 'Saudi Capital Market Authority announcements', 'open_web_public', 'public', TRUE),
    ('ae_cbuae_news', 'Central Bank of the UAE news', 'open_web_public', 'public', TRUE),
    ('ae_cma_updates', 'UAE Capital Market Authority updates', 'open_web_public', 'public', TRUE),
    ('qa_qcb_news', 'Qatar Central Bank news', 'open_web_public', 'public', TRUE),
    ('qa_qfma_news', 'Qatar Financial Markets Authority news', 'open_web_public', 'public', TRUE),
    ('kw_cbk_press', 'Central Bank of Kuwait press releases', 'open_web_public', 'public', TRUE),
    ('kw_cbk_announcements', 'Central Bank of Kuwait announcements', 'open_web_public', 'public', TRUE),
    ('bh_cbb_media', 'Central Bank of Bahrain media centre', 'open_web_public', 'public', TRUE),
    ('bh_bourse_announcements', 'Bahrain Bourse company announcements', 'open_web_public', 'public', TRUE),
    ('om_cbo_news', 'Central Bank of Oman news', 'open_web_public', 'public', TRUE),
    ('om_fsa_news', 'Oman Financial Services Authority news', 'open_web_public', 'public', TRUE)
ON CONFLICT (source_id) DO UPDATE
SET display_name = EXCLUDED.display_name,
    licence_group = EXCLUDED.licence_group,
    barrier_side = EXCLUDED.barrier_side,
    licensed = EXCLUDED.licensed;

INSERT INTO entitlement_grant (entitlement_group, source_id)
SELECT groups.entitlement_group, source.source_id
FROM (VALUES ('fi_gcc_public'), ('fi_gcc_private')) AS groups(entitlement_group)
CROSS JOIN (VALUES
    ('sa_sama_news'), ('sa_cma_announcements'), ('ae_cbuae_news'), ('ae_cma_updates'),
    ('qa_qcb_news'), ('qa_qfma_news'), ('kw_cbk_press'), ('kw_cbk_announcements'),
    ('bh_cbb_media'), ('bh_bourse_announcements'), ('om_cbo_news'), ('om_fsa_news')
) AS source(source_id)
ON CONFLICT DO NOTHING;
