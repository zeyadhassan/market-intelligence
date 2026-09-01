# Developer MVP Architecture and Readiness Plan

Status date: 2026-08-27

## Purpose

This document is the implementation plan for turning `fi-intel` into a developer MVP that can:

1. Continuously ingest a bounded set of current market sources.
2. Preserve source history and derive governed entity, assertion, and signal state.
3. Detect timely financial-institution opportunities using current facts and historical context.
4. Research candidates with graph-guided hybrid retrieval.
5. Publish a small, evidence-grounded daily result set.
6. Show the exact same immutable results on the web and in a daily development email.
7. Support interactive analyst search without weakening daily-product controls.

This is a developer MVP plan, not a production-launch checklist. Full licensing, statistically
qualified performance, unrestricted customer email, enterprise operations, and formal model-risk
approval remain later phases.

## Documentation authority

This file is the sole architecture, product-readiness, and implementation roadmap for the
repository. `README.md` is only the developer entrypoint for setup, commands, and the current
qualification boundary. Point-in-time status reports, parallel roadmaps, and separate architecture
decision files must not be created; update this plan and rely on version control for decision
history.

Checkboxes describe repository implementation, not external qualification. A checked item means
the behavior exists in the current tree and has focused automated evidence. It does not prove live
source coverage, model quality, analyst usefulness, licensing, or production readiness.

## Executive architecture decision

The developer MVP will use a **PostgreSQL-authoritative, graph-guided hybrid RAG architecture**.

Retrieval order depends on the workflow:

- **Daily opportunity discovery:** graph and temporal detectors first, followed by hybrid evidence
  retrieval, bounded graph expansion, contradiction retrieval, reranking, grounded LLM synthesis,
  and deterministic validation.
- **Interactive analyst search:** route by query type; run vector and graph retrieval in parallel
  where appropriate, use exact chunk metadata to enter the graph, perform bounded expansion,
  rerank combined evidence, and then synthesize.

The system will not use vector-only daily discovery and will not allow an LLM to perform arbitrary
or unbounded graph traversal.

The runtime will remain a modular monolith with independently restartable processes. These process
boundaries do not require separate repositories or a network microservice for every stage.

## Accepted architecture decisions

The following decisions were accepted on 2026-08-26 and are normative for the developer MVP:

1. PostgreSQL is the authoritative append-only system of record for source and document versions,
   identity decisions, evidence, assertions, signals, analysis state, investigations, result
   versions, exposures, evaluations, model releases, and audit events.
2. Neo4j is an entitlement-filtered bitemporal projection that can be rebuilt from PostgreSQL. A
   graph projection failure cannot make authoritative work appear complete.
3. Cross-store work begins with one PostgreSQL transaction containing the domain change and a
   deterministic outbox event. Handlers are idempotent by event identity and aggregate version.
4. Daily opportunity discovery is detector-led and graph-first. Interactive search is routed among
   graph-first, vector-first, and mixed plans. Both modes use bounded typed tools and the same
   authoritative records.
5. The LLM may choose among allowlisted research actions and synthesize validated evidence. It
   cannot decide authorization, source completeness, identity, temporal truth, materiality
   thresholds, result admission, or delivery eligibility.
6. The canonical UI, API, and email consume the same immutable result version. Synthetic fixtures
   are test-only; no alternate live/prototype runtime command path is supported.

Changing one of these decisions requires editing this section, its affected milestones and gates,
and the corresponding tests in the same change.

## Why this approach is best

Daily opportunity emails require high precision, clear novelty, and defensible absence claims.
Graph and temporal detectors are well suited to conditions such as:

- a material maturity is approaching and no refinancing has been observed;
- a rating deteriorated while capital or liquidity also weakened;
- an issuance programme was approved but has not yet been used;
- a treasury leadership change followed another relevant event; or
- a previous opportunity strengthened, weakened, resolved, or was contradicted.

Vector retrieval has strong semantic recall but cannot reliably establish multi-fact state,
temporal supersession, or absence. Graph detection has strong precision but can miss narrative
evidence. The combined daily pipeline provides the best balance:

```text
structured graph and temporal state
  -> high-precision candidate
  -> pgvector + lexical evidence recall
  -> bounded graph context
  -> explicit contradiction retrieval
  -> reranking and diversity
  -> grounded LLM explanation
  -> deterministic validation
```

Interactive search also needs vector-first retrieval because broad thematic questions may not map
to a predefined detector. Supporting both entry modes avoids forcing one strategy onto every
problem.

## Product quality priorities

Quality is ordered as follows:

1. **Coverage honesty:** never claim nothing changed when required sources or processing are
   incomplete.
2. **Correct entity:** evidence and graph state must belong to the intended legal entity.
3. **Temporal correctness:** current, superseded, future-effective, and late-recorded facts must not
   be confused.
4. **Evidence integrity:** every factual claim must map to exact immutable source text.
5. **Opportunity precision:** the daily list should be small and useful rather than speculative.
6. **Novelty:** unchanged opportunities must not appear as new each day.
7. **Recall:** eligible opportunities should not be missed because one retrieval mode was narrow.
8. **Freshness:** source changes should enter processed intelligence continuously.
9. **Reproducibility:** identical manifests and policies should produce stable identities and
   explainable decisions.

## Product contract

### Governed topics

A topic is a versioned analysis product, not a free-form prompt. The MVP catalog should contain one
or two topics whose coverage and quality can be measured. Each topic requires a stable ID,
user-facing name and description, owner, eligible entities, detector/retrieval policy versions,
required sources, freshness window, ranking and lifecycle policy, display order, and active state.

Analysis runs once for each compatible topic, authorization scope, entity universe, and daily
window. Eligible subscribers share that governed run; analysis must not be rerun per recipient.

### Analyst result

Every admitted result version must expose or retain:

- topic, entity, title, concise opportunity explanation, commercial relevance, and why it is fresh;
- first-seen, last-confirmed, effective, recorded, and analysis timestamps;
- lifecycle state and material score components;
- exact evidence, contradictory evidence, source and document versions, and coverage warnings;
- detector, retrieval, prompt, model, policy, run, and authorization lineage; and
- unknowns, typed falsifier, and material change from the previous result version.

The page and API must distinguish `queued`, `running`, `current results`, `nothing new`,
`incomplete or delayed coverage`, and `failed`. `Nothing new` is a positive statement that is valid
only after required coverage and processing complete.

### Exposure and evaluation

Record an exposure containing the exact result version and display position before accepting
feedback. MVP verdicts are `useful`, `not_relevant`, `incorrect`, `duplicate`, and `too_old`, with
an optional note. Corrections append a superseding event rather than overwriting history.

Evaluation informs offline measurement first. It must not immediately retrain, change a detector,
or cross desks, entitlements, barriers, incompatible policy versions, or future temporal cutoffs.

## Developer MVP scope

The MVP may operate with:

- one development deployment and analyst desk;
- one or two carefully selected topics;
- a small named legal-entity universe;
- a bounded set of approved public GCC sources;
- English-only evidence if the limitation is visible;
- one OpenAI-compatible development model endpoint;
- PostgreSQL/pgvector and the existing Neo4j projection;
- a filesystem archive on a mounted volume;
- a sandbox SMTP service or local mail catcher; and
- allowlisted development recipients only.

The MVP must never:

- describe fixture data as current market intelligence;
- describe bounded sources as complete GCC FIG coverage;
- use Neo4j as the sole record of an assertion, signal, result, or outcome;
- perform source fetching or multi-stage analysis inside a normal HTTP request;
- let an LLM issue arbitrary Cypher or perform unbounded traversal;
- use vector similarity as the only daily detector;
- regenerate analytical prose independently for email;
- send after unsubscribe, pause, entitlement loss, or suppression; or
- report `nothing new` when required coverage is incomplete.

## Current repository status

### Implemented developer MVP

- PostgreSQL is the append-only authority and Neo4j is a rebuildable projection.
- Raw acquisition, source watermarks, immutable document versions, and quarantine state exist.
- A filesystem-backed content-addressed development archive exists.
- PostgreSQL-backed outbox checkpoints, leases, and dead letters exist.
- Entity resolution, extraction, temporal state, coverage, and graph detector components exist.
- Accepted assertions, evidence, and identity decisions are committed through
  `LedgerAssertionAdmissionSink` before `assertion.accepted.v1` projection.
- Signal identities and lifecycle transitions are committed through `LedgerSignalAuthority` before
  `signal.transitioned.v1` projection.
- PostgreSQL/pgvector hybrid lexical and vector retrieval exists.
- Entity-filtered retrieval and structure-aware chunks exist.
- Bounded two-hop allowlisted graph traversal exists.
- Explicit support and contradiction retrieval, reranking, and diversity exist.
- Bounded investigation trajectories and model-call lineage exist.
- Grounding, citation validation, semantic entailment, and publication gates exist.
- Immutable analysis runs, result versions, exposures, and evaluations exist.
- Stable business-window run identity exists.
- Independently restartable source, projection, analysis, search, delivery, and scheduler processes
  share one canonical PostgreSQL-backed runtime-resource bundle.
- The API only enqueues or joins durable jobs and reads PostgreSQL projections; it never owns
  background analysis work.
- The daily workflow consumes frozen processed inputs, waits for authoritative projection
  completion, and materializes its read model before the terminal job transition.
- Exact chunk-to-document/entity/assertion/evidence links bridge authorized pgvector candidates to
  reviewed PostgreSQL authority and bounded Neo4j context.
- PostgreSQL owns logical opportunity lifecycle transitions and daily topic read models. Unchanged
  material fingerprints do not create new result versions.
- Stage One renders durable queue, coverage, lifecycle, evidence, contradiction, uncertainty,
  exposure, and evaluation state from PostgreSQL.
- The one browser/API path verifies OIDC tokens, resolves current server-owned access attributes
  from PostgreSQL, and keeps a pasted development token only in browser session storage. Fixed
  fixture credentials are confined to test-only factories.
- Typed asynchronous entity, pattern, thematic, and mixed GraphRAG search exists with allowlisted
  two-hop traversal and PostgreSQL reauthorization of graph identities.
- Versioned notification preferences, encrypted development destinations, immutable digests,
  escaped templates, immediate pre-send authorization, sandbox SMTP, suppression, retry, and
  acceptance-unknown handling exist.
- Operators can inspect queues and dead letters, replay correlated outbox/document work, and rebuild
  Neo4j from PostgreSQL with an equivalence report.
- The Podman Compose topology mounts the archive explicitly and runs the same independent process
  entry points used on the host. Docker Engine and a parallel Docker deployment path are not used.
- Safe metrics cover source, queue, coverage, retrieval, model, result, and delivery transitions;
  correlation and model-call lineage remain durable.
- Bounded official GCC adapters exist.
- Extraction, reasoning, embedding, reranking, and entailment have governed model-routing
  foundations and call-lineage records.

### Verification status and qualification boundary

- Focused service-free tests cover architecture boundaries, retry/commit ordering, exact lineage,
  lifecycle identity, authorization, bounded search, delivery suppression, immutable page/email
  identity, and graph rebuild accounting.
- Live PostgreSQL/Neo4j tests are mandatory in the complete local verification path through the
  checked-in Podman launcher. `deploy/podman_infra.py test` sets the required-infrastructure flag,
  so a missing integration service fails rather than silently skipping.
- The bounded developer evaluation is a synthetic contract set, not a statistical quality claim.
  Its immutable manifest covers every required lifecycle, temporal, entity, coverage, citation,
  adversarial, failure, authorization, and high-degree-graph label and records its limitations.
- Licensed-source completeness, named-analyst usefulness, Arabic-language quality, production
  statistical confidence, and unrestricted delivery remain in the explicitly deferred
  qualification phase below. They are not represented as implemented or proven here.
- External runtime inputs are consolidated in the checked-in `deploy/app.env.example`; the
  operator copies it to the ignored `deploy/app.env`. `podman_infra.py preflight` validates every
  required endpoint, model release, evaluation digest, coverage, OIDC, access, and enabled-email
  field without printing secrets. `app-up` then synchronizes the configured access assignment and
  evaluated model releases before starting the one application profile.
- The server model route is now concrete rather than placeholder-only: chat uses the pulled UAT
  OpenAI-compatible `openai/gpt-oss-120b` endpoint, while embeddings use the NVIDIA NIM
  `/v1/embeddings` route with `nvidia/llama-3.2-nv-embedqa-1b-v2`, query/passage `input_type`
  semantics, explicit proxy/TLS policy, and a 2,048-dimensional pgvector migration with a
  half-precision HNSW expression index.
  `deploy/model_smoke.py` checks both connections from the operator-owned env file without printing
  credentials or model output.

## Target runtime architecture

```text
Official and licensed sources
            |
      source workers
            |
  raw archive + PostgreSQL ledger
            |
     transactional outbox
            |
 document-processing/projection workers
    |          |                 |
 pgvector   PostgreSQL       Neo4j projection
 chunks     assertions       and traversal
    |          |                 |
    +----------+-----------------+
               |
       daily analysis worker
 graph/time detectors -> hybrid research -> validation
               |
 logical opportunity + immutable result versions
        |                              |
 read-focused API              digest/delivery worker
        |                              |
 analyst page                  sandbox email provider
        |
 durable evaluation and outcome feedback
```

### API process

- Authenticate and authorize.
- Read topics, subscriptions, runs, opportunities, evidence, and evaluations.
- Append subscription and evaluation transitions.
- Insert or join durable analysis/search requests.
- Return `202 Accepted` for incomplete work.
- Never own source polling, extraction, projection, research, or delivery tasks.

### Source worker

- Poll registered sources independently at their configured cadence.
- Respect cursors, conditional requests, limits, and retry policy.
- Archive raw bytes before parsing.
- Commit raw assets and document versions to PostgreSQL.
- Emit deterministic transactional-outbox events.
- Record operational freshness and completeness.

### Document-processing worker

- Claim document events durably.
- Load immutable archived bytes and document versions.
- Create chunks and embeddings.
- Resolve entities.
- Extract evidence spans, candidates, and assertions.
- Commit accepted intelligence to PostgreSQL.
- Emit graph-projection events.
- Quarantine ambiguity and invalid work visibly.

### Projection worker

- Consume projection events idempotently.
- Project PostgreSQL identities and temporal state into Neo4j.
- Preserve assertion and signal lineage.
- Support a complete rebuild from PostgreSQL.

### Daily analysis worker

- Claim one durable business-window job.
- Freeze the processed input manifest and temporal pin.
- Assess operational and factual coverage.
- Run graph and temporal detectors.
- Research candidates through daily graph-first hybrid RAG.
- Validate and admit immutable result versions.
- Update logical opportunity lifecycle and daily read models.

### Delivery worker

- Select due allowlisted recipients.
- Re-check identity, subscription, entitlement, pause, and suppression.
- Assemble a digest from admitted result versions.
- Render deterministic HTML and text.
- Send using a durable idempotency key.
- Persist delivery transitions and failures.

## Authoritative data design

### PostgreSQL/pgvector owns

- source registry and coverage policy;
- raw identities and archive pointers;
- document identities and versions;
- chunks, embeddings, and index versions;
- entity identities, aliases, and resolution decisions;
- evidence spans and claim candidates;
- assertions and valid/recorded-time state;
- signal identities and transitions;
- investigations and validation decisions;
- logical opportunities and result versions;
- subscriptions and notification preferences;
- analysis, search, digest, and delivery jobs;
- exposures, evaluations, outcomes, and audit events; and
- outbox events and handler checkpoints.

### Neo4j contains only rebuildable projections for

- typed entity relationships;
- current and as-of assertion neighborhoods;
- bounded multi-hop paths;
- registered graph detector templates;
- timelines and time series;
- resolved opportunity precedents; and
- relationship explanations.

Every graph object influencing a result must reference stable PostgreSQL identities.

The end-to-end identity chain is:

```text
raw_asset_id -> document_version_id -> entity_id -> assertion_id -> signal_id
-> investigation_id -> logical_opportunity_id -> result_version_id
-> exposure_id -> evaluation_id
```

Digest and delivery identities branch from immutable result versions. Identifiers must include the
immutable business inputs and relevant policy/artifact lineage needed for idempotent replay; they
must never depend on database insertion order or model-authored prose alone.

### Raw archive

The archive owns immutable bytes only. PostgreSQL owns their identities, hashes, policy, and
lineage. The MVP may use a mounted filesystem and later replace it with an S3-compatible adapter.

## PostgreSQL-first write paths

Assertions must follow:

```text
document version
  -> extraction response
  -> validated evidence and candidates
  -> entity decisions and admitted assertions
  -> one PostgreSQL transaction
       - authoritative records
       - deterministic outbox event
  -> Neo4j projection handler
```

Signals must follow:

```text
registered detector query
  -> candidate and matched assertion IDs
  -> lifecycle classification
  -> PostgreSQL signal transition transaction
  -> projection event
  -> Neo4j signal projection
```

Governed modes must fail startup if extraction or detection writes authoritative state directly to
Neo4j.

## GraphRAG design

### Daily graph-first research

#### 1. Deterministic candidates

- Run only registered, versioned detector templates.
- Query as of the daily temporal pin.
- Enforce entity, authorization, source, and factual-coverage prerequisites.
- Record matched assertion IDs, source IDs, pattern version, coverage, and score contributions.
- Suppress absence candidates unless required coverage is complete.

#### 2. Exact graph entry

- Enter using a reviewed stable identifier, preferably LEI.
- Abstain on ambiguous identity.
- Never ask the LLM to infer a graph key when an authoritative ID exists.

#### 3. Bounded graph context

- Load the authorized entity profile as of the temporal pin.
- Traverse at most two hops for the MVP.
- Allow only policy-approved relationship families.
- Bound nodes, assertions, paths, and elapsed time.
- Retrieve time-series changes and outcome-qualified precedents where relevant.
- Record templates, parameter digests, returned identities, and truncation.

#### 4. Hybrid support retrieval

- Build the query from entity, aliases, signal pattern, triggering facts, and material terms.
- Filter authorization, source, entity, class, language, and dates before ranking.
- Retrieve lexical and vector candidates from PostgreSQL.
- Use the canonical entity first and related entities only through an explicit fallback.
- Broaden to the corpus only with identity-preserving checks.
- Preserve document version and exact chunk coordinates.

#### 5. Explicit contradiction retrieval

- Run a separate contradiction query.
- Search for withdrawal, cancellation, correction, supersession, completion, refinancing, denial,
  and pattern-specific falsifiers.
- Search graph state for superseding assertions.
- Preserve contradiction evidence even when it weakens the thesis.
- Abstain or downgrade when material contradiction is unresolved.

#### 6. Fusion, diversity, and reranking

- Deduplicate by text fingerprint, evidence span, and document version.
- Preserve source and document diversity.
- Combine graph evidence, hybrid candidates, contradictions, and precedents.
- Use deterministic reciprocal-rank fusion before governed reranking.
- Do not lock fusion weights before retrieval evaluation.
- Retain component scores and final admission reasons.

#### 7. Grounded synthesis

The LLM receives one hypothesis, typed graph context, historical state, support and contradiction
evidence, precedents, unknowns, coverage warnings, and a strict schema. It may summarize and explain
commercial relevance. It may not introduce facts outside the evidence bundle.

#### 8. Validation and admission

- Validate evidence indices and source coordinates.
- Ground entity, date, amount, timing, materiality, and commercial angle.
- Verify semantic entailment where necessary.
- Check title/summary consistency.
- Require a typed falsifier.
- Re-check temporal validity, authorization, and coverage.
- Publish only supported versions; otherwise abstain, hold, or defer.

### Bounded investigation state machine

Daily and interactive research share a durable, evidence-first investigation protocol:

1. Authorize the signal or query and freeze its temporal and coverage context.
2. Resolve a safe graph entry or record identity ambiguity.
3. Form typed hypotheses and their required supporting and contradicting evidence.
4. Invoke only allowlisted graph, retrieval, time-series, and precedent tools.
5. Assess support, contradiction, materiality, and remaining uncertainty.
6. Draft atomic claims and a typed falsifier from the admitted evidence bundle.
7. Validate fields, entailment, temporal state, authorization, and coverage.
8. Terminate as `supported`, `contradicted`, `held`, `abstained`, `budget_exhausted`, or `failed`.

Policy fixes maximum steps, calls by tool type, graph hops/nodes/paths, retrieved chunks and
per-document concentration, tokens, cost, latency, retries, and total deadline. Persist each tool
request, typed response, artifact version, budget update, and stop reason. Repeated equivalent calls
must be blocked. A tool failure creates a typed partial or failed state, never an invented answer.

### Routed interactive GraphRAG

Interactive search is a separate use case over the same stores. It does not replace daily
detectors.

| Route | Example | Initial retrieval |
|---|---|---|
| Entity-centric | "What changed at Bank X?" | Entity resolution, graph first |
| Pattern/temporal | "Which banks have near-term maturities?" | Registered graph template first |
| Thematic | "Find signs of liquidity pressure" | Hybrid vector/lexical first |
| Mixed | "Why might Bank X issue soon?" | Graph and vector in parallel |

Routing may use deterministic rules plus one bounded structured model call. Record the selected
route and its confidence.

#### Typed retrieval plan

The planner may produce only a validated schema:

```json
{
  "route": "mixed",
  "query": "liquidity and refinancing pressure",
  "seed_entity_ids": ["LEI"],
  "relationship_types": ["ISSUED", "HAS_MATURITY", "HAS_RATING"],
  "max_hops": 2,
  "date_from": "2025-08-27",
  "date_to": "2026-08-27",
  "include_support": true,
  "include_contradictions": true,
  "candidate_limit": 50,
  "final_evidence_limit": 12
}
```

Application code validates:

- node and relationship allowlists;
- maximum two hops;
- authorization and barriers;
- temporal and source bounds;
- candidate/evidence limits;
- model-call, token, and time budgets; and
- duplicate-call prevention.

The LLM never sends arbitrary Cypher.

#### Vector-seeded graph entry

1. Retrieve hybrid candidates.
2. Read exact entity, assertion, evidence, and document-version IDs from metadata.
3. Rank graph seeds by retrieval score, resolution confidence, recency, and source quality.
4. Expand only the highest-ranked authorized seeds.
5. Fetch authoritative PostgreSQL records for graph identities.
6. Run support and contradiction retrieval around expanded context.
7. Deduplicate, rerank, and synthesize with citations.

### Required chunk metadata

Every chunk must be traceable to:

```text
chunk_id
document_id
document_version_id
source_id and source_url
entity_ids[]
assertion_ids[]
evidence_span_ids[]
document_class and language
published_at and recorded_at
valid_from / valid_to where applicable
policy_id / entitlement scope
section path and character offsets
content hash
chunker version
embedding release and dimension
```

Use normalized link tables where arrays weaken integrity or query performance. The vector index
stores searchable content and references, not duplicate authoritative assertions.

### Graph projection requirements

Project supported entities, instruments, programmes, ratings, maturities, capital/liquidity
metrics, leadership changes, evidence-backed assertions, supersession intervals, signal
observations, and outcome references.

Every graph query applies authorization scope, barrier side, knowledge time, valid time,
supersession, source allowlist, and bounded limits.

## Opportunity lifecycle

Create a PostgreSQL logical opportunity aggregate. Daily results are immutable versions of that
aggregate, not unrelated objects each day.

Required states:

- `new`: no prior logical opportunity;
- `updated`: material evidence, score, timing, coverage, or interpretation changed;
- `unchanged`: supported with no material change;
- `weakened`: support remains but materiality decreased;
- `contradicted`: new evidence conflicts materially;
- `resolved`: the condition ended or expected action occurred;
- `suppressed`: policy or analyst disposition prevents resurfacing; and
- `held`: evidence, authorization, or coverage is insufficient.

Derive stable identity from governed topic/pattern, entity, instrument/subject, authorization
scope, and policy lineage. Create a new result version only when canonical output or material state
changes.

Email eligibility is limited to new, materially updated/weakened, contradicted/resolved updates for
previously exposed opportunities, and explicit coverage notices according to preference. Unchanged
opportunities remain on the page but do not appear as new.

Signal/opportunity resolution and business outcome are different facts. Track at least
`condition_resolved`, `opportunity_won`, `opportunity_lost`, `not_actionable`, and
`unknown_outcome` separately. Precedent retrieval may use only episodes with a governed analyst
disposition or objective linked outcome; disappearance of a detector condition is not evidence of
commercial success.

## Coverage and daily cutoff

Configure source cadence, daily cutoff, grace period, analysis deadline, and digest time.

Freeze a manifest containing business date, temporal pin, topic/detector versions, authorization
scope, entity/source universe digests, completed source observations, included document versions,
model/index releases, policies, and prior result versions used for comparison.

`Nothing new` is valid only when every required source and processing job is complete. Late data
updates the page and next digest, or follows a separately defined alert path; it does not silently
change a frozen daily result.

The MVP source contract must name its legal entities, aliases, jurisdictions, topics, required
source classes, permitted use, expected cadence, silence SLA, retention rule, and operational
owner. Fetch success is not factual completeness: a landing page with no admitted detail documents
cannot justify a negative inference. Full source bytes and discovery metadata must be archived
before parsing, and source-to-admission latency, schema drift, unexpected silence, and document
quality must remain visible.

Automatic entity links need conservative jurisdiction/sector blocking, exact identifiers where
available, a measured winner margin, and a held/review state for ambiguity. Preserve alias
provenance, effective dates, parent/subsidiary relationships, renames, and reversible review
decisions. Deduplicate exact and near-duplicate documents across sources without discarding source,
licence, entitlement, or revision provenance; use a bounded/indexed method before corpus growth
makes pairwise comparison unsafe.

## API and read models

### Durable analysis requests

- Replace API `asyncio.create_task()` ownership with an `analysis_job` record.
- Use a unique business-window idempotency key.
- Let API and scheduler insert or join the job.
- Let a worker claim it with a durable lease.
- Store queued, running, complete, partial, held, deferred, retryable-failed, and terminal-failed
  transitions.
- Recover stale leases after worker termination.

### Daily-topic read model

Materialize topic/business date, run/scope, coverage summary, latest source time, lifecycle counts,
ordered result-version IDs, and safe failure messages. Page and digest assembly read the same model.

### Stage One analyst workflow

1. An authenticated analyst sees the small authorized topic catalog and current subscriptions.
2. Selecting or saving a topic queues or joins today's durable analysis job.
3. The same page shows its explicit run/coverage state and any admitted prior result while work is
   in progress.
4. A result summary shows entity, opportunity, why now, materiality, freshness, and lifecycle.
5. Expansion shows exact evidence, contradictory evidence, unknowns, temporal state, coverage,
   falsifier, source links, and an analyst-safe investigation trace.
6. The analyst records an exposure-bound evaluation without leaving the page.

Keep administration, raw prompts, and deep operational diagnostics outside this primary workflow.
The page must remain keyboard accessible and readable on mobile-sized viewports even though the
first target is a desktop analyst workflow.

### Interactive search API

- `POST /v1/searches` validates and queues a governed search.
- `GET /v1/searches/{id}` returns state and final evidence/answer.
- Results include route, temporal pin, sources, graph paths, citations, contradictions, unknowns,
  and model/policy lineage.
- Saved searches are not automatically admitted daily opportunities.

## Daily email design

### Preferences

Store versioned channel state, verified destination reference, IANA timezone, local send time,
frequency, pause, topics, no-result preference, unsubscribe, and link-only policy.

### Assembly

- Build one digest across compatible topics.
- Reference immutable result versions.
- Re-check authorization before assembly and sending.
- Group by topic and mark lifecycle state.
- Include evidence links and coverage warnings.
- Render sensitive content link-only when required.
- Never ask an LLM to rewrite result claims for email.
- Permit `nothing new` only with complete coverage and opt-in.

### Delivery

- Use Mailpit/local SMTP or an approved sandbox provider.
- Require global kill switch and recipient allowlist.
- Key idempotency by recipient, local business date, scope, and digest version.
- Persist queued, rendered, accepted, observed-delivered, retryable-failed, permanent-failed, and
  suppressed transitions as supported.
- Re-check unsubscribe, pause, account, entitlement, and suppression immediately before send.

## Code structure changes

Target incremental boundaries:

```text
fi_intel/
  domain/
    sources/ documents/ entities/ intelligence/
    opportunities/ subscriptions/
  application/
    ports/ polling/ document_processing/ projection/
    daily_analysis/ search/ result_admission/ delivery/
  infrastructure/
    postgres/ neo4j/ archive/ models/ email/
  interfaces/
    api/ cli/ workers/
  tests/
```

Do not perform a large mechanical rename before the canonical write path works. Refactor along
completed vertical slices.

### Required ports

- `RawArchive`
- `SourceOperationsStore`
- `IntelligenceLedger`
- `AssertionAdmission`
- `ProjectionEventStore`
- `GraphProjection`
- `SignalDetector`
- `SignalRepository`
- `CorpusIndex`
- `EvidenceRetriever`
- `GraphRetriever`
- `Reranker`
- `InvestigationStore`
- `AnalysisJobStore`
- `OpportunityRepository`
- `DailyTopicReadModel`
- `SubscriptionRepository`
- `DigestRepository`
- `DeliveryProvider`

The application layer must not import HTTP request models or concrete database clients. Move
principal/access context into governance/domain and adapt API authentication into it.

### Shared resources

```python
class RuntimeResources:
    postgres_pool: asyncpg.Pool
    neo4j_driver: AsyncDriver
    raw_archive: RawArchive
```

- Use one PostgreSQL pool and Neo4j driver per process.
- Use explicit PostgreSQL units of work for shared transactions.
- Let process entry points own readiness, lifetime, telemetry flush, and shutdown.
- Stop modules reaching into `GraphClient._driver`; expose query/projection operations.

### Single runtime path

- Keep synthetic fixture implementations reachable only from tests.
- Prevent canonical application and process entry points from importing fixture modules.
- Do not expose prototype ingestion, direct corpus-search, direct detector, fixture entity, or
  manual indexing commands beside the worker-owned workflow.
- Maintain one governed source-to-result path through the API, scheduler, and durable workers.
- Maintain one operator-owned runtime configuration at `deploy/app.env`; keep its complete safe
  template at `deploy/app.env.example` and reject placeholders before application startup.

## Data-model additions

Reuse existing tables where semantics match and add migrations where they do not.

- Chunk-to-entity, chunk-to-assertion, and chunk-to-evidence links.
- Durable source/document worker job and lease state where not already represented.
- Projection version/checkpoint state supporting rebuild.
- Canonical PostgreSQL signal transition usage.
- Logical opportunity and lifecycle transition records.
- Daily-topic read model.
- Analysis job request/lease/transitions.
- Search request, plan, trajectory, and result records.
- Notification preference and destination records.
- Digest, item, delivery attempt/transition, and suppression records.

Define deterministic identities for source revisions, document versions, chunks, evidence, entity
decisions, assertions, signals, opportunities, results, analysis windows, searches, exposures,
digests, and delivery attempts.

## Model and prompt governance

- Resolve extraction, embedding, reranking, entailment, routing, and reasoning through governed
  releases, including in development mode.
- Record immutable artifact or endpoint revision, tokenizer/quantization where relevant, prompt
  and schema digests, preprocessing/chunker version, tool-contract version, inference settings,
  latency, usage, cost, and typed failure outcome.
- Changing an artifact, prompt, schema, preprocessing rule, or material policy creates new lineage.
- Candidate or failed releases cannot publish. Support deterministic rollback, shadow comparison,
  and canary identities without rewriting historical output.
- Do not show model self-confidence as a probability. Use evidence-strength and uncertainty
  categories until calibration is independently established.

## Implementation plan

Implement in order. Do not start unrestricted email or broad interactive search before the daily
canonical path passes its gates.

### Milestone 0: architecture guardrails

- [x] Record the PostgreSQL-authoritative data-plane decision in this canonical plan.
- [x] Record daily graph-first and routed interactive retrieval decisions in this plan.
- [x] Define the canonical UI path and isolate the fixture from governed operating modes.
- [x] Add tests preventing application-to-API and canonical-to-test-fixture imports.
- [x] Make governed runtime validation reject direct authoritative Neo4j writes.
- [x] Define process entry points and shared resource ownership.

**Exit:** every authoritative commit location and workflow owner is explicit.

### Milestone 1: PostgreSQL authority

- [x] Introduce `AssertionAdmission`.
- [x] Convert extracted claims into ledger evidence, entity decisions, and assertions.
- [x] Commit authoritative assertion and signal records with projection events.
- [x] Use `AssertionWriter` only behind the canonical assertion projection handler.
- [x] Persist signal identity/lifecycle through PostgreSQL.
- [x] Project signal transitions from outbox events in the canonical coordinator.
- [x] Rebuild Neo4j entirely from PostgreSQL and verify equivalence.

**Exit:** Neo4j deletion/rebuild needs no refetch, model rerun, or analyst-state loss.

### Milestone 2: continuous workers

- [x] Extract source polling from the daily coordinator.
- [x] Add source-worker `--once` and long-running modes.
- [x] Extract outbox/document processing into a worker.
- [x] Mount the development archive explicitly.
- [x] Inject shared database resources.
- [x] Add retry scheduling, dead-letter inspection, replay, and stale-lease recovery.
- [x] Test concurrent claims and aggregate ordering.

**Exit:** new source data becomes indexed intelligence while API and daily worker are stopped.

### Milestone 3: hybrid retrieval links

- [x] Add exact chunk/entity/assertion/evidence/document links.
- [x] Populate links idempotently.
- [x] Preserve authorization and temporal metadata.
- [x] Implement vector-hit-to-graph-seed resolution.
- [x] Retain lexical/vector scores.
- [x] Add deterministic fusion, diversity, deduplication, and reranking.
- [x] Add pattern-specific contradiction queries and supersession checks.
- [x] Evaluate fallback tiers separately.

**Exit:** every vector result traces to an immutable document and exact reviewed graph entry.

### Milestone 4: canonical daily graph-first RAG

- [x] Reduce the daily coordinator to a thin workflow over processed inputs.
- [x] Add durable daily job and lease.
- [x] Remove API background task ownership.
- [x] Freeze input manifest and temporal pin.
- [x] Run detectors only with sufficient coverage.
- [x] Run support, contradiction, graph context, fusion, reranking, synthesis, and validation.
- [x] Persist full detector/retrieval/investigation/model/validation lineage.
- [x] Implement opportunity lifecycle comparison.
- [x] Materialize only new or changed results and the daily-topic read model.

**Exit:** unchanged inputs create no new version; material updates are explainable.

### Milestone 5: Stage One loop

- [x] Enqueue/join durable analysis jobs from API.
- [x] Read all job states from PostgreSQL.
- [x] Render lifecycle and coverage states.
- [x] Show evidence, temporal state, contradictions, unknowns, and trace summary.
- [x] Record governed exposures and evaluations.
- [x] Coalesce compatible analysis while filtering per user.

**Exit:** an authenticated analyst completes subscribe, view, evidence, and evaluation in the UI.

### Milestone 6: development email

- [x] Add preferences and verified development destinations.
- [x] Calculate due recipients with IANA timezones.
- [x] Add digests/items referencing immutable results.
- [x] Add deterministic escaped templates.
- [x] Add sandbox delivery, allowlist, and kill switch.
- [x] Add idempotency and durable transitions.
- [x] Re-check authorization immediately before send.
- [x] Test provider-acceptance crash and unsubscribe race.

**Exit:** one recipient gets at most one digest and every statement matches the page result.

### Milestone 7: interactive GraphRAG

- [x] Define typed plan schema and policy.
- [x] Implement entity, pattern, thematic, and mixed routing.
- [x] Run parallel vector/graph seeds for mixed queries.
- [x] Implement vector-seeded bounded expansion.
- [x] Fetch PostgreSQL authority for graph identities.
- [x] Run support/contradiction passes and durable trajectories.
- [x] Add asynchronous search API and result view.
- [x] Prevent arbitrary Cypher.
- [x] Keep search answers separate from admitted opportunities.

**Exit:** routed retrieval improves recall without reducing correct-entity or citation precision.

### Milestone 8: recovery and handoff

- [x] Add complete source-to-page-to-email vertical-slice test.
- [x] Add crash/retry tests at each durable boundary.
- [x] Add graph rebuild, archive replay, and stale-lease tests.
- [x] Test authorization through page and email.
- [x] Test adversarial source content, HTML, prompts, and query plans.
- [x] Add source, queue, coverage, retrieval, model, result, and delivery metrics.
- [x] Add correlation tracing and operator inspection/replay commands.
- [x] Write start/reset/run/recover runbook.
- [x] Run bounded labelled evaluation and record limitations.

**Exit:** another developer can run, inspect, fail, recover, replay, and verify the workflow.

## Evaluation and MVP gates

The labelled set must include new, updated, unchanged, resolved, contradicted, ambiguous,
superseded, future-effective, late-recorded, incomplete-coverage, wrong-entity, support, and
contradiction cases. It must also include:

- similar bank, subsidiary, branch, holdco/opco, issuer/instrument, and asset-manager names;
- Arabic legal names and English transliterations when Arabic is in scope;
- revised, withdrawn, cancelled, completed, and superseded announcements;
- a refinancing fact hidden by a temporarily unavailable required source;
- copied or syndicated content, repetitive boilerplate, tables, and malformed documents;
- citations that mention the entity but do not support the predicate;
- valid excerpts paired with fabricated amount, currency, date, or status;
- prompt injection inside source content, model refusal, malformed output, timeout, and tool outage;
- public/private barrier crossover attempts and high-degree graph entities; and
- signal disappearance without evidence of a successful commercial outcome.

Evaluation data must use immutable manifests, documented labels, source/entity/time separation,
reviewer agreement where judgment is involved, and a holdout not used for prompt or threshold
tuning. Report sample counts and confidence intervals where they are meaningful.

These are developer gates, not production statistical claims:

| Area | Gate |
|---|---|
| Coverage honesty | Zero false `nothing new` states under incomplete coverage |
| Entity precision | At least 98% auto-link precision; otherwise hold |
| Correct-entity retrieval | At least 99% of admitted evidence belongs to intended entity |
| Evidence recall | At least 90% of labelled support/contradiction evidence is in pre-LLM candidates |
| Citation integrity | 100% of factual claims map to immutable coordinates |
| Entailment | 100% of published claims pass deterministic or semantic support |
| Temporal correctness | Zero known current-state errors in the labelled set |
| Daily usefulness | At least 80% of top results rated useful and correct by named reviewers |
| Duplicate control | Zero unchanged opportunities presented as new |
| Page/email integrity | 100% of email items match page result versions |
| Delivery idempotency | Zero duplicate digests in restart/retry tests |
| Authorization | Zero unauthorized evidence, results, or sends in tests |

Report graph detectors, lexical, vector, fusion, reranking, contradictions, routed search, and final
admission separately. Do not hide a weak component behind an end-to-end average.

## Observability requirements

Record safe structured telemetry for source freshness, archive/document identity, outbox lag and
leases, entity decisions, extraction rejection, projection lag, detector coverage, retrieval route
and ranks, graph templates and bounds, investigation steps, model lineage and usage, admission
reasons, lifecycle transitions, digest authorization, and delivery status.

Never log credentials, tokens, raw private content, full model prompts, or destinations.

## Security constraints

- Apply source-origin and redirect allowlists.
- Treat source/retrieved text as data, never instructions.
- Escape page and email content.
- Validate all model responses against strict schemas.
- Never execute model-generated SQL, Cypher, URLs, or code.
- Enforce authorization at retrieval, graph query, result, page, digest, and send.
- Keep public/private scopes distinct in identities and caches.
- Require email allowlist and kill switch.
- Fail closed on unknown coverage, model release, index, policy, or temporal state.

## Deferred beyond developer MVP

- complete GCC FIG source licensing and coverage;
- production statistical qualification and model-risk approval;
- unrestricted customer email and sender reputation;
- full provider bounce/complaint/webhook operations;
- enterprise provisioning and administration;
- full Arabic qualification unless selected sources require it;
- autoscaling, multi-region failover, disaster recovery, and production SLOs;
- a full-quarter shadow period and formal desk approval;
- penetration, compliance, and records-management approval; and
- broad expansion beyond the declared MVP universe.

Deferral does not permit claims of complete coverage or production readiness.

## Post-MVP qualification handoff

After the developer MVP gates pass, advance in this order:

1. Retrospective replay on a locked, licensed, independently labelled historical corpus.
2. Prospective daily dark runs with no analyst-facing publication or external delivery.
3. Analyst shadow operation beside the existing manual process.
4. A limited named-user pilot with explicit approval and kill switches.
5. Controlled production activation after data, desk, model-risk, compliance, security, and
   operations sign-off.

Production thresholds are deliberately not MVP completion criteria. Before pilot, owners must set
predeclared lower-confidence-bound gates by topic, country, language, source class, entity type,
and evidence age. At minimum they must cover source SLA completion, entity-link precision,
material-field extraction precision/recall, retrieval recall and ranking, claim entailment,
opportunity precision/recall, correct abstention, authorization, reproducibility, run completion,
duplicate delivery, analyst verification time, and usefulness. The local release verification
command must keep PostgreSQL and Neo4j integration tests mandatory rather than silently skipped.

## Developer MVP definition of done

The MVP is complete only when:

- continuous source/document workers operate independently from API and daily analysis;
- PostgreSQL owns evidence, assertions, signals, opportunities, results, exposures, and evaluations;
- Neo4j rebuild requires no refetch or model rerun;
- pgvector chunks carry exact graph-entry metadata;
- daily discovery uses graph-first detection and hybrid support/contradiction retrieval;
- traversal is typed, authorized, temporal, allowlisted, and bounded;
- the LLM uses only the supplied evidence bundle;
- daily runs are durable, coalesced, pinned, and restart-safe;
- unchanged inputs produce no new opportunity or digest candidate;
- incomplete coverage cannot produce a false absence claim;
- page and email use the same immutable result versions;
- delivery is allowlisted, suppressible, idempotent, and restart-safe;
- interactive search routes correctly among graph-first, vector-first, and mixed retrieval;
- every result traces from source bytes to exposure and evaluation;
- bounded developer quality gates pass; and
- another developer can execute and recover the workflow from checked-in documentation.
