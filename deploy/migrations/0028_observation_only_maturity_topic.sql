-- Keep the developer topic useful without weakening negative-inference gates.
-- The original no-refinancing detectors remain registered, but they require
-- predeclared entity/instrument factual-completeness contracts.  This topic
-- version surfaces only positively asserted maturity and AT1-call facts.

INSERT INTO analysis_topic_v4 (
    topic_id, version, display_name, description, owner, pattern_names,
    required_source_ids, freshness_seconds, detector_policy_version,
    retrieval_policy_version, lifecycle_policy_version, display_order, active,
    created_at
) VALUES (
    'upcoming-maturities', 'topic-v2', 'Upcoming maturities',
    'Material maturities and AT1 calls explicitly supported by current evidence; refinancing absence is not inferred.',
    'fi_gcc',
    ARRAY['upcoming_maturity_observed','at1_call_approaching_observed'],
    ARRAY['sa_sama_news','sa_cma_announcements','ae_cbuae_news','ae_cma_updates',
          'qa_qcb_news','qa_qfma_news','kw_cbk_press','kw_cbk_announcements',
          'bh_cbb_media','bh_bourse_announcements','om_cbo_news','om_fsa_news'],
    86400, 'detector-policy-v2', 'daily-hybrid-v1',
    'opportunity-lifecycle-v1', 10, TRUE,
    TIMESTAMPTZ '2026-09-03 00:00:00+00'
)
ON CONFLICT DO NOTHING;
