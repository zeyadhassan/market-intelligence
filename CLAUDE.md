# CLAUDE.md

Persistent project context. Read this before every task. If a task instruction
contradicts this file, stop and flag the contradiction rather than guessing.

---

## What we are building

A market intelligence platform for the Financial Institutions unit of a bank.
It ingests licensed financial news and filings, builds a temporal knowledge
graph of institutions and the events affecting them, detects patterns that
historically precede business opportunities, and compiles a daily brief for
coverage bankers with every claim traceable to a source document.

Built on a fork of `langchain-ai/open_deep_research` (MIT). Upstream is kept as
a git remote. Our code lives in `fi_intel/`; only three upstream files are ever
modified: `configuration.py`, `state.py`, `deep_researcher.py`.

## Who this is for and why it matters

Users are coverage bankers at a regulated institution. Outputs influence client
outreach and may be reviewed by Compliance, Internal Audit, and Model Risk
Management under SR 11-7-equivalent standards.

Consequences of this, which are not negotiable:
- A hallucinated claim about a client is a client-relationship incident.
- An unlicensed source in the corpus is a vendor contract breach.
- Private-side information reaching a public-side user is a regulatory incident.

Correctness beats coverage. Silence beats a plausible guess. When in doubt,
produce less and flag more.

---

## Architecture decisions already made

Do not re-litigate these. If you believe one is wrong, say so explicitly and
wait — do not quietly implement an alternative.

| Decision | Rationale |
|---|---|
| Two planes: deterministic ingestion, probabilistic reasoning | Different tempos, different failure modes. Never run ingestion inside an agent turn. |
| Retrieval over a private licensed corpus, never open-web search | Source restriction must be structural, not prompted |
| Event-centric graph, not entity-attribute | Attributes derived by projecting events to a timestamp; no lossy overwrites |
| Bi-temporal: `valid_from`/`valid_to` plus `recorded_at` | Without transaction time, backtests leak future knowledge |
| Append-only assertions | Corrections supersede, never mutate |
| Rules find, LLM qualifies | Deterministic patterns for recall; LLM for precision and reasoning |
| Parameterized graph queries only | Free-form LLM-generated Cypher is an injection and accuracy risk |
| Neo4j for graph, Postgres + pgvector for evidence | Pragmatic, operable by a small team |
| `CanonicalDocument` is the source boundary | Makes a new vendor a config change, not a refactor |

---

## Invariants

These are enforced by tests. A change that breaks one is rejected regardless of
how much it improves anything else.

1. **No vendor field names downstream of an adapter.** If `factiva_`, `rdp_`,
   or any provider-specific identifier appears outside `fi_intel/sources/`, the
   abstraction has leaked. Also: no `if doc.source_id == "<provider>"` outside
   `sources/`. Push the difference into the adapter.

2. **No code path reaches the open internet for content retrieval.** Not as a
   fallback, not "for coverage," not behind a feature flag. Upstream's Tavily
   and native-search integrations are deleted, not disabled. Reference data
   fetches (GLEIF bulk, EDGAR) go through registered adapters like anything else.

3. **Entitlement filtering happens in the data layer.** Every retrieval query
   joins against the source registry and filters by the caller's entitlement
   group and barrier side. Never in a system prompt. A prompt-level restriction
   is not a control.

4. **Every graph assertion carries provenance and both time axes.** Minimum:
   `source_doc_id`, `snippet_offset`, `extractor_version`, `confidence`,
   `valid_from`, `recorded_at`. An assertion that cannot name its source
   document is not written.

5. **Extraction is constrained to the closed T-Box vocabulary.** The model
   selects from a fixed enum of node and edge types. Anything it wants to
   invent goes to a `proposed_type` review queue and is never auto-admitted.

6. **Entity merges require a deterministic key or human review.** LEI, BIC,
   ISIN, or PermID match auto-merges. Fuzzy matches above threshold auto-merge
   only with the resolver and score recorded. Everything else queues. The model
   never merges entities on its own judgement.

7. **Every claim in generated output carries an evidence ID** resolving to a
   real document and character span. Output containing an unresolvable evidence
   ID fails validation and is not published.

8. **The agent can always return nothing.** "No material developments" is a
   valid, expected daily output. Never add logic, prompt language, or minimum
   counts that pressure the system to fill a page.

9. **Ingestion failures are loud.** Never swallow an exception and continue.
   Silent data loss is worse than a crashed job, because a gap in the corpus
   silently corrupts every backtest that spans it.

10. **Backtest reads are pinned.** Any as-of evaluation pins both the corpus
    cutoff and the graph read session to `recorded_at <= as_of`. A backtest that
    can see the outcome is measuring hindsight.

---

## Repository layout

```
fi_intel/
  sources/       canonical.py, base.py, fixture.py, vendor_stub.py, adapters/
  ingest/        pipeline.py, dedupe.py, normalize.py, resolve.py, extract.py
  ontology/      schema.py, vocab.py, validators.py
  graph/         client.py, writer.py, queries/          (versioned templates)
  retrieval/     corpus.py, precedent.py, entitlement.py
  tools/         corpus_search.py, graph_query.py, entity_profile.py,
                 timeseries.py, precedent_search.py
  agents/        triage.py, opportunity_research.py, brief.py
  governance/    entitlements.py, wall.py, audit.py
  synth/         episodes.py
  cli.py
open_deep_research/    upstream fork, minimal diffs
tests/
evals/           backtest harness, golden sets
deploy/          init.sql, docker-compose.yml
```

## Stack

Python 3.11+. Pydantic v2 for all schemas. LangGraph for agent orchestration.
Neo4j 5 (Cypher, GDS for motif similarity). Postgres 16 with pgvector and
`pg_trgm`. `httpx` + `tenacity` for outbound calls. `structlog` for logging.
`typer` for CLI. `pytest` + `pytest-asyncio` for tests. `ruff` and `mypy` for
static checks.

---

## Coding standards

- Type-annotate everything. `mypy --ignore-missing-imports` passes clean.
- Pydantic models for every data structure crossing a module boundary. No bare
  dicts between layers.
- `async` for all I/O. No blocking calls inside async functions.
- Structured logging with `structlog`. Every log line carries `run_id`. No
  `print()` outside `cli.py`.
- No bare `except:`. Catch specific exceptions. Re-raise or fail the job.
- Configuration via Pydantic settings objects, never module-level globals.
- Docstrings explain *why*, not *what*. The code says what.
- Comment non-obvious decisions, especially where a simpler approach was
  rejected for a domain reason. A future reader will otherwise "simplify" it back.

## Testing requirements

Every module ships with tests. No exceptions for "just plumbing."

- **Contract tests** for anything implementing a protocol. New adapters must
  pass `tests/test_adapter_contract.py` unmodified.
- **Golden-path test** per pipeline stage using the synthetic corpus.
- **Negative test** per detector. The decoy episode
  (`steady_state_decoy`, Northern Harbour Bank) must produce zero signals. A
  detector that fires on the decoy is broken even if it also fires correctly on
  the positive case.
- **Leakage test** for anything time-aware. Assert that as-of reads cannot see
  documents or assertions recorded after the cutoff.
- No network calls in tests. No live LLM calls in unit tests — stub the model
  and assert on the constructed request. LLM behaviour goes in `evals/`, which
  runs separately and is allowed to be slow and non-deterministic.

## Definition of done

A task is complete only when all of these hold:

1. `pytest` passes, including the negative and leakage tests for the new code.
2. `ruff check` and `mypy` pass clean.
3. New behaviour is demonstrable from the CLI with a documented command.
4. The README roadmap table is updated.
5. No invariant above is weakened.
6. Anything deliberately deferred is listed explicitly in the summary, not left
   silent.

---

## Anti-patterns

Specific to this project. Each one has been chosen over a plausible-looking
alternative for a reason.

**Do not add a web-search fallback** when corpus retrieval returns nothing. The
correct behaviour is to return nothing and let the agent say so.

**Do not put business logic in prompts.** Thresholds, scoring weights, source
restrictions, and entitlement rules live in code where they can be tested and
version-controlled. A prompt is not a config file.

**Do not let extracted facts become authoritative without provenance.** An
LLM-derived assertion with `confidence` and a `source_doc_id` is data. The same
assertion without them is a rumour with good grammar.

**Do not cache model output as ground truth.** Cache it as a model output, with
the model version and prompt version recorded, so it can be invalidated when
either changes.

**Do not batch-merge entities to improve resolution coverage.** A bad merge is
invisible and corrupts every downstream query about both entities. Low coverage
is a visible, fixable problem. Prefer it.

**Do not smooth over a schema mismatch in an adapter.** If a vendor field does
not map cleanly to `CanonicalDocument`, raise and flag it. A silently coerced
timestamp becomes a wrong `valid_from`, which becomes a wrong signal.

**Do not tune detectors against the positive episode alone.** Every threshold
change gets checked against the decoy. Precision is the metric that determines
whether anyone reads the second daily brief.

**Do not optimize token cost by removing the evidence trail.** Citations are the
product, not overhead.

---

## Domain glossary

Enough to read the code and the tickets without a finance background.

- **FI / FIG** — Financial Institutions Group. The desk. Their clients are other
  financial firms: banks, insurers, asset managers, sovereign wealth funds.
- **LEI** — Legal Entity Identifier. Free global ID for legal entities, issued
  via GLEIF, with parent/child hierarchy. Our primary entity key.
- **BIC / SWIFT code** — bank identifier used in payments. Secondary key.
- **ISIN** — security identifier. Primary key for instruments.
- **DCM** — Debt Capital Markets. Helping a client issue bonds.
- **Sukuk** — Sharia-compliant instrument, economically similar to a bond.
  Common in GCC markets; a first-class product for this desk.
- **AT1 / T2** — regulatory capital instruments banks issue. AT1 has a first
  call date, which is a scheduled, predictable event and therefore a strong signal.
- **Maturity wall** — a large volume of debt coming due in a narrow window. The
  issuer must refinance, which creates a mandate.
- **Mandate** — the client formally appointing banks to run a transaction. This
  is the outcome we are trying to predict *before* it is announced.
- **Correspondent banking** — one bank holding accounts for another to clear
  payments in a currency. A competitor exiting a corridor creates an opening.
- **Rating action** — an agency upgrading, downgrading, or changing outlook.
  High-signal, well-structured, and time-stamped.
- **MNPI** — Material Non-Public Information. Cannot cross from private side to
  public side. Drives the barrier design.
- **Public side / private side** — staff with access to MNPI (private) versus
  those without (public). Separate data, separate retrieval paths.
- **Lead time** — days between our system flagging a situation and the mandate
  being announced. The headline success metric.

---

## When to stop and ask

Stop and ask rather than guessing when:

- A task requires weakening an invariant.
- The correct behaviour depends on a licence term you do not have.
- A schema change would require rewriting existing assertions rather than
  appending.
- A detector's threshold cannot be justified from the synthetic ground truth.
- You are about to add a dependency not listed in the stack.

Asking costs one message. Guessing costs a rebuild.
