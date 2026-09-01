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

## One-command start on Windows

From the repository root, run:

```powershell
.\run.cmd
```

On first use, this automatically creates `deploy/app.env` with the supplied chat and NVIDIA
embedding gateways. There are no OIDC/JWKS, LEI, evaluation-dataset, artifact, release-ID, or model
admin fields. The same command checks both gateways, starts Podman when needed, applies migrations,
starts every service, and opens `http://127.0.0.1:8000/`. The browser uses one built-in local analyst
and never asks for a token.

To fill and validate `deploy/app.env` without starting anything:

```powershell
.\run.cmd --configure-only
```

## One runtime configuration file

Use Python 3.11 or later. Create a virtual environment and install the locked development
dependencies using the package workflow for your environment. The commands below assume the local
Windows virtual environment at `.venv`.

The canonical service reads model settings from [`deploy/app.env`](deploy/app.env). It is ignored
by Git because it may contain endpoint credentials. `run.cmd` creates and upgrades it automatically;
manual copying is optional:

```powershell
Copy-Item deploy\app.env.example deploy\app.env
```

The file contains only the chat and embedding endpoint URLs, optional endpoint credentials,
transport settings, model IDs, and the fixed 2,048 embedding dimension. The checked-in values are
already ready for the supplied UAT endpoints, so normally no editing is needed.

Use a URL reachable from inside Podman. For endpoints running on this Windows host, use
`host.containers.internal` instead of `127.0.0.1`.

### Configure the server models

The checked-in template now matches the two UAT connection examples:

- chat uses `https://cbq2-svd-dsgpu2.cbq.com.qa:8443/v1` with
  `openai/gpt-oss-120b` for all four chat roles;
- embeddings use NVIDIA NIM at `https://10.1.94.110:8443/v1` with
  `nvidia/llama-3.2-nv-embedqa-1b-v2` and 2,048 dimensions; the adapter sends
  `input_type=passage` for indexed documents and `input_type=query` for searches; and
- both endpoints bypass environment proxy variables, matching the UAT scripts.

The embedding gateway example does not require an API key or Basic Auth. If the gateway policy
changes, put the credential only in the ignored `deploy/app.env`; do not add it to tracked code.

The UAT scripts disable certificate verification. The template preserves that behavior only so a
`shadow` demo can connect immediately. For `pilot` or `production`, install the internal CA in the
application container trust store and set both `FI_INTEL_LLM_TLS_VERIFY=true` and
`FI_INTEL_EMBEDDING_TLS_VERIFY=true`; preflight rejects insecure TLS in those modes.

Test the two model connections from the operator-owned settings. This check reads `deploy/app.env`,
does not require the database, and never prints credentials or model output:

```powershell
.\.venv\Scripts\python.exe deploy\model_smoke.py
```

To test the new embedding route without depending on the separate chat gateway:

```powershell
.\.venv\Scripts\python.exe deploy\model_smoke.py --embedding-only
```

On a Linux server, use the equivalent commands:

```bash
cp deploy/app.env.example deploy/app.env
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -e '.[dev]'
python deploy/model_smoke.py
python deploy/podman_infra.py preflight
python deploy/podman_infra.py app-up
```

Migration 0024 changes pgvector storage to 2,048 dimensions and clears only the rebuildable
chunk/index projection. It retains full-precision vectors and uses a half-precision HNSW expression
index because pgvector's full-precision HNSW limit is 2,000 dimensions. The projection worker
automatically re-embeds authoritative documents with NVIDIA NIM; source documents, evidence, and
assertions are retained.

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
then starts the application profile. Model lineage is derived automatically from the effective
model and prompt configuration; no model admin setup is required.

Open `http://127.0.0.1:8000/`. The API only enqueues durable work and reads PostgreSQL read models;
it does not fetch sources or launch analysis tasks. The loopback-only browser UI signs in as the
built-in local analyst automatically.

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

Run the complete local suite, with PostgreSQL and Neo4j skips forbidden:

```powershell
.\.venv\Scripts\python.exe deploy\podman_infra.py test
```

Use the local Podman-backed command above as the verification authority; this repository does not
require a hosted CI/CD workflow.
