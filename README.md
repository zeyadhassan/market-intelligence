# Server UAT

The checked-in configuration limits NVIDIA NIM embedding requests to eight
texts per HTTP call. The projection worker commits each completed document
independently, so one failing document no longer rolls back the whole index.
Transient embedding timeouts, connection failures, rate limits, and 5xx
responses receive up to four attempts with bounded exponential backoff.
Permanent request/schema errors still fail immediately and appear with a safe
reason in the control room.

The main page is now an operations-first control room. It refreshes every two
seconds and shows all ten application stages, every configured source, worker
heartbeats, queue depth, retrieval-index coverage, current and recent model
calls, agent/search transitions, delivery state, and payload-safe failure logs.
The page also retains the topic-analysis workflow and adds an on-demand research
form over the governed knowledge base.

On the connected server:

```powershell
git pull origin main
.\run.cmd --configure-only
.\.venv\Scripts\python.exe deploy\model_smoke.py --embedding-only
python deploy/podman_infra.py app-up
python deploy/podman_infra.py diagnose
```

`run.cmd` creates the repository-owned virtual environment and installs the
project dependencies on first use. `app-up` itself needs only Python's standard
library and Podman: it builds the application image, runs preflight and database
migrations inside that image, and then starts the services. It applies
`0027_runtime_observability.sql` before starting the application workers.
After startup, open `/` or `/stage-one`. The underlying authenticated snapshot
is also available at `GET /v1/operations/dashboard?event_limit=200`.

Worker and model visibility starts when the updated processes start. Historical
domain transitions already in PostgreSQL are included in the activity log, but
historical process heartbeats and model-start events cannot be reconstructed.
The browser log intentionally excludes prompts, credentials, private document
text, and raw request/response payloads.

Confirm `deploy/app.env` contains these settings exactly once:

```text
FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS=sa_sama_news
FI_INTEL_EMBEDDING_BATCH_SIZE=8
FI_INTEL_EMBEDDING_MAX_ATTEMPTS=4
FI_INTEL_EMBEDDING_RETRY_BASE_SECONDS=1
```

Do not reset the database for this recovery. Existing completed document jobs
are reusable; after restart, the projection worker resumes the missing index
work. In `diagnose`, `unindexed_document_versions` should descend to zero and
`embedding_calls_last_hour` should show successful calls.

If the embedding smoke test reports that the deployed NIM profile accepts a
smaller maximum batch, set `FI_INTEL_EMBEDDING_BATCH_SIZE=1` in
`deploy/app.env`, rerun the smoke test, and run `app-up` again.

For detailed progress or an exact failure:

```bash
python deploy/podman_infra.py logs --no-follow --tail 500
```

Look for `embed.batch.started`, `embed.batch.retrying`, `embed.batch.failed`,
`retrieval.index.document.completed`, and `retrieval.index.document.failed`.
After `unindexed_document_versions` reaches zero, refresh the application at
`http://127.0.0.1:8000/` (or the configured API host port).

When ready to restore the full governed source set, change:

```text
FI_INTEL_COVERAGE_REQUIRED_SOURCE_IDS=
```
