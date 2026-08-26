UPDATE source_registry
SET licensed = FALSE
WHERE source_id IN ('sec_edgar_8k', 'fed_press_releases');

DELETE FROM entitlement_grant
WHERE entitlement_group IN ('fi_gcc_public', 'fi_gcc_private')
  AND source_id IN ('sec_edgar_8k', 'fed_press_releases');
