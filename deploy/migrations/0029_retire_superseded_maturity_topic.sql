-- analysis_topic_v4 is an append-only governance ledger. Historical topic
-- versions must not be updated or deleted; PostgresTopicCatalog resolves the
-- newest eligible version by created_at and version. Validate that the
-- observation-only successor was installed, which logically supersedes v1
-- while preserving the policy inputs recorded by historical runs.

DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM analysis_topic_v4
        WHERE topic_id = 'upcoming-maturities'
          AND version = 'topic-v2'
          AND active
    ) THEN
        RAISE EXCEPTION 'active upcoming-maturities topic-v2 is required';
    END IF;
END
$$;
