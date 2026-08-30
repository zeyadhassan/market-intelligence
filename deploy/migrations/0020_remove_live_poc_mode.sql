-- There is no direct live-POC execution path. Non-fixture runs use the
-- canonical governed pipeline and declare only qualification/release state.

ALTER TABLE analysis_run_v3 DROP CONSTRAINT IF EXISTS analysis_run_v3_mode_check;
ALTER TABLE analysis_run_v3 ADD CONSTRAINT analysis_run_v3_mode_check CHECK (
    mode IN ('fixture', 'shadow', 'pilot', 'production')
) NOT VALID;

DO $$
BEGIN
    IF EXISTS (SELECT 1 FROM analysis_run_v3 WHERE mode = 'live_poc') THEN
        RAISE NOTICE 'historical live_poc analysis rows retained; constraint remains NOT VALID';
    ELSE
        ALTER TABLE analysis_run_v3 VALIDATE CONSTRAINT analysis_run_v3_mode_check;
    END IF;
END
$$;
