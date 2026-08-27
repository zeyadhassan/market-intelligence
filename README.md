# fi-intel

`fi-intel` is an evidence-first market-intelligence system for Financial Institutions coverage.
It is currently a developer system under qualification, not a production analyst decision system.

The sole architecture, product-readiness, and implementation roadmap is
[`docs/DEV_MVP_READINESS_GAPS.md`](docs/DEV_MVP_READINESS_GAPS.md). Update that document rather than
creating parallel plans, implementation-status reports, or ADR files.

## Runtime boundary

`fi-intel serve` is the only supported source-to-result service. PostgreSQL is authoritative;
pgvector and Neo4j provide retrieval and rebuildable graph projections. Synthetic fixtures are
test-only and are not exposed as a second runtime command path.

The checked-in official GCC source matrix is bounded development coverage. It is not a licensed or
complete GCC FIG universe, and repository tests do not prove live model quality or analyst utility.

## Environment

Use Python 3.11 or later. Create a virtual environment and install the locked development
dependencies using the package workflow for your environment. The commands below assume the local
Windows virtual environment at `.venv`.

The canonical service fails closed unless its database, authorization, coverage, source-contact,
and governed model configuration is present. A minimal shadow-mode configuration includes:

```powershell
$env:FI_INTEL_ANALYSIS_MODE="shadow"
$env:FI_INTEL_LLM_BASE_URL="http://127.0.0.1:8001/v1"
$env:FI_INTEL_LLM_API_KEY="your-endpoint-key"
$env:FI_INTEL_EMBEDDING_BASE_URL="http://127.0.0.1:8002/v1"
$env:FI_INTEL_EMBEDDING_MODEL="your-multilingual-embedding-model"
$env:FI_INTEL_RSS_USER_AGENT="YourOrg FI Intelligence contact@your-real-domain.example"
$env:FI_INTEL_COVERED_ENTITY_LEIS="529900...,..."
$env:FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS="source_a,source_b,..."
$env:FI_INTEL_OIDC_ISSUER="https://identity.example/"
$env:FI_INTEL_OIDC_AUDIENCE="fi-intel"
$env:FI_INTEL_OIDC_JWKS_URL="https://identity.example/.well-known/jwks.json"
$env:FI_INTEL_ACCESS_SUBJECT="oidc-subject-for-the-development-analyst"
```

The model registry must contain active, gate-passed releases matching the configured extraction,
reasoning, embedding, reranking, and entailment model identities.

## Local infrastructure

Podman owns the local PostgreSQL/pgvector and Neo4j services. On Windows or macOS, initialize and
start a Podman machine, then use the checked-in launcher:

```powershell
podman machine init
podman machine start
.\.venv\Scripts\python.exe deploy\podman_infra.py up
.\.venv\Scripts\python.exe deploy\podman_infra.py status
```

Install the development dependencies before using the launcher. They include `podman-compose`, and
the launcher pins `podman compose` to that provider so Docker Desktop is never used implicitly. If
the CLIs live outside `PATH`, set `FI_INTEL_PODMAN_BIN` and/or
`FI_INTEL_PODMAN_COMPOSE_PROVIDER` to their executable paths. Podman Desktop without the separate
Podman engine/CLI is not sufficient to create the machine.

The launcher waits for service health and applies the checksummed migrations. The migration ledger
is the only schema-bootstrap authority. Stop services while retaining named volumes with:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py down
```

## Run and recover

Start the full independently restartable topology in Podman after setting the governed model,
coverage, and email variables above. When the model endpoints run on the host, use
`host.containers.internal` rather than `127.0.0.1` in their container-visible URLs.

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py app-up
podman compose --file deploy\compose.yml --profile app ps
```

This starts the API, scheduler, source, projection/document-processing, analysis, search, and
delivery processes, plus PostgreSQL/pgvector, Neo4j, and Mailpit. The development raw archive is
mounted explicitly at `.fi-intel/archive`; Mailpit is visible at `http://127.0.0.1:8025/`.

Provision or reactivate the configured OIDC subject through the governed operator command (the
other access attributes default to the development `fi_gcc` analyst assignment and can be set with
the corresponding `FI_INTEL_ACCESS_*` variables):

```powershell
.\.venv\Scripts\fi-intel.exe operator sync-access --confirm ACCESS
```

For process-by-process development, start the canonical service and each owner in separate
terminals:

```powershell
.\.venv\Scripts\fi-intel.exe serve
.\.venv\Scripts\fi-intel.exe scheduler run
.\.venv\Scripts\fi-intel.exe worker source
.\.venv\Scripts\fi-intel.exe worker projection
.\.venv\Scripts\fi-intel.exe worker analysis
.\.venv\Scripts\fi-intel.exe worker search
.\.venv\Scripts\fi-intel.exe worker delivery
```

Every worker also accepts `--once` for a bounded diagnostic run. Open
`http://127.0.0.1:8765/`. The API only enqueues durable work and reads PostgreSQL read models; it
does not fetch sources or launch analysis tasks. On first use, the page asks for an OIDC access
token and holds it only in browser session storage.

Configure one encrypted, allowlisted development recipient after setting
`FI_INTEL_EMAIL_DESTINATION_KEY`, `FI_INTEL_EMAIL_RECIPIENT_ALLOWLIST`, and
`FI_INTEL_EMAIL_ENABLED=true`:

```powershell
.\.venv\Scripts\fi-intel.exe notification set-email analyst@example.test `
  --topics upcoming-maturities,ratings-capital-pressure `
  --timezone Europe/Berlin --send-time 07:00 --frequency weekdays
```

Pause with `--frequency paused`; append an unsubscribe transition with `--unsubscribe`. Delivery
rechecks the account, current authorization scope, topic subscriptions, preference, kill switch,
and destination immediately before SMTP acceptance.

Inspect and recover without database surgery:

```powershell
.\.venv\Scripts\fi-intel.exe operator status
.\.venv\Scripts\fi-intel.exe operator dead-letters
.\.venv\Scripts\fi-intel.exe operator replay-outbox DEAD_LETTER_ID
.\.venv\Scripts\fi-intel.exe operator replay-document DOCUMENT_VERSION_UUID
.\.venv\Scripts\fi-intel.exe operator rebuild-graph --confirm REBUILD
```

Expired worker leases are reclaimed automatically. Outbox replay appends a causally linked event;
archive replay verifies both immutable archive objects before requeueing; graph rebuild clears only
the disposable Neo4j projection and verifies PostgreSQL-equivalent assertion and signal counts.
An SMTP process death during the acceptance window becomes `acceptance_unknown` and is never
automatically resent.

Stop the complete topology while retaining database volumes and the raw archive:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py down
```

For a deliberate database reset, the confirmation phrase is mandatory. This removes the Podman
database volumes but deliberately retains `.fi-intel/archive` for audit/replay safety:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py reset --confirm RESET
.\.venv\Scripts\python.exe deploy\podman_infra.py up
```

If a run is held, inspect `operator status` and source coverage first. A held or incomplete run is
not a valid “nothing new” result. Restore the missing source/model/index dependency, run the
relevant worker once, and let the stale lease or retry schedule resume the same deterministic job.

### Interactive search

`POST /v1/searches` queues a typed governed plan; `GET /v1/searches/{id}` reads its durable state
and final citations. Search routes are entity, pattern, thematic, or mixed. Graph relationships are
allowlisted and traversal is capped at two hops. Search output remains separate from admitted daily
opportunities.

## Verify

Run code-only checks:

```powershell
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\ruff.exe check .
.\.venv\Scripts\ruff.exe format --check .
.\.venv\Scripts\python.exe -m mypy fi_intel
.\.venv\Scripts\python.exe -m pip check
```

Run the release-equivalent suite, with PostgreSQL and Neo4j skips forbidden:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py test
```

Do not record passing test counts in documentation; CI and version control are the status record.
