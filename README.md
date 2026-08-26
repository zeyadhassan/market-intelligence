# fi-intel

`fi-intel` is a governed market-intelligence pipeline for Financial Institutions coverage.

## Stage 1 live GCC subscription POC

The default Stage 1 command is live and fail-closed. Configure an OpenAI-compatible model endpoint
and an honest HTTP user agent first:

```powershell
$env:FI_INTEL_LLM_BASE_URL="http://127.0.0.1:8001/v1"
$env:FI_INTEL_LLM_API_KEY="your-endpoint-key"
$env:FI_INTEL_RESEARCH_MODEL="your-served-model"
$env:FI_INTEL_RSS_USER_AGENT="YourOrg FI Opportunity Watch contact@YOUR_REAL_DOMAIN"
.\.venv\Scripts\fi-intel.exe demo stage-one
```

Open `http://127.0.0.1:8765/`, choose a topic, inspect the same-page opportunity and evidence, and
record an evaluation. The first result request fetches twelve registered official public pages—two
in each GCC country—and calls the configured model once per successfully fetched source. Candidate
results are rejected unless their entity, date marker, and evidence quote occur in the fetched
source text. The page shows the model/run ID, source-by-source status, fetch time, source hash, and
the number of unsupported model candidates rejected. Subscriptions, cached runs, and evaluations
are kept in memory and reset when the process stops.

The command refuses to start when the model endpoint or a real source-contact user agent is absent;
it never substitutes fixtures while claiming to be live. `complete` means all twelve registered POC
pages fetched and analysed in that run. It does not mean the production issuer-IR, licensed-news,
or rating-agency universe is complete.

For offline UI work and regression tests only, use the explicitly named fixture command:

```powershell
.\.venv\Scripts\fi-intel.exe demo stage-one-fixture
```

## Coverage status

The repository now contains a bounded live POC matrix of twelve official public regulator/market
pages spanning Saudi Arabia, the UAE, Qatar, Kuwait, Bahrain, and Oman. It does **not** contain a
production-ready GCC FIG source universe. SEC current 8-K and Federal Reserve feeds remain
registered for controlled development but disabled by default; they must not be used as a proxy for
GCC coverage volume.

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
