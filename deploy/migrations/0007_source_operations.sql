-- Registered production sources, restart state, and source-health observations.

INSERT INTO source_registry (
    source_id, display_name, licence_group, barrier_side, licensed
) VALUES (
    'gleif', 'GLEIF LEI reference data', 'open_reference', 'public', TRUE
) ON CONFLICT (source_id) DO NOTHING;

INSERT INTO entitlement_grant (entitlement_group, source_id) VALUES
    ('open_reference', 'gleif'),
    ('fi_gcc_public', 'gleif'),
    ('fi_gcc_private', 'gleif')
ON CONFLICT DO NOTHING;

CREATE TABLE IF NOT EXISTS source_registration_v2 (
    source_id                  TEXT NOT NULL REFERENCES source_registry (source_id),
    catalog_version            TEXT NOT NULL,
    display_name               TEXT NOT NULL,
    source_kind                TEXT NOT NULL CHECK (
                                   source_kind IN (
                                       'feed_detail', 'reference_api', 'reference_bulk'
                                   )
                               ),
    discovery_url              TEXT NOT NULL CHECK (discovery_url ~ '^https://'),
    allowed_origins            TEXT[] NOT NULL CHECK (cardinality(allowed_origins) > 0),
    cadence_seconds            INTEGER NOT NULL CHECK (cadence_seconds > 0),
    freshness_sla_seconds      INTEGER NOT NULL CHECK (
                                   freshness_sla_seconds >= cadence_seconds
                               ),
    silence_sla_seconds        INTEGER NOT NULL CHECK (
                                   silence_sla_seconds >= freshness_sla_seconds
                               ),
    expected_min_items         INTEGER NOT NULL CHECK (expected_min_items >= 0),
    expected_max_items         INTEGER NOT NULL CHECK (
                                   expected_max_items >= expected_min_items
                               ),
    licence_group              TEXT NOT NULL,
    licence_class              TEXT NOT NULL CHECK (
                                   licence_class IN (
                                       'open_government', 'open_reference',
                                       'licensed_vendor'
                                   )
                               ),
    raw_retention_days         INTEGER NOT NULL CHECK (raw_retention_days > 0),
    barrier_side               TEXT NOT NULL CHECK (barrier_side IN ('public', 'private')),
    allowed_entitlement_groups TEXT[] NOT NULL CHECK (
                                   cardinality(allowed_entitlement_groups) > 0
                               ),
    max_feed_bytes             INTEGER NOT NULL CHECK (max_feed_bytes > 0),
    max_detail_bytes           INTEGER NOT NULL CHECK (
                                   max_detail_bytes >= max_feed_bytes
                               ),
    request_timeout_seconds    DOUBLE PRECISION NOT NULL CHECK (
                                   request_timeout_seconds > 0
                               ),
    max_attempts               INTEGER NOT NULL CHECK (max_attempts BETWEEN 1 AND 10),
    max_redirects              INTEGER NOT NULL CHECK (max_redirects BETWEEN 0 AND 10),
    cursor_history_limit       INTEGER NOT NULL CHECK (cursor_history_limit > 0),
    enabled                    BOOLEAN NOT NULL DEFAULT TRUE,
    registered_at              TIMESTAMPTZ NOT NULL DEFAULT now(),
    PRIMARY KEY (source_id, catalog_version)
);

INSERT INTO source_registration_v2 (
    source_id, catalog_version, display_name, source_kind, discovery_url,
    allowed_origins, cadence_seconds, freshness_sla_seconds,
    silence_sla_seconds, expected_min_items, expected_max_items,
    licence_group, licence_class, raw_retention_days, barrier_side,
    allowed_entitlement_groups, max_feed_bytes, max_detail_bytes,
    request_timeout_seconds, max_attempts, max_redirects, cursor_history_limit
) VALUES
    (
        'sec_edgar_8k', 'source-catalog-v1', 'SEC EDGAR current 8-K filings',
        'feed_detail',
        'https://www.sec.gov/cgi-bin/browse-edgar?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=100&output=atom',
        ARRAY['https://www.sec.gov'], 300, 1800, 259200, 0, 500,
        'open_web_public', 'open_government', 2555, 'public',
        ARRAY['fi_gcc_public', 'open_web_public'], 2097152, 16777216,
        15, 3, 3, 1000
    ),
    (
        'fed_press_releases', 'source-catalog-v1',
        'Federal Reserve press releases', 'feed_detail',
        'https://www.federalreserve.gov/feeds/press_all.xml',
        ARRAY['https://www.federalreserve.gov'], 900, 3600, 604800, 0, 100,
        'open_web_public', 'open_government', 2555, 'public',
        ARRAY['fi_gcc_public', 'open_web_public'], 2097152, 16777216,
        15, 3, 3, 1000
    ),
    (
        'gleif', 'source-catalog-v1', 'GLEIF LEI reference data',
        'reference_api',
        'https://api.gleif.org/api/v1/lei-records?page[size]=100',
        ARRAY['https://api.gleif.org', 'https://goldencopy.gleif.org'],
        28800, 43200, 86400, 1, 10000, 'open_reference', 'open_reference',
        2555, 'public', ARRAY['fi_gcc_private', 'fi_gcc_public', 'open_reference'],
        2097152, 16777216, 15, 3, 3, 1000
    )
ON CONFLICT (source_id, catalog_version) DO NOTHING;

CREATE TABLE IF NOT EXISTS source_poll_state_v2 (
    source_id                  TEXT NOT NULL REFERENCES source_registry (source_id),
    partition_key              TEXT NOT NULL DEFAULT 'default',
    cursor_json                JSONB CHECK (
                                   cursor_json IS NULL OR
                                   jsonb_typeof(cursor_json) = 'object'
                               ),
    last_successful_poll_at     TIMESTAMPTZ,
    latest_source_published_at TIMESTAMPTZ,
    consecutive_failures       INTEGER NOT NULL CHECK (consecutive_failures >= 0),
    updated_at                 TIMESTAMPTZ NOT NULL,
    PRIMARY KEY (source_id, partition_key)
);

CREATE TABLE IF NOT EXISTS source_observation_v2 (
    observation_id             UUID PRIMARY KEY,
    run_id                     UUID NOT NULL REFERENCES ingest_run_v2 (run_id),
    source_id                  TEXT NOT NULL,
    partition_key              TEXT NOT NULL DEFAULT 'default',
    catalog_version            TEXT NOT NULL,
    policy_id                  UUID NOT NULL REFERENCES access_policy (policy_id),
    health                     TEXT NOT NULL CHECK (
                                   health IN ('healthy', 'degraded', 'failed')
                               ),
    started_at                 TIMESTAMPTZ NOT NULL,
    finished_at                TIMESTAMPTZ NOT NULL CHECK (finished_at >= started_at),
    feed_modified              BOOLEAN NOT NULL,
    page_count                 INTEGER NOT NULL CHECK (page_count >= 0),
    discovered_count           INTEGER NOT NULL CHECK (discovered_count >= 0),
    acquired_count             INTEGER NOT NULL CHECK (acquired_count >= 0),
    unchanged_count            INTEGER NOT NULL CHECK (unchanged_count >= 0),
    committed_count            INTEGER NOT NULL CHECK (committed_count >= 0),
    not_novel_count            INTEGER NOT NULL CHECK (not_novel_count >= 0),
    quarantine_count           INTEGER NOT NULL CHECK (quarantine_count >= 0),
    complete                   BOOLEAN NOT NULL,
    fresh                      BOOLEAN NOT NULL,
    silent                     BOOLEAN NOT NULL,
    within_expected_volume     BOOLEAN NOT NULL,
    freshness_lag_seconds      DOUBLE PRECISION CHECK (
                                   freshness_lag_seconds IS NULL OR
                                   freshness_lag_seconds >= 0
                               ),
    latest_source_published_at TIMESTAMPTZ,
    error_type                 TEXT,
    error_message              TEXT,
    UNIQUE (run_id, source_id, partition_key),
    FOREIGN KEY (source_id, catalog_version)
        REFERENCES source_registration_v2 (source_id, catalog_version),
    CHECK (
        acquired_count = committed_count + not_novel_count + quarantine_count
    ),
    CHECK (health <> 'failed' OR error_type IS NOT NULL)
);
CREATE INDEX IF NOT EXISTS source_observation_health_idx
    ON source_observation_v2 (source_id, health, finished_at DESC);
CREATE INDEX IF NOT EXISTS source_observation_slo_idx
    ON source_observation_v2 (
        source_id, fresh, complete, silent, within_expected_volume, finished_at DESC
    );

CREATE OR REPLACE FUNCTION protect_source_poll_state_update()
RETURNS TRIGGER LANGUAGE plpgsql AS $$
BEGIN
    IF ROW(NEW.source_id, NEW.partition_key)
       IS DISTINCT FROM ROW(OLD.source_id, OLD.partition_key) THEN
        RAISE EXCEPTION 'source poll state identity is immutable';
    END IF;
    IF NEW.updated_at < OLD.updated_at THEN
        RAISE EXCEPTION 'source poll state cannot move backwards';
    END IF;
    RETURN NEW;
END;
$$;

DROP TRIGGER IF EXISTS source_poll_state_update_guard ON source_poll_state_v2;
CREATE TRIGGER source_poll_state_update_guard
BEFORE UPDATE ON source_poll_state_v2
FOR EACH ROW EXECUTE FUNCTION protect_source_poll_state_update();

DROP TRIGGER IF EXISTS source_registration_append_only ON source_registration_v2;
CREATE TRIGGER source_registration_append_only
BEFORE UPDATE OR DELETE ON source_registration_v2
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();

DROP TRIGGER IF EXISTS source_observation_append_only ON source_observation_v2;
CREATE TRIGGER source_observation_append_only
BEFORE UPDATE OR DELETE ON source_observation_v2
FOR EACH ROW EXECUTE FUNCTION reject_ledger_mutation();
