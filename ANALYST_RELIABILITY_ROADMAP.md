# Subscription Opportunity Product and Reliability Roadmap

Status date: 2026-08-26

## Purpose

This roadmap turns the current `fi-intel` prototype and production foundations into a simple,
subscription-based opportunity service. A user chooses the topics they care about, the system
analyzes fresh information every day, and the user receives a short list of relevant opportunities.

The product is deliberately simple at the surface. Users should not need to understand ingestion,
graphs, detectors, models, or briefs. They choose tags, see fresh results on the same page, and
evaluate whether those results are useful. Email delivery is added only after that loop works.

The system is currently a strong engineering foundation, not a production-ready analyst tool.
Synthetic results demonstrate contracts and workflow only; they do not establish real-world
precision, recall, coverage, or commercial value.

## Target analyst experience

The core user journey is:

1. Sign in.
2. See a short, curated list of topic tags.
3. Select one or more tags and save the subscription.
4. See the latest analysis and opportunities for the selected tag on the same page.
5. Return each day and see only new or materially changed results since the previous run.
6. Open a result to see why it matters, the relevant entity, the exact supporting evidence, the
   analysis timestamp, and any coverage warning.
7. Mark the result `Useful`, `Not relevant`, `Incorrect`, `Duplicate`, or `Too old`, with an optional
   note.
8. In the second product stage, choose a delivery time and receive the same governed results by
   email or another approved update channel every day.

Advanced provenance, audit, correction, and administration remain available behind the simple
subscription experience, but they must not make the main page complicated.

## Product model

### Topic tags

A tag is a governed analysis product, not an unrestricted user-written prompt. Examples might be:

- `Upcoming maturities`
- `AT1 call or refinancing risk`
- `New funding programmes`
- `Rating changes`
- `Capital raising`
- `M&A and strategic stakes`
- `Leadership changes`
- `Regulatory actions`

Each tag must have a stable ID, user-facing name, short description, required source coverage,
eligible entities, one or more detector/retrieval policies, freshness window, ranking policy,
version, owner, and active/inactive status. Free-form tags can be considered later, after governed
tags have demonstrated reliable results.

### Subscriptions

A subscription links a user to a tag and records whether it is active. Stage 1 needs only this
minimal state:

- `user_id`
- `topic_tag_id`
- `active`
- `created_at`
- `updated_at`

Stage 2 adds channel, delivery time, timezone, digest frequency, no-result preference, and verified
destination. Entitlement and barrier policy must be evaluated when results are viewed and again
when a notification is sent; they must not be copied permanently from the user's original signup.

### Daily analysis runs and results

The expensive analysis should run once for each compatible tag, coverage universe, authorization
scope, and daily window, then be safely fanned out to eligible subscribers. It should not rerun the
same model analysis independently for every subscriber.

Every result must record:

- tag, entity, title, concise opportunity explanation, and why it is fresh;
- first-seen, last-confirmed, effective, and recorded timestamps;
- score and material score components;
- exact citations and source/document versions;
- pattern, prompt, model, policy, and analysis-run versions;
- coverage state: `complete`, `incomplete`, `delayed`, or `failed`;
- lifecycle state: `new`, `updated`, `unchanged`, `resolved`, or `withdrawn`; and
- the authorization scope used to create it.

The page must distinguish these outcomes:

- Fresh opportunities found
- Nothing new for this tag
- Analysis still running
- Coverage incomplete or delayed
- Analysis failed

`Nothing new` must never be shown when required coverage is incomplete.

### Evaluation events

Each displayed result records an exposure event before feedback can be interpreted. Feedback is
append-only and tied to the exact result version. Initial labels are:

- `Useful`
- `Not relevant`
- `Incorrect`
- `Duplicate`
- `Too old`

An optional note supports diagnosis. Feedback must improve evaluation and, after sufficient
governed evidence, ranking; it must not immediately retrain or change production behavior from one
user click.

## Product delivery stages

### Stage 1: Subscription page, same-page analysis, and evaluation

Build the smallest complete product loop:

1. The user signs in and sees the tag catalog.
2. The user selects a tag and saves the subscription.
3. The page requests or retrieves today's governed analysis for that tag.
4. Results appear below the tags on the same page.
5. The user opens evidence and records an evaluation.
6. The subscription is reused by the daily scheduler, so the next day's results are waiting on the
   page without another setup step.

Stage 1 does not include email. Its purpose is to prove that the tags are understandable, results
are fresh and useful, evidence is trustworthy, and the feedback labels capture why results succeed
or fail.

The intended page is intentionally small:

```text
Topics you follow

[x] Upcoming maturities     [ ] Rating changes
[ ] AT1/refinancing risk    [ ] Capital raising
[ ] New programmes          [ ] M&A / strategic stakes

[ Save topics ]                         Last analysis: 08:00 UTC

Today's results: Upcoming maturities

1. Bank / opportunity title                    New
   Why it matters, amount/date, and short analysis
   [View evidence]  [Useful] [Not relevant] [Incorrect] [Duplicate] [Too old]

Coverage complete through 08:00 UTC
```

### Stage 2: Daily email or update delivery

After Stage 1 quality and usability gates pass, add notification preferences and a daily digest.
The digest contains the same result versions shown on the page; it does not run a separate analysis
or generate ungoverned email prose.

Stage 2 adds:

- delivery channel and verified destination;
- user timezone, delivery time, frequency, and pause/resume controls;
- one-click unsubscribe and per-tag unsubscribe;
- grouped results by tag with direct links back to evidence on the page;
- explicit `nothing new` or coverage-warning behavior according to user preference;
- idempotent send keys so retries cannot send duplicate digests;
- entitlement and barrier re-check immediately before rendering/sending;
- delivery, bounce, complaint, suppression, and failure events;
- safe handling for sensitive/private-side material, including link-only notifications when
  required; and
- notification metrics that never treat email opens as proof of opportunity quality.

## Non-negotiable principles

- No production claim may rely on the synthetic corpus.
- Missing source coverage must fail closed and remain visible to analysts.
- A citation is not sufficient unless the cited passage entails the material claim.
- Current-state detectors must not treat superseded or contradicted facts as active.
- Authorization, entitlements, and information barriers must remain enforced below the prompt
  layer.
- Runtime models must be promoted, immutable artifacts from the governed model registry.
- Analyst feedback must be linked to the same signal record produced by the detector.
- Topic tags must resolve to versioned, governed analysis policies rather than arbitrary prompts.
- Saved subscriptions must never bypass current entitlements or information barriers.
- Page, email, and other channels must display the same immutable result version.
- Every go-live quality target must be measured on a locked, representative, independently
  reviewed dataset.

## Status legend

- `[ ]` Not started or not proven.
- `[~]` Partially implemented; integration or real-data evidence is missing.
- `[x]` Implemented and verified in the current working tree.

An item is not complete merely because a unit test exists. Completion requires the exit criteria
for its phase.

## Phase 0: Stabilize and package the POC

Goal: provide a repeatable, explicitly synthetic version of the tag-subscription product loop that
can be shown without implying production readiness.

### Work

- [x] Fix `_LocalPatternRegistry` initialization so `run_poc_demo()` provides the
  `last_coverage_gaps` state expected by `BriefCompiler`.
- [x] Add a test that invokes `run_poc_demo()` from ingestion through materialized tag results and
  the rendered page, and asserts that its fixture quality checks pass.
- [x] Keep the deterministic regression product loop behind the explicitly named
  `fi-intel demo stage-one-fixture` command; it does not make network or LLM calls.
- [x] Add a simple POC page containing a curated tag list, saved in-memory demo subscriptions,
  same-page results, evidence expansion, and the five initial evaluation buttons.
- [x] Make selecting a tag run or retrieve the corresponding synthetic analysis and render the
  results below the tag list without navigating to a separate workbench.
- [~] Persist demo evaluations for the life of the demo process and display aggregate counts so the
  audience can see the quality-learning loop.
- [x] Put a permanent synthetic warning on the explicit fixture mode only. Never reuse fixture
  output behind the live command.
- [ ] Keep ingestion counts, deduplication, entity resolutions, queued ambiguities, accepted and
  rejected claims, detected signals, citations, coverage status, and fixture precision/recall in a
  collapsible technical panel rather than the main user flow.
- [ ] Add one intentional decoy document, one future-recorded document, one ambiguous bank name,
  and one contradictory update to the demo narrative.
- [ ] Make a failed evidence, look-ahead, entitlement, or coverage check fail the demo command with
  a non-zero exit code.
- [ ] Document a five-minute presenter script and expected output.
- [~] Preserve the current improvements for ambiguous bank-name resolution, score separation,
  bounded near-duplicate comparison, held unresolved claims, centralized triage threshold, USD
  scope disclosure, and incomplete-coverage rendering.
- [ ] Commit or intentionally remove all untracked migrations and tests so the demo is
  reproducible from a named revision.
- [ ] Make `ruff format --check .` pass.

### Live Stage 1 correction implemented

- [x] Make `fi-intel demo stage-one` the live command and fail it before startup when the
  OpenAI-compatible LLM endpoint/model or honest source-contact user agent is missing.
- [x] Register a bounded official-public-source matrix with two regulator/market pages in each of
  Saudi Arabia, the UAE, Qatar, Kuwait, Bahrain, and Oman.
- [x] Fetch those pages through the origin-locked, bounded HTTP client and record per-source fetch,
  analysis, timestamp, content hash, accepted-candidate, rejection, and failure state.
- [x] Call the configured LLM on the real fetched text using a strict JSON schema.
- [x] Reject a model candidate unless the topic is allowed, its timestamp is inside the lookback,
  and its entity, date marker, and exact evidence quote all occur in the fetched source text.
- [x] Show the live model name, run ID, twelve-source ledger, content provenance, coverage state,
  and unsupported-candidate count on the same page.
- [x] Re-run the server analysis when the user presses `Refresh analysis`, while caching normal
  topic navigation for the configured interval.
- [x] Define `complete` narrowly as all registered live POC pages fetched and analysed in that run;
  suppress `nothing new` whenever any required page fails.
- [ ] Replace listing-page evidence with archived full-detail documents and stable detail URLs for
  every source.
- [ ] Add issuer IR, local-exchange disclosures, licensed rating actions, and licensed news for the
  desk-approved legal-entity universe before claiming production GCC FIG coverage.

### Exit criteria

- A clean checkout can start the explicit fixture page with one command; the live page additionally
  requires an operator-supplied LLM endpoint and source-contact identity.
- A user can choose a tag, see same-page synthetic results, open evidence, and evaluate a result.
- Refreshing the analysis does not duplicate unchanged results or evaluation events.
- The full POC has a regression test and no external service dependency.
- The presenter can explain every synthetic result and every intentional non-result.
- The demo makes no claim about real-world model or detector accuracy.

## Phase 1: Converge on one production data plane

Goal: remove the split between the prototype Neo4j workflow and the PostgreSQL production ledger.

### Architecture decision

- [ ] Write and approve an architecture decision record defining the authoritative stores for raw
  assets, document versions, entity identities, assertions, signals, topic tags, subscriptions,
  daily analysis runs, result versions, exposures, feedback, notifications, and audit events.
- [ ] Define stable identifiers and idempotency keys across every stage.
- [ ] Decide which graph material is authoritative and which graph is a rebuildable projection.
- [ ] Establish one transaction/outbox boundary for cross-store changes.
- [ ] Mark v1 compatibility code with a removal milestone and prevent new production dependencies
  on it.

### Runtime integration

- [ ] Wire registered raw adapters into `SourceIngestionCoordinator` and
  `ReplayableIngestionService` through a scheduled production worker.
- [ ] Consume `transactional_outbox` events with retry, dead-letter handling, ordering rules, and
  idempotent handlers.
- [ ] Transform admitted `document_version` records into retrieval chunks and extraction jobs.
- [ ] Run entity resolution and extraction from the versioned document record, not the prototype
  `document` table.
- [ ] Persist detector signals through `PostgresIntelligenceLedger` and, if needed, project the
  same signal ID into Neo4j.
- [ ] Materialize one immutable, user-facing result version from the authoritative signal, analysis,
  and evidence records.
- [ ] Ensure the API, subscription page, notification flow, and precision-feedback provider consume
  those same result and signal records.
- [ ] Add a daily analysis worker that owns detection, research, validation, result materialization,
  and status transitions for each tag/scope/window job.
- [ ] Coalesce compatible subscriber demand so analysis executes once and delivery fans out only
  after authorization filtering.
- [ ] Remove or hard-disable production use of the prototype `ingest`, `extract`, `research`, and
  `brief` paths after parity is established.

### End-to-end contract tests

- [ ] Test raw acquisition through archived bytes, document admission, entity resolution,
  extraction, graph/ledger projection, signal detection, tag result materialization, subscription
  retrieval, page display, exposure, and feedback.
- [ ] Test retries at every boundary without duplicate documents, assertions, signals, daily result
  sets, exposures, evaluations, or notifications.
- [ ] Test replay from archived raw bytes with network access disabled.
- [ ] Test public/private barrier separation through the entire vertical slice.
- [ ] Test an outbox crash between database commit and result release.

### Exit criteria

- There is exactly one supported production execution path.
- A single correlation/run ID traces a source asset through the analyst-visible result.
- Feedback written through the API changes the governed precision estimate for the originating
  detector lineage.
- The same result ID can later be delivered by email without rerunning or rewriting the analysis.
- Replaying the same inputs is idempotent and reproducible.

## Phase 2: Make temporal truth and evidence reliable

Goal: prevent stale facts and merely decorative citations from creating analyst-facing claims.

### Current-state semantics

- [ ] Add `valid_to` or an explicit state interval to the extraction contract.
- [ ] Define state keys for mutable predicates such as rating outlook, marketed status, programme
  approval, capital ratios, leadership roles, instrument status, and transaction status.
- [ ] When a later assertion changes a state key, close the former fact at a deterministic
  effective time while retaining full bitemporal history.
- [ ] Distinguish correction of a previously recorded fact from a genuine change in the world.
- [ ] Represent negation, rescission, expiry, cancellation, completion, and unknown state
  explicitly.
- [ ] Make every detector query select the latest active state as of both valid time and recorded
  time.
- [ ] Add adversarial tests for negative-to-stable ratings, marketed-to-completed programmes,
  leadership changes, amended maturity dates, and documents received late.
- [ ] Correct CLI timestamp handling so offset-aware values are converted to UTC rather than
  relabelled as UTC.
- [ ] Require timezone-aware API timestamps.

### Claim grounding

- [ ] Require material object names, numeric values, units, currencies, dates, and statuses to be
  present in or deterministically derived from the cited passage.
- [ ] Add an entailment validator for every atomic research claim, with an abstain/hold outcome
  when support is unclear.
- [ ] Store evidence-to-field mappings so analysts can see which text supports which structured
  value.
- [ ] Reject claims whose citations merely mention the subject without supporting the predicate.
- [ ] Verify source URL, archived content hash, span offsets, and document version before display or
  delivery.
- [ ] Remove model self-confidence percentages from analyst output until calibration demonstrates
  that they are reliable; otherwise label them as uncalibrated model scores.
- [ ] Add a human correction path that creates a new version rather than mutating provenance.

### Server-side result-release and publication gate

- [ ] Stop accepting caller-attested `coverage_complete` as authoritative.
- [ ] Expose only immutable result versions associated with a completed governed analysis run.
- [ ] Recalculate coverage, citations, entitlements, model policy, and temporal pins server-side in
  the result-release transaction or immediately before it.
- [ ] Store a result manifest containing all source versions, assertion IDs, signal IDs,
  prompt/model artifacts, policies, validation results, authorization scope, and output hash.
- [ ] Define which tags can be released automatically after gates and which require a named
  analyst/publisher approval; preserve the approval history.
- [ ] Sanitize generated HTML and keep the existing restrictive browser policy.

### Exit criteria

- No detector fires from a fact that was no longer active at the requested as-of time.
- Every displayed or delivered material claim has a field-level, versioned, entailing citation.
- An authorized API caller cannot mark arbitrary HTML or an incomplete result set as complete
  governed output.
- Corrections and state changes remain reproducible in historical backtests.

## Phase 3: Build the real GCC FIG coverage universe

Goal: make silence meaningful by covering the institutions and sources analysts actually monitor.

### Coverage definition and licensing

- [ ] Obtain a desk-approved legal-entity universe with LEIs, parents, subsidiaries, aliases,
  jurisdictions, sectors, languages, and coverage tiers.
- [ ] Register required GCC central banks, local exchanges, issuer/investor-relations sites,
  ministries, rating agencies, prospectus/regulatory repositories, and licensed news sources.
- [ ] Record licence class, permitted audiences, retention rules, redistribution restrictions,
  source owner, and renewal date for every source.
- [ ] Define required sources by entity, country, signal pattern, and freshness window rather than
  one global list.
- [ ] Add source health, observed completeness, silence SLA, expected cadence, and operational
  ownership.
- [ ] Keep SEC and Federal Reserve feeds out of GCC coverage calculations unless a separately
  approved use case requires them.

### Source acquisition

- [ ] Implement full-content adapters for the approved source set; do not treat feed summaries as
  source evidence.
- [ ] Scope GLEIF synchronization to the coverage universe or use a resumable bulk process; do not
  fail the whole sync when unfiltered pagination exceeds the configured page cap.
- [ ] Add checkpointing, conditional requests, backfill, tombstone handling, schema-drift alerts,
  and replay tests.
- [ ] Archive exact raw bytes and acquisition metadata before parsing.
- [ ] Measure source-to-admission latency and notify operators of stale or partial coverage.

### Entity resolution

- [~] Retain IDF-weighted name matching, jurisdiction/sector blocking, and minimum winner margin.
- [ ] Create a representative labelled benchmark for English, Arabic, transliteration variants,
  abbreviations, government entities, bank groups, subsidiaries, and common confusing names.
- [ ] Require exact identifiers for sensitive automatic merges where available.
- [ ] Add alias provenance, effective dates, parent-child relationships, mergers, renames, and LEI
  lifecycle changes.
- [ ] Ensure missing document jurisdiction/sector metadata cannot silently broaden automatic
  matching across the full reference universe.
- [ ] Add review-queue SLAs, assignment, reason codes, evidence, dual control for high-risk merges,
  and reversible decisions.
- [ ] Measure auto-match precision, auto-match coverage, queue rate, queue age, and downstream claim
  loss.

### Deduplication and document quality

- [~] Keep exact-hash identity and the bounded recent near-duplicate window.
- [ ] Replace pairwise near-duplicate scans with blocking, MinHash/LSH, or another measured index
  before production volume.
- [ ] Bound or partition historical exact hashes rather than loading an indefinitely growing set
  into each worker.
- [ ] Deduplicate syndicated content across authorized sources while preserving every source's
  provenance and entitlement constraints.
- [ ] Measure false merges and missed duplicates on multilingual and templated filings.

### Exit criteria

- The desk signs the entity universe and source-to-entity coverage matrix.
- Required sources meet their freshness and completeness SLAs for a sustained qualification
  window.
- Ambiguous identities are held for review rather than falsely merged.
- Coverage gaps appear beside the affected tag results and in any later digest.

## Phase 4: Govern retrieval, extraction, and reasoning models

Goal: use versioned, evaluated model artifacts that perform on the actual source languages and
document types.

### Retrieval

- [ ] Replace the default `HashingEmbedder` in production with an approved multilingual semantic
  embedding model.
- [ ] Evaluate Arabic, English, bilingual, transliterated, table-heavy, and long-form regulatory
  documents.
- [ ] Tune chunk boundaries using headings, tables, page references, and source-specific structure
  while keeping exact character/page provenance.
- [ ] Evaluate lexical, vector, hybrid, and reranked retrieval separately.
- [ ] Add hard negatives from same-named banks, old documents, unrelated capital events, and
  repeated boilerplate.
- [ ] Version embeddings and support resumable, zero-downtime re-embedding.

### Model registry and serving

- [ ] Make extraction and research builders resolve an active/canary artifact from the governed
  model registry rather than reading an unconstrained model name directly from settings.
- [ ] Record artifact digest, weights/container digest, tokenizer, quantization, prompt version,
  schema version, endpoint deployment, and inference parameters on every model call.
- [ ] Enforce promotion gates at runtime and fail closed when no approved artifact exists.
- [ ] Add canary routing, rollback, capacity limits, timeout policy, and model-health monitoring.
- [ ] Prevent a mutable endpoint from changing behavior while retaining the same recorded model
  identity.

### Evaluation

- [ ] Build independently reviewed train/development/holdout splits separated by entity, source,
  time, and document family.
- [ ] Measure field-level extraction precision/recall, citation accuracy, entity linking, temporal
  interval accuracy, abstention, and hallucination severity.
- [ ] Calibrate confidence only after a reliability curve exists; otherwise keep confidence gates
  disabled and confidence out of analyst-facing probability language.
- [ ] Run adversarial prompt-injection, corrupted document, OCR, encoding, malformed table, and
  source-spoofing tests.

### Exit criteria

- Production calls can be traced to an immutable, promoted artifact.
- Retrieval and model quality gates pass on a locked real-data holdout.
- Model rollback can be completed without losing replayability.
- No analyst-facing percentage is presented as calibrated unless calibration has been approved.

## Phase 5: Validate and expand signal quality

Goal: demonstrate that signals save analyst time and surface real opportunities without excessive
noise.

### Detector and scoring work

- [~] Keep the improved scoring separation and visible threshold distribution.
- [ ] Replace synthetic calibration scenarios with labelled historical GCC FIG episodes and hard
  negatives.
- [ ] Add instrument- and desk-specific materiality instead of treating missing account tier or
  deal size as neutral evidence of importance.
- [ ] Support relevant currencies with explicit FX source, FX timestamp, original amount, converted
  amount, and threshold policy. Do not silently infer USD.
- [ ] Expand beyond the five hardcoded DCM-oriented patterns according to analyst priorities.
- [ ] Define lifecycle closure and re-opening for every pattern.
- [ ] Centralize detector, tag-result, API, and page threshold policy in one versioned policy
  artifact.
- [ ] Run threshold sensitivity and workload-capacity analysis before choosing desk defaults.

### Feedback loop

- [~] Keep Beta-shrunk early feedback and compatibility-lineage rules.
- [ ] Ensure feedback operates on the exact production signal IDs created by detectors.
- [ ] Separate usefulness, correctness, timeliness, materiality, duplication, and disposition
  labels instead of collapsing them into one verdict.
- [ ] Prevent feedback leakage across desks, entitlement groups, barriers, incompatible pattern
  versions, and future as-of times.
- [ ] Monitor selection bias: rejected or unseen candidates need sampled labelling as well as
  analyst feedback on surfaced signals.
- [ ] Require minimum sample size and uncertainty intervals before feedback can materially alter
  ranking.

### Exit criteria

- Every production pattern has an owner, hypothesis, required sources, state semantics, labelled
  evaluation, threshold, capacity analysis, lifecycle policy, and kill switch.
- Measured false-positive and false-negative rates meet desk-approved targets on the locked
  holdout.
- The feedback loop is observable, versioned, and cannot cross authorization boundaries.

## Phase 6: Deliver Product Stage 1 - tag subscriptions and same-page results

Goal: validate the complete user loop on the web before adding email delivery.

### Topic catalog and subscriptions

- [ ] Add governed `topic_tag` records with stable IDs, display order, descriptions, ownership,
  analysis-policy version, required coverage, and active status.
- [ ] Add a unique, auditable `user_topic_subscription` record for each user/tag pair.
- [ ] Provide authenticated APIs to list authorized tags, list subscriptions, subscribe, and
  unsubscribe.
- [ ] Keep the first catalog deliberately small; launch with three to five topics whose source and
  quality requirements can actually be met.
- [ ] Do not expose free-form prompts or complex rule builders in Stage 1.
- [ ] Explain each tag in one sentence and show when its analysis last completed successfully.

### Daily analysis orchestration

- [ ] Schedule one daily job per compatible tag, authorization scope, universe, and analysis
  window; use a unique idempotency key for that combination.
- [ ] Trigger the same governed job on demand when a subscribed tag has no current result, while
  coalescing concurrent requests.
- [ ] Track `queued`, `running`, `complete`, `partial`, and `failed` analysis states.
- [ ] Materialize `new` and `materially updated` result versions; do not resurface unchanged items
  as fresh opportunities every day.
- [ ] Store a per-tag daily run summary with counts, coverage state, latest source timestamp,
  failure reasons, and model usage.
- [ ] Make empty results explicit and only valid when required coverage is complete.
- [ ] Add retry, timeout, dead-letter, stale-job recovery, and a per-tag kill switch.

### Simple page

- [ ] Make the default authenticated page a tag list followed by the selected tag's results.
- [ ] Save a subscription with one click and update the page without navigating away.
- [ ] Show loading, current, empty, incomplete, delayed, and failed states in plain language.
- [ ] Display result title, entity, short opportunity analysis, freshness reason, first-seen time,
  and `New`/`Updated` state in the collapsed card.
- [ ] Expand a result in place to show score explanation, exact evidence passages, original source
  links, effective/recorded times, contradictory evidence, unknowns, and coverage warnings.
- [ ] Provide exactly the initial evaluation actions: `Useful`, `Not relevant`, `Incorrect`,
  `Duplicate`, and `Too old`, plus an optional note.
- [ ] Allow a user to change their own evaluation by appending a superseding event; never overwrite
  the audit history.
- [ ] Preserve accessible keyboard navigation, mobile readability, safe rendering, and clear
  information-barrier boundaries.
- [ ] Keep advanced administration and technical diagnostics out of the normal user page.

### Evaluation and product learning

- [ ] Record which result version and position the user was shown before accepting feedback.
- [ ] Report usefulness, error, irrelevance, duplication, staleness, evidence-open rate, and no-result
  days by tag and cohort.
- [ ] Sample non-surfaced candidates for expert labelling so evaluation is not biased only toward
  displayed results.
- [ ] Review incorrect results with traceability to the source, extraction, entity decision,
  detector, and reasoning output.
- [ ] Provide a tag-level quality dashboard for product/model owners, not on the simple user page.
- [ ] Run moderated usability sessions to verify that users understand tags, freshness, evidence,
  feedback, and coverage warnings.

### Stage 1 exit criteria

- A user can sign in, select a tag, save it, see today's same-page results, open evidence, and
  evaluate a result without command-line or database access.
- The following day's scheduled run updates the same page and does not duplicate unchanged results.
- Every result shown is authorized, versioned, evidence-gated, and traceable to one governed run.
- `Nothing new` is never displayed for an incomplete or failed coverage window.
- User testing confirms that the simple page and tags are understandable.
- Pilot tag quality meets its agreed precision, freshness, and usefulness thresholds.

## Phase 7: Deliver Product Stage 2 - daily email or update delivery

Goal: deliver the already-governed Stage 1 results automatically according to each user's saved
subscriptions and preferences.

### Preferences and scheduling

- [ ] Extend subscriptions with enabled channel, verified destination, timezone, local delivery
  time, frequency, pause-until date, and no-result preference.
- [ ] Support one daily digest across selected tags by default; avoid one email per result.
- [ ] Calculate due recipients safely across daylight-saving and timezone changes.
- [ ] Add global and per-tag unsubscribe, pause, and resume controls on both the page and email.
- [ ] Respect organization suppression, offboarding, disabled accounts, and compliance holds.

### Notification assembly

- [ ] Build each digest only from immutable Stage 1 result versions.
- [ ] Re-check the recipient's active account, subscriptions, entitlement group, barrier side, and
  tag access immediately before rendering and sending.
- [ ] Group results by tag, clearly mark `New` and `Updated`, and link each item to its evidence page.
- [ ] Use an approved deterministic template; do not ask a model to regenerate claims for email.
- [ ] Show coverage warnings prominently and never convert incomplete coverage into `nothing new`.
- [ ] For sensitive/private-side results, use a link-only notification when content cannot safely
  leave the application boundary.
- [ ] Include why the recipient received the email and direct preference/unsubscribe links.

### Delivery reliability and compliance

- [ ] Use a transactional email provider approved by security and compliance.
- [ ] Give every recipient/date/digest-version combination an idempotency key so retries cannot
  duplicate messages.
- [ ] Record queued, rendered, provider-accepted, delivered, bounced, complained, suppressed, and
  failed events without storing unnecessary message content.
- [ ] Retry transient failures with backoff and dead-letter permanent failures for operator review.
- [ ] Configure domain authentication, bounce/complaint handling, suppression lists, rate limits,
  and provider webhooks.
- [ ] Add test-recipient, preview, and global notification kill-switch controls.
- [ ] Define retention, audit, and deletion rules for destinations and delivery events.

### Stage 2 exit criteria

- Every sent opportunity matches an immutable result version already available on the page.
- Unauthorized, unsubscribed, paused, offboarded, or suppressed recipients receive nothing.
- Retries and scheduler restarts do not create duplicate digests.
- Delivery failures and complaints are observable and actionable.
- Users can change preferences or unsubscribe without support intervention.
- Email does not become a second, less-governed analysis path.

## Phase 8: Production engineering and operations

Goal: make the system observable, recoverable, secure, and supportable.

### CI and release engineering

- [ ] Add checked-in CI for Python 3.11+, dependency checks, unit tests, strict mypy, Ruff lint,
  Ruff formatting, migration drift, secret scanning, dependency scanning, and build provenance.
- [ ] Run PostgreSQL/pgvector and Neo4j integration suites in CI with
  `FI_INTEL_REQUIRE_INFRA=true`; skipped infrastructure tests must fail release jobs.
- [ ] Add a true end-to-end ephemeral-environment test.
- [ ] Resolve the Docker bootstrap/migration conflict: initialize an empty database through the
  migration runner, or record the bootstrap migration checksum atomically.
- [ ] Test upgrade, downgrade/forward-fix, backup restore, and migration failure recovery.
- [ ] Produce immutable releases and a software bill of materials.

### Reliability and observability

- [ ] Define SLOs for acquisition freshness, queue latency, daily analysis completion, page-result
  readiness, notification delivery, application availability, and recovery.
- [ ] Monitor source health, outbox lag, resolution queue age, extraction rejection causes,
  detector volumes, score drift, evidence failures, coverage gaps, model usage, result-release
  failures, subscription-job lag, and notification failures.
- [ ] Add alerts, dashboards, runbooks, escalation owners, and post-incident review.
- [ ] Add load, soak, backpressure, connection-pool, retry-storm, and large-document tests.
- [ ] Establish backup, point-in-time recovery, graph rebuild, raw-archive recovery, and disaster
  recovery exercises.
- [ ] Define retention and erasure policies consistent with source licences and audit obligations.

### Security and governance

- [~] Retain current entitlement, barrier, audit, SSRF, redirect, size-limit, XML, OIDC/JWKS, CSP,
  and strict-schema controls.
- [ ] Complete threat modelling for ingestion, models, plugins/tools, result release, subscriptions,
  notification delivery, insider risk, supply chain, and administrative operations.
- [ ] Use a deployment secret manager, short-lived credentials, key rotation, least privilege,
  network segmentation, and environment separation.
- [ ] Add penetration testing and a remediation SLA before production.
- [ ] Test policy and entitlement changes against historical and cached material.

### Exit criteria

- Recovery objectives have been tested, not merely documented.
- No critical or high-severity unresolved security finding remains.
- Release gates cannot pass with skipped infrastructure tests.
- Operations has dashboards, alerts, runbooks, and named ownership.

## Phase 9: Real-data validation and controlled launch

Goal: prove reliability with analysts before automated output affects decisions.

### Qualification sequence

1. Retrospective replay against a locked historical corpus.
2. Prospective dark run with no analyst exposure.
3. Analyst shadow mode alongside the existing manual process.
4. Limited pilot for named analysts and entities with mandatory human approval.
5. Desk rollout with kill switches and ongoing sampling.

### Proposed go-live gates

Targets below are starting proposals and require desk, data-governance, compliance, model-risk, and
operations approval.

| Dimension | Proposed gate |
|---|---|
| Source operations | At least 99% of required polls complete within source SLA during qualification |
| Coverage honesty | Zero pages or digests falsely reporting `nothing new`/complete when required coverage is incomplete |
| Entity resolution | At least 99.5% precision for automatic organization matches; ambiguous cases held |
| Material field extraction | At least 95% precision and 85% recall on the locked holdout |
| Citation entailment | At least 99% of displayed or delivered material claims fully supported by cited passages |
| Tag-result precision | At least 85% analyst-confirmed correctness/usefulness at the chosen daily workload |
| Temporal integrity | Zero known look-ahead or superseded-state violations in holdout and replay tests |
| Authorization | Zero cross-entitlement or cross-barrier disclosure in automated and penetration tests |
| Reproducibility | 100% of sampled outputs replay to the same governed inputs and compatible result |
| Stage 1 usability | At least 90% of pilot users can subscribe, find evidence, and evaluate without assistance |
| Notification integrity | Zero duplicate digests and zero sends after unsubscribe in qualification tests |
| Availability | At least 99.5% page availability during the pilot, excluding approved maintenance |
| Shadow evidence | One full quarter completed with signed analyst and model-risk review |

Recall targets should be set per pattern because some opportunity types tolerate misses differently
from others. Precision must be reported with sample counts and confidence intervals, not as a point
estimate alone.

### Launch decision

- [ ] Desk owner signs utility and workload results.
- [ ] Data governance signs source rights, retention, and provenance.
- [ ] Compliance signs result release, notification, barriers, and audit controls.
- [ ] Model risk signs datasets, metrics, calibration, limitations, and monitoring.
- [ ] Security signs the production threat model and penetration-test remediation.
- [ ] Operations signs SLOs, runbooks, recovery tests, and on-call ownership.
- [ ] Product owner accepts residual risks and rollback criteria.

## Cross-cutting risk register

| Risk | Current consequence | Primary roadmap control |
|---|---|---|
| Split Neo4j/PostgreSQL signal planes | Feedback and API do not operate on detector output | Phase 1 unified identifiers and ledger |
| Stale open-ended assertions | False opportunities from obsolete facts | Phase 2 state intervals and supersession |
| Citation without entailment | Grounded-looking hallucinations | Phase 2 field grounding and entailment gate |
| Caller-attested output completeness | Governed analysis can be bypassed by arbitrary content | Phase 2 server-side result manifest |
| Missing GCC sources | Safe but empty or severely incomplete output | Phase 3 source/entity coverage matrix |
| Weak multilingual retrieval | Missed Arabic, transliteration, and semantic matches | Phase 4 approved multilingual retrieval |
| Mutable/unregistered runtime models | Unreproducible quality and governance | Phase 4 runtime model registry enforcement |
| Synthetic-only evaluation | Unknown production precision and recall | Phases 4, 5, and 9 real holdout/shadow run |
| USD-only detector scope | Missed relevant local/non-USD opportunities | Phase 5 governed currency support |
| Ungoverned or vague tags | Users subscribe to topics whose output cannot be measured | Phase 6 governed topic catalog |
| Per-user duplicate analysis | High cost and inconsistent results for the same topic | Phases 1 and 6 shared daily runs |
| Missing exposure logging | Feedback rates are biased or uninterpretable | Phase 6 exposure and evaluation events |
| Stale authorization at send time | Email leaks content after access changes | Phase 7 send-time authorization check |
| Duplicate or unwanted email | Loss of trust, complaints, and compliance risk | Phase 7 idempotency and unsubscribe controls |
| Email prose differs from page | Two conflicting versions of the opportunity | Phase 7 immutable result rendering |
| Skipped database/graph tests | Passing unit suite hides integration failures | Phase 8 required infrastructure CI |
| Docker/migration bootstrap conflict | Fresh deployment can fail or drift | Phase 8 migration-only bootstrap |
| Pairwise/source-local deduplication | Scaling limits and syndicated duplicates | Phase 3 indexed cross-source dedupe |

## Recommended delivery order

The critical path is:

`tag-subscription POC -> one data plane -> temporal/evidence correctness -> real coverage -> governed
models -> real tag-result labels -> Stage 1 same-page pilot -> Stage 2 email delivery -> shadow
qualification -> controlled launch`

Source licensing, coverage-universe definition, and label collection should begin during Phase 0
because they are external lead-time items. They can proceed in parallel with the engineering work,
but production qualification cannot finish without them.

The Stage 1 page, tag catalog, subscription APIs, and feedback event model can be developed in
parallel with Phases 1-5 using synthetic results. They must not be called production-ready until the
underlying data, evidence, and quality gates pass. Stage 2 email work begins only after users have
validated Stage 1 tags and results on the page; otherwise email merely distributes unproven output
more widely.

## POC demo runbook

### Live Stage 1 demo

Configure a real OpenAI-compatible endpoint and a source user agent that identifies the operating
organization and a real contact, then start the default command:

```powershell
$env:FI_INTEL_LLM_BASE_URL="http://127.0.0.1:8001/v1"
$env:FI_INTEL_LLM_API_KEY="your-endpoint-key"
$env:FI_INTEL_RESEARCH_MODEL="your-served-model"
$env:FI_INTEL_RSS_USER_AGENT="YourOrg FI Opportunity Watch contact@YOUR_REAL_DOMAIN"
.\.venv\Scripts\fi-intel.exe demo stage-one
```

Open `http://127.0.0.1:8765/`. The command refuses to start if the model endpoint or source-contact
identity is missing. It never falls back to the fixture.

Use this five-minute flow:

1. Select a tag. Explain that the first result request fetches twelve registered official public
   pages, two in each of the six GCC countries, and calls the configured model on every page that
   fetched successfully.
2. Keep the source ledger visible. Show the model name, run ID, `completed/required` count, and any
   source-level fetch or analysis failure. A failure changes coverage to `incomplete`.
3. Open a result. Show its exact copied quote, official source link, publication time, fetch time,
   and source-content SHA-256 prefix.
4. Explain that entity, date marker, and evidence quote are deterministically checked against the
   fetched text. Point to the run's rejected-candidate count rather than implying the LLM is trusted.
5. Record `Useful`, `Not relevant`, `Incorrect`, `Duplicate`, or `Too old` feedback.
6. Press `Refresh analysis` to trigger a new server run; ordinary navigation reuses the short-lived
   cached run instead of multiplying model calls by subscription.
7. If coverage is incomplete, show that the page says `no absence claim can be made`, never
   `nothing new`.
8. Close with the exact boundary: this is real live public-source/model execution across all GCC
   countries, but it is not yet the production issuer-IR, licensed-rating, or licensed-news universe.

The current machine still needs the operator-supplied LLM endpoint/key/model and a real contact user
agent before this live flow can execute. Those values are intentionally not committed to source.

### What the explicit fixture proves

The POC is intended to prove that the product loop is understandable:

`choose a governed tag -> synthetic documents -> deduplication -> entity resolution -> typed
assertions -> deterministic signals -> evidence-backed tag results on the same page -> user
evaluation -> fixture checks`

It proves software contracts, provenance, temporal pinning, entitlement boundaries, abstention, and
the proposed analyst experience. It does not prove live source coverage, real model quality,
production precision/recall, operational scale, or commercial value.

### Explicit offline fixture

From PowerShell in the repository root:

```powershell
.\.venv\Scripts\fi-intel.exe demo stage-one-fixture
```

Then open `http://127.0.0.1:8765/`. The demo uses a local fixed fixture credential, runs without
external services, and keeps subscriptions and evaluations only in memory until the process stops.

Use this flow:

1. Select `Upcoming maturities`; the first request runs the packaged synthetic ingestion,
   resolution, extraction, detection, retrieval, and research pipeline.
2. Show the same-page opportunity, freshness explanation, score, and analysis timestamp.
3. Expand `View evidence and what would disprove this` and inspect the exact fixture passage.
4. Press `Useful`, add an optional note, and show that the evaluation remains selected.
5. Select `Treasury leadership changes` to demonstrate a complete `Nothing new` outcome when the
   available signal falls below the visible triage threshold.
6. Return to another followed tag with `View results`; cached analysis is reused rather than rerun
   independently for each subscription.
7. End with the fixture notice and state that real accuracy remains a gated future measurement.

### Optional terminal quality report

The underlying service-free analysis can also be inspected directly:

```powershell
.\.venv\Scripts\python.exe -c "import asyncio; from fi_intel.demo.runner import format_poc_report, run_poc_demo; artifacts = asyncio.run(run_poc_demo()); print(format_poc_report(artifacts.report))"
```

The terminal report should finish with `FI intelligence POC - PASS`, zero decoy signals, zero
look-ahead violations, zero citation failures, and explicitly fixture-only precision/recall values.

### Fixture presenter flow

1. **Problem:** users need fresh opportunities on a few topics they care about, without searching
   many sources every day.
2. **Subscribe:** choose one governed tag from the simple list and save it.
3. **Result:** show the same-page daily opportunities and explain `New`, `Updated`, and
   `Nothing new` behavior.
4. **Evidence:** expand one result, open the exact source passage, and show freshness and coverage.
5. **Evaluate:** mark the result `Useful` or another reason and show that feedback is attached to
   the exact result version.
6. **Controls:** open the technical panel to show the duplicate, ambiguous name, decoy, temporal
   pin, entitlement, and fixture checks.
7. **Next stage:** show how saved subscriptions later feed a daily digest without changing the
   underlying analysis.
8. **Close:** ask users which three to five tags matter first and what would make each result useful.

### Claims to avoid during the POC

Do not say:

- The tool is production ready.
- Synthetic precision predicts live precision.
- The twelve-page live POC matrix is the complete production GCC FIG source/entity universe.
- A displayed confidence value is a calibrated probability.
- Every cited model claim is already entailment-verified.
- User feedback already changes the same production signals shown on the page.
- Email delivery is already implemented or approved.
- The live demo has exercised PostgreSQL, Neo4j, licensed sources, or production scale.

## Definition of done

### Stage 1 done

The subscription page and same-page daily results are reliable enough for a controlled analyst
pilot only when:

- all Phase 1 and Phase 2 exit criteria are complete;
- the desk-approved source and entity universe is operational;
- real-data model and detector gates pass on locked holdouts;
- the governed tag catalog, subscription APIs, daily scheduler, result versioning, page states,
  evidence view, exposure logging, and evaluation events pass the Phase 6 exit criteria;
- analyst shadow mode has run for a full quarter;
- page display cannot bypass coverage, evidence, authorization, and model-policy gates;
- infrastructure tests, security gates, backups, recovery, monitoring, and runbooks are live; and
- the named governance owners approve launch and residual risks.

Stage 1 may launch without email. Users return to the page for their daily subscribed results while
the team measures tag usefulness, correctness, freshness, and user behavior.

### Stage 2 done

Email or another update channel may launch only when Stage 1 remains within its quality gates and:

- every message is built from the same immutable result versions already available on the page;
- notification cannot bypass current subscription, coverage, evidence, authorization, barrier, or
  model-policy gates;
- timezone scheduling, idempotency, unsubscribe, suppression, bounce/complaint handling, retries,
  provider webhooks, audit, and the global kill switch pass Phase 7 exit criteria;
- delivery is shadowed to test recipients before real subscribers are enabled; and
- security, compliance, operations, and the product owner approve the channel-specific risks.
