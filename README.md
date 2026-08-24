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
| 8 | Fork the agent | ⬜ Not started |
| 9 | Brief compiler | ⬜ Not started |
| 10 | Backtest harness | ⬜ Not started |

## Synthetic corpus

12 raw wire records → 10 unique documents after dedupe. Two episodes:
Gulf Meridian (positive, 4 expected signals, mandate outcome on
2024-07-10) and Northern Harbour (steady-state decoy, zero expected
signals). See [fi_intel/synth/data/README.md](fi_intel/synth/data/README.md).
