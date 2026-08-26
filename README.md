# fi-intel

`fi-intel` is a governed market-intelligence pipeline for Financial Institutions coverage.

## Coverage status

The repository does **not** currently contain a production-ready GCC FIG source universe. SEC
current 8-K and Federal Reserve feeds are registered for controlled development, but are disabled
by default because they do not represent the desk's covered institutions. They must not be used as
a proxy for coverage volume.

Production activation requires all of the following:

- a named legal-entity coverage list in `FI_INTEL_COVERED_ENTITY_LEIS`;
- registered local-exchange, central-bank, rating-action, and licensed-news sources that cover
  those entities;
- their IDs in `FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS`;
- healthy, complete source-operation observations through the detector freshness window; and
- explicit source enablement and entitlement grants.

Until those conditions are met, computed coverage checks fail closed and affected detectors do not
fire. The synthetic corpus is regression data only and supports no production quality claim.
LLM self-reported extraction confidence is likewise not treated as a probability: its admission
threshold defaults to disabled until a governed labelled reliability curve earns one.

## Release gates still requiring external inputs

Repository code cannot supply source licences, independent labels, an on-prem model endpoint, or a
quarter of shadow-operation evidence. Production release therefore remains blocked until the desk
adds its real GCC FIG source universe and coverage list, model risk locks a qualifying real-data
holdout, the real-model vertical slice is run on that corpus, and a full-quarter shadow comparison
is completed. Synthetic fixtures remain regression-only throughout.

## Verification

Run `pytest`, `ruff check .`, and `mypy fi_intel`. PostgreSQL/pgvector and Neo4j are required for the
complete suite. Set `FI_INTEL_REQUIRE_INFRA=true` for a full local integration run so missing-service
skips fail the command.
