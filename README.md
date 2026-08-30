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

## One runtime configuration file

Use Python 3.11 or later. Create a virtual environment and install the locked development
dependencies using the package workflow for your environment. The commands below assume the local
Windows virtual environment at `.venv`.

The canonical service reads its operator-owned settings from [`deploy/app.env`](deploy/app.env).
That file is ignored by Git because it can contain endpoint credentials, identity subjects,
recipient addresses, and an encryption key. Create it once from the complete checked-in template:

```powershell
Copy-Item deploy\app.env.example deploy\app.env
```

Open `deploy/app.env` and replace every `REPLACE_WITH_*` or all-zero value. Its sections cover all
configuration that was previously missing:

- model and embedding endpoint URLs, API keys, exact model IDs, embedding dimension and prefixes;
- immutable release UUIDs and SHA-256 digests for the five governed serving roles;
- the evaluation dataset/report digests, timestamps, responsible operator, and explicit passed gate;
- the real source-request organization/contact, required official-source IDs, and covered-entity
  LEIs;
- OIDC issuer, audience, JWKS URL, subject, server-owned principal, desk, roles, and purposes;
- sandbox email enablement, sender, allowlist and Fernet destination-encryption key; and
- optional OpenTelemetry endpoints.

Use a URL reachable from inside Podman. For endpoints running on this Windows host, use
`host.containers.internal` instead of `127.0.0.1`. Generate release UUIDs with
`[guid]::NewGuid()`. Generate a lowercase SHA-256 with
`(Get-FileHash -Algorithm SHA256 PATH).Hash.ToLowerInvariant()`. Generate the email Fernet key only
when enabling email:

```powershell
.\.venv\Scripts\python.exe -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

`FI_INTEL_MODEL_QUALITY_GATE_PASSED=true` is an operator assertion about the two configured
evaluation digests. Do not set it before that report has actually passed. `MODEL_EVALUATED_AT`
must be no later than `MODEL_REGISTERED_AT`, and both must be timezone-aware ISO-8601 values such
as `2026-08-30T08:00:00+02:00`. Covered entities must be comma-separated, checksum-valid LEIs.

Validate the file without starting Podman services:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py preflight
```

Validation reports every missing or malformed field without printing secrets.

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

Start the full independently restartable topology after `preflight` succeeds:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py app-up
.\.venv\Scripts\python.exe deploy\podman_infra.py status
```

This starts the API, scheduler, source, projection/document-processing, analysis, search, and
delivery processes, plus PostgreSQL/pgvector, Neo4j, and Mailpit. The development raw archive is
mounted explicitly at `.fi-intel/archive`; Mailpit is visible at `http://127.0.0.1:8025/`.
`app-up` fails before application startup if the configuration is incomplete, applies migrations,
then idempotently synchronizes the configured OIDC assignment and five evaluated model releases.
It does not silently substitute fixtures or an ungoverned model.

Open `http://127.0.0.1:8000/`. The API only enqueues durable work and reads PostgreSQL read models; it
does not fetch sources or launch analysis tasks. On first use, the page asks for an OIDC access
token and holds it only in browser session storage.

To enable email, set the three protected email fields in `deploy/app.env`, rerun `app-up`, then
configure one encrypted, allowlisted development recipient inside the delivery container:

```powershell
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app run --rm `
  delivery-worker notification set-email analyst@example.test `
  --topics upcoming-maturities,ratings-capital-pressure `
  --timezone Europe/Berlin --send-time 07:00 --frequency weekdays
```

Pause with `--frequency paused`; append an unsubscribe transition with `--unsubscribe`. Delivery
rechecks the account, current authorization scope, topic subscriptions, preference, kill switch,
and destination immediately before SMTP acceptance.

Inspect logs and durable state without database surgery:

```powershell
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app logs --tail 200
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app run --rm `
  projection-worker operator status
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app run --rm `
  projection-worker operator dead-letters
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app run --rm `
  projection-worker operator replay-outbox DEAD_LETTER_ID
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app run --rm `
  projection-worker operator replay-document DOCUMENT_VERSION_UUID
podman compose --file deploy\compose.yml --env-file deploy\app.env --profile app run --rm `
  projection-worker operator rebuild-graph --confirm REBUILD
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
