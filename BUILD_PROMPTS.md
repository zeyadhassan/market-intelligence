# Build prompts

Ten milestone prompts. Each is a separate agent session. Each references
`CLAUDE.md`, which must be in context for all of them.

**Do not paste more than one milestone at a time.** The single most common cause
of bad agentic output on a project this size is a prompt that asks for four
things at once — the agent front-loads the easy parts, produces something that
imports cleanly, and quietly skips the hard middle. One milestone, verified,
then the next.

Before each session: `git checkout -b milestone-N-<name>`.
After each session: run the verification command yourself. Do not take the
agent's word that tests pass.

---

## How to run a milestone

Paste this wrapper, then the milestone body:

> Read `CLAUDE.md` in full before starting. It contains architecture decisions,
> invariants, and anti-patterns that constrain this task.
>
> Work in this order: (1) restate the objective and list the acceptance criteria
> as a checklist, (2) flag anything ambiguous or anything that would require
> weakening an invariant — stop and ask, do not proceed on a guess, (3) write
> the tests first, including the negative and leakage cases, (4) implement until
> they pass, (5) run the verification command and paste the real output, (6)
> report what you deliberately did not do.
>
> Do not implement future milestones. Scope creep here means the next milestone
> starts from code I have not reviewed.
>
> ---
>
> [MILESTONE BODY]

---

## Milestone 2 — Ingestion pipeline

**Objective.** Turn the adapter stream into persisted, deduplicated,
canonicalized documents in Postgres, with resumable cursors.

**Read first.** `fi_intel/sources/`, `deploy/init.sql`, `tests/test_adapter_contract.py`.

**Build.**
- `fi_intel/ingest/pipeline.py` — orchestrates fetch → dedupe → persist per source.
- `fi_intel/ingest/dedupe.py` — content-hash exact dedupe plus near-duplicate
  detection for the same story carried by multiple wires.
- `fi_intel/ingest/store.py` — async Postgres writes, idempotent upsert,
  cursor persistence in `ingest_cursor`.
- CLI: `fi-intel ingest run --source <id>` and `fi-intel ingest status`.

**Acceptance criteria.**
- Ingesting the synthetic corpus twice produces 10 documents, not 20.
- Killing the process mid-run and restarting resumes without gap or duplicate.
  Test this by injecting a failure after N documents, not by mocking it away.
- A malformed document fails the run loudly with the offending `doc_id` in the
  error, and does not partially commit.
- Near-duplicate detection collapses two wire copies of the same story while
  keeping two genuinely different stories about the same event separate. Add
  both cases to the synthetic corpus.
- `fi-intel ingest status` shows per-source document counts and cursor position.

**Non-goals.** No entity resolution, no extraction, no embeddings.

**Verify.** `pytest tests/test_ingest.py -v && fi-intel ingest run --source synthetic_wire && fi-intel ingest status`

---

## Milestone 3 — GLEIF entity resolution

**Objective.** Resolve organization mentions to stable LEIs. This is the
highest-risk milestone in the project; a bad merge is invisible and corrupts
everything downstream.

**Read first.** `CLAUDE.md` invariant 6 and the "do not batch-merge" anti-pattern.

**Build.**
- A GLEIF adapter (registered like any other source) that loads the golden-copy
  file into a local reference table, including parent/child relationships.
- `fi_intel/ingest/resolve.py` — cascade: exact identifier match (LEI, BIC,
  ISIN) → normalized-name exact → blocked fuzzy match on name plus jurisdiction
  plus sector → queue.
- A `resolution_queue` table for borderline and unmatched candidates.
- Every resolution records `resolver`, `score`, and `resolved_at`.
- CLI: `fi-intel entities resolve`, `fi-intel entities queue`.

**Acceptance criteria.**
- All seven Gulf Meridian documents resolve to one stable entity key. Add name
  variants to the synthetic corpus first — "Gulf Meridian Bank", "Gulf Meridian
  Bank Q.P.S.C.", "Gulf Meridian" — and assert all three collapse.
- Two genuinely different institutions with similar names do **not** merge. Add
  this case to the corpus. This test matters more than the previous one.
- Precision on the labelled set is above 0.98. Report the actual number. If
  recall is low, leave it low — queue depth is a visible problem, a false merge
  is not.
- No merge occurs without a recorded resolver and score.

**Non-goals.** No person resolution. No instrument resolution beyond ISIN
passthrough.

**Verify.** `pytest tests/test_resolve.py -v && fi-intel entities resolve && fi-intel entities queue`

---

## Milestone 4 — Hybrid retrieval behind the entitlement filter

**Objective.** The `corpus_search` capability: BM25 plus vector, entity-filtered,
recency-weighted, with entitlement enforced in SQL.

**Build.**
- `fi_intel/retrieval/corpus.py` — hybrid search with reciprocal rank fusion.
  Chunking, embedding, and HNSW index population.
- `fi_intel/retrieval/entitlement.py` — resolves caller identity to an allowed
  source set; every query joins against it.
- `fi_intel/governance/audit.py` — writes to `access_log` on every retrieval.
- CLI: `fi-intel search "<query>" --as-of <date> --group <entitlement_group>`.

**Acceptance criteria.**
- A caller in a group without access to a source cannot retrieve its documents
  through any parameter combination. Write this as an adversarial test that
  tries at least five bypass routes, including entity-filtered search and
  as-of queries.
- Every retrieval writes an `access_log` row with `run_id`, `principal`,
  `source_id`, `doc_id`.
- `--as-of` filtering is applied in SQL, not in Python after fetching.
- Hybrid beats BM25-alone and vector-alone on a small labelled relevance set.
  Report all three numbers.

**Non-goals.** No agent integration. No reranking model.

**Verify.** `pytest tests/test_retrieval.py tests/test_entitlement.py -v`

---

## Milestone 5 — Bi-temporal graph writer

**Objective.** Write assertions to Neo4j with full provenance and both time
axes, append-only, with an as-of read API.

**Build.**
- `fi_intel/ontology/schema.py` — Pydantic models for every node and edge type.
- `fi_intel/ontology/vocab.py` — the closed T-Box enums.
- `fi_intel/graph/writer.py` — append-only assertion writer. Corrections
  supersede via `superseded_at`; nothing is ever mutated or deleted.
- `fi_intel/graph/client.py` — read sessions that accept an `as_of` and pin
  `recorded_at <= as_of` at the query level.
- Constraints and indexes as a versioned migration.

**Acceptance criteria.**
- Writing the same assertion twice is idempotent.
- A correction creates a new assertion and marks the old one superseded. Both
  remain queryable. Assert the old one is still retrievable at its original
  as-of date.
- An as-of read at time T returns exactly the assertions recorded on or before
  T. Write the leakage test explicitly: assert that an assertion recorded at
  T+1 is invisible at T.
- Any write missing `source_doc_id`, `valid_from`, or `recorded_at` raises.
  Test each missing field separately.

**Non-goals.** No extraction. Populate via a hand-written fixture.

**Verify.** `pytest tests/test_graph_writer.py tests/test_temporal.py -v`

---

## Milestone 6 — Constrained event extraction

**Objective.** Populate the graph from document text, restricted to the closed
vocabulary.

**Build.**
- `fi_intel/ingest/extract.py` — structured-output extraction returning typed
  Pydantic models. Character offsets for every claim.
- `fi_intel/ontology/validators.py` — rejects out-of-vocabulary types, routes
  them to `proposed_type`.
- Prompt versioning: every extraction records `extractor_version` and
  `prompt_version`.

**Acceptance criteria.**
- Extraction over the synthetic corpus produces the expected event types with
  correct dates. Assert on the specific events, not on a count.
- An out-of-vocabulary type never reaches the graph. Test by prompting toward an
  invented relation and asserting it lands in `proposed_type`.
- Every claim's character offsets resolve to real text in the source document.
- Unit tests stub the model and assert on the constructed request. Live-model
  behaviour goes in `evals/`.

**Non-goals.** No pattern detection. No coreference beyond within-document.

**Verify.** `pytest tests/test_extract.py -v && python -m evals.extraction_quality`

---

## Milestone 7 — Pattern query library

**Objective.** Five deterministic detectors that fire on the positive episode
and stay silent on the decoy.

**Build.**
- `fi_intel/graph/queries/` — five parameterized Cypher templates, each
  versioned, each with a named owner in a docstring:
  1. maturity wall with no announced refinancing
  2. negative rating action combined with declining capital metric
  3. treasury or CFO leadership change at a covered entity
  4. board-approved issuance programme
  5. one you propose, justified from the FI plays in the glossary
- A registry that runs all patterns and writes `Signal` nodes.
- CLI: `fi-intel patterns run --as-of <date>`, `fi-intel patterns explain <signal_id>`.

**Acceptance criteria.**
- Every expected signal in `ground_truth.json` fires for Gulf Meridian, and
  fires before day 205.
- **Zero signals fire for Northern Harbour.** This is the criterion that
  matters most. If a threshold change makes the positive case better and the
  decoy worse, the change is rejected.
- `patterns explain` returns the exact subgraph and documents that caused a
  signal to fire.
- Each pattern is independently toggleable and independently tested.

**Non-goals.** No LLM. No scoring beyond a raw priority integer.

**Verify.** `pytest tests/test_patterns.py -v && fi-intel patterns run --as-of 2024-06-01`

---

## Milestone 8 — Fork the agent

**Objective.** Rewire Open Deep Research to use our tools and our graph.

**Read first.** Upstream `deep_researcher.py`, `utils.py::get_all_tools`,
`configuration.py`, `state.py`.

**Build.**
- `fi_intel/tools/` — `corpus_search`, `graph_query` (parameterized templates
  only), `entity_profile`, `timeseries_lookup`, `precedent_search`.
- Modify `get_all_tools()` to return our tools. **Delete** the Tavily and
  native-search code paths and their config enum members.
- New graph nodes: `signal_intake`, `graph_context_hydration`,
  `precedent_retrieval`, `hypothesis_scoring`, `compliance_gate`,
  `graph_writeback`.
- Extend `AgentState` with `signals`, `graph_context`, `hypotheses`,
  `evidence: list[EvidenceItem]`.
- Rewrite prompts: every claim carries an evidence ID; every hypothesis states
  its falsifier; "insufficient evidence" is an explicit, blessed outcome.

**Acceptance criteria.**
- `grep -ri "tavily\|web_search" open_deep_research/ fi_intel/` returns nothing.
- The agent runs end-to-end on a Gulf Meridian signal and produces a structured
  `Opportunity` with resolvable evidence IDs.
- Given a signal with no supporting evidence, the agent returns "insufficient
  evidence" rather than constructing a narrative. Test this deliberately with a
  synthetic signal that has no corroborating documents.
- Output containing an unresolvable evidence ID fails validation and is not
  published.
- The ad-hoc entry point (`clarify_with_user`) still works alongside the
  scheduled one.

**Non-goals.** No brief formatting. No scheduling.

**Verify.** `pytest tests/test_agent.py -v && fi-intel research --signal <id>`

---

## Milestone 9 — Brief compiler

**Objective.** A daily brief a banker will actually read.

**Build.**
- `fi_intel/agents/brief.py` — tiered routing (patterns → triage → deep research
  → assembly), per-tier budget caps.
- Static HTML output, one page, each item linking to its evidence.
- CLI: `fi-intel brief --as-of <date> --desk <id> --out <path>`.

**Acceptance criteria.**
- On a day with nothing material, the brief says so. It does not pad. Test with
  a corpus window containing only decoy documents.
- Every claim links to a real document and highlights the cited span.
- Per-run cost is logged and a configurable ceiling aborts the run rather than
  silently overspending.
- The brief renders correctly with zero items, one item, and twenty items.

**Non-goals.** No web UI, no auth, no email delivery.

**Verify.** `fi-intel brief --as-of 2024-06-01 --desk fi_gcc --out /tmp/brief.html`

---

## Milestone 10 — Backtest harness

**Objective.** Measure lead time. The number the whole project is judged on.

**Build.**
- `evals/backtest.py` — replay at an as-of date with both corpus and graph
  pinned; compare fired signals against the outcome ledger.
- Metrics: precision@10, recall, lead-time-to-mandate in days, per-pattern
  attribution.
- CLI: `fi-intel backtest --from <date> --to <date> --step 7d`.

**Acceptance criteria.**
- A deliberate leakage attempt fails loudly. Write a test that tries to read an
  assertion recorded after the cutoff and assert it raises.
- Per-pattern attribution shows which detectors earned their keep and which
  fired without ever preceding a real outcome.
- Results are reproducible: same inputs, same seed, same numbers.
- Report the actual lead-time distribution, not just a mean. A detector that
  fires 400 days early on everything is not useful.

**Non-goals.** No optimization based on the results. Measure first, then discuss.

**Verify.** `pytest evals/test_backtest.py -v && fi-intel backtest --from 2024-02-01 --to 2024-08-01 --step 7d`

---

## Session hygiene

**Ask for the checklist first.** If the agent's opening move isn't restating the
acceptance criteria, stop and ask for it. An agent that starts writing code
immediately has decided what the task means without telling you.

**Reject "tests pass" without output.** Run the verification command yourself.
This catches the most common failure mode, which is tests that pass because they
assert nothing.

**Read the negative tests before the implementation.** The decoy test and the
leakage test are where correctness actually lives. If those are thin, the
milestone is not done regardless of how good the rest looks.

**Watch for silently weakened invariants.** A fallback added "temporarily," a
threshold loosened to make a test pass, an exception swallowed to get a run to
complete. Ask directly at the end of each session: *which invariants did you
weaken, and where?*

**Commit per milestone, never per session.** You want to be able to revert one
milestone cleanly when the backtest tells you milestone 7's thresholds were wrong.
