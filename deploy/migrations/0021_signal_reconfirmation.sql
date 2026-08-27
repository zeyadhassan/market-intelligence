-- A confirmed detector condition is observed on each distinct daily pin even
-- when its coarse ledger status remains confirmed. This immutable
-- reconfirmation owns the full signal projection event for unchanged,
-- strengthened, and weakened analyst lifecycle states.

ALTER TABLE signal_transition
    DROP CONSTRAINT IF EXISTS signal_transition_check;

ALTER TABLE signal_transition
    ADD CONSTRAINT signal_transition_change_or_reconfirmation_check
    CHECK (
        from_status IS NULL
        OR from_status <> to_status
        OR (from_status = 'confirmed' AND to_status = 'confirmed')
    );
