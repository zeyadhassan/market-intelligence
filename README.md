# fi-intel

Market intelligence platform for the Financial Institutions unit. See
[CLAUDE.md](CLAUDE.md) for architecture decisions, invariants, and
anti-patterns — read it before touching anything. Milestones are defined in
[BUILD_PROMPTS.md](BUILD_PROMPTS.md).

## Setup

```powershell
python -m venv .venv
.venv\Scripts\pip install -e ".[dev]"
docker compose -f deploy/docker-compose.yml up -d   # Postgres 16 + pgvector, Neo4j 5
```

## Verify

```powershell
pytest            # unit tests (DB-backed tests skip without env vars)
ruff check .
mypy
fi-intel sources peek --source synthetic_wire

# With the compose stack running (Postgres + Neo4j):
$env:FI_INTEL_TEST_PG_DSN = "postgresql://fi_intel:fi_intel@localhost:5432/fi_intel"
$env:FI_INTEL_TEST_NEO4J_URI = "bolt://localhost:7687"
pytest            # now includes live Postgres + Neo4j contract tests
fi-intel ingest run --source synthetic_wire
fi-intel ingest status
fi-intel entities resolve
fi-intel entities queue
fi-intel search "sukuk maturity" --group fi_gcc_public --as-of 2024-06-01
fi-intel migrate  # apply graph schema migrations

# Open-web sources (see "Open-web sources" below) — set a real contact
# first, or SEC.gov returns 403:
$env:FI_INTEL_RSS_USER_AGENT = "YourOrg you@yourorg.com"
fi-intel sources peek --source sec_edgar_8k
fi-intel sources peek --source fed_press_releases
```

## Roadmap

| # | Milestone | Status |
|---|---|---|
| 1 | Foundation: canonical boundary, adapter protocol + contract test, synthetic corpus, deploy artifacts | ✅ Done |
| 2 | Ingestion pipeline (dedupe, store, cursors) | ✅ Done (live-verified) |
| 3 | GLEIF entity resolution | ✅ Done (precision 1.0; live-verified) |
| 4 | Hybrid retrieval behind entitlement filter | ✅ Done (MRR hybrid .875 / BM25 .823 / vector .917; live-verified) |
| 5 | Bi-temporal graph writer | ✅ Done (live-verified against Neo4j 5) |
| 6 | Constrained event extraction | ✅ Done (stub-model tests live-verified; real LLM extractor not yet wired) |
| 7 | Pattern query library | ✅ Done (5 detectors; all fire for Gulf Meridian, zero for the decoy; live-verified) |
| 8 | Fork the agent | ✅ Done (Tavily/native search deleted; tools+evidence validation live-verified; no live reasoning LLM wired) |
| 9 | Brief compiler | ✅ Done (tiered routing, budget abort, decoy-day no-padding; live-verified + browser-rendered) |
| 10 | Backtest harness | ✅ Done (leakage gate, per-pattern attribution, distribution, reproducible; live-verified) |

All ten milestones complete.

## Open-web sources

Two demo-only ingestion adapters (`fi_intel/sources/adapters/rss.py`) pull
real, live content for demoing the pipeline against something other than
the synthetic corpus:

| source_id | Feed | document_class |
|---|---|---|
| `sec_edgar_8k` | SEC EDGAR current 8-K filings (Atom) | `filing` |
| `fed_press_releases` | Federal Reserve press releases (RSS) | `regulatory` |

Both are freely published government feeds, not licensed vendor content —
that distinction is carried structurally in `source_registry.licence_group
= 'open_web_public'` (deploy/init.sql), never conflated with a paid wire.
Parsing is stdlib-only (`xml.etree` + `email.utils`), not a feed-parsing
library, per CLAUDE.md's dependency-stack discipline.

SEC.gov rejects unidentified traffic with 403; set
`FI_INTEL_RSS_USER_AGENT` to your own `Org contact@example.com` before
fetching from `sec_edgar_8k` — the shipped default is a deliberately
unusable placeholder. `fi-intel sources peek --source <id>` fetches
without touching Postgres; `fi-intel ingest run --source <id>` persists
through the same entitlement-checked path as every other source.

Contract-tested via network-free fixtures captured from real feed
responses (`fi_intel/synth/data/*_sample.xml`), registered in
`tests/test_adapter_contract.py` alongside every other adapter, plus
adapter-specific parsing tests in `tests/test_rss_adapter.py`.

## Synthetic corpus

12 raw wire records → 10 unique documents after dedupe. Two episodes:
Gulf Meridian (positive, 4 expected signals, mandate outcome on
2024-07-10) and Northern Harbour (steady-state decoy, zero expected
signals). See [fi_intel/synth/data/README.md](fi_intel/synth/data/README.md).
