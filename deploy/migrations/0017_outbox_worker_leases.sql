-- Durable concurrent-safe transactional-outbox dispatch state.

ALTER TABLE transactional_outbox
    ADD COLUMN IF NOT EXISTS next_attempt_at TIMESTAMPTZ;
ALTER TABLE transactional_outbox
    ADD COLUMN IF NOT EXISTS lease_owner TEXT;
ALTER TABLE transactional_outbox
    ADD COLUMN IF NOT EXISTS lease_expires_at TIMESTAMPTZ;

UPDATE transactional_outbox
SET next_attempt_at = occurred_at
WHERE next_attempt_at IS NULL;

ALTER TABLE transactional_outbox
    ALTER COLUMN next_attempt_at SET NOT NULL;

CREATE INDEX IF NOT EXISTS transactional_outbox_claim_idx
    ON transactional_outbox (next_attempt_at, occurred_at, event_id)
    WHERE published_at IS NULL;

CREATE TABLE IF NOT EXISTS outbox_dead_letter_v3 (
    dead_letter_id         CHAR(64) PRIMARY KEY CHECK (dead_letter_id ~ '^[0-9a-f]{64}$'),
    event_id               UUID NOT NULL REFERENCES transactional_outbox (event_id),
    event_type             TEXT NOT NULL,
    retryable              BOOLEAN NOT NULL,
    attempt_count          INTEGER NOT NULL CHECK (attempt_count > 0),
    safe_error_summary     TEXT NOT NULL,
    payload_digest         CHAR(64) NOT NULL CHECK (payload_digest ~ '^[0-9a-f]{64}$'),
    quarantined_at         TIMESTAMPTZ NOT NULL,
    UNIQUE (event_id, payload_digest)
);

CREATE INDEX IF NOT EXISTS outbox_dead_letter_retry_idx
    ON outbox_dead_letter_v3 (retryable, quarantined_at);

DROP TRIGGER IF EXISTS immutable_outbox_dead_letter ON outbox_dead_letter_v3;
CREATE TRIGGER immutable_outbox_dead_letter
BEFORE UPDATE OR DELETE ON outbox_dead_letter_v3
FOR EACH ROW EXECUTE FUNCTION reject_agentic_record_mutation();
