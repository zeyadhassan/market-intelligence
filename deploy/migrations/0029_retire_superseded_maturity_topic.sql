-- Keep exactly one active governed version for the maturity topic. Historical
-- runs retain their recorded topic version and detector executions.

UPDATE analysis_topic_v4
SET active = FALSE
WHERE topic_id = 'upcoming-maturities'
  AND version <> 'topic-v2'
  AND active;
