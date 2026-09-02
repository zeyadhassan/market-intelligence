-- Keep usage accounting aligned with every model component emitted by the runtime.

ALTER TABLE model_call_log
    DROP CONSTRAINT IF EXISTS model_call_log_component_check;

ALTER TABLE model_call_log
    ADD CONSTRAINT model_call_log_component_check
    CHECK (component IN (
        'extract', 'research', 'embedding', 'reranker', 'entailment'
    ));
