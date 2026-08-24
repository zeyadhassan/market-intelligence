"""Command-line entry point.

This is the only module where print() is permitted. Subcommands are added
per milestone; unimplemented milestones fail loudly rather than silently
no-oping.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated

import typer

from fi_intel.config import Settings
from fi_intel.ingest.pipeline import IngestPipeline, IngestResult
from fi_intel.ingest.resolve import QueuedMention
from fi_intel.ingest.store import PostgresDocumentStore, SourceStatus
from fi_intel.logging import configure_logging, get_logger, new_run_id
from fi_intel.sources.base import SourceAdapter
from fi_intel.sources.canonical import CanonicalDocument
from fi_intel.sources.fixture import synthetic_wire

app = typer.Typer(name="fi-intel", help="FI market intelligence platform.", no_args_is_help=True)


def _resolve_adapter(source: str) -> SourceAdapter:
    if source == "synthetic_wire":
        return synthetic_wire()
    if source == "synthetic_wire_private":
        from fi_intel.sources.fixture import synthetic_wire_private

        return synthetic_wire_private()
    raise typer.BadParameter(f"unknown source {source!r}")


@app.callback()
def _main(log_level: Annotated[str, typer.Option(envvar="FI_INTEL_LOG_LEVEL")] = "INFO") -> None:
    configure_logging(log_level)


sources_app = typer.Typer(help="Inspect registered sources.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@sources_app.command("list")
def sources_list() -> None:
    """List registered source adapters."""
    adapter = synthetic_wire()
    print(f"{adapter.source_id}\tfixture")  # noqa: T201


@sources_app.command("peek")
def sources_peek(
    source: Annotated[str, typer.Option(help="Source adapter id.")] = "synthetic_wire",
    limit: Annotated[int, typer.Option(min=1)] = 5,
) -> None:
    """Print the first N canonical documents a source serves (smoke test)."""
    adapter = _resolve_adapter(source)

    async def _peek() -> list[tuple[str, str]]:
        out: list[tuple[str, str]] = []
        # See sources/base.py note on async-generator typing.
        stream: AsyncIterator[CanonicalDocument] = adapter.fetch()  # type: ignore[assignment]
        async for doc in stream:
            out.append((doc.doc_id, doc.title))
            if len(out) >= limit:
                break
        return out

    run_id = new_run_id()
    log = get_logger(command="sources.peek", source=source)
    log.info("peek.start", limit=limit)
    for doc_id, title in asyncio.run(_peek()):
        print(f"{doc_id}\t{title}")  # noqa: T201
    log.info("peek.done", run_id=run_id)


ingest_app = typer.Typer(help="Ingestion pipeline (deterministic plane).", no_args_is_help=True)
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("run")
def ingest_run(
    source: Annotated[str, typer.Option(help="Source adapter id.")],
    batch_size: Annotated[int, typer.Option(min=1)] = 5,
) -> None:
    """Ingest one source into Postgres. Idempotent and resumable."""
    new_run_id()
    adapter = _resolve_adapter(source)
    settings = Settings()
    store = PostgresDocumentStore(settings.postgres_dsn)

    async def _run() -> IngestResult:
        try:
            return await IngestPipeline(store, batch_size=batch_size).run(adapter)
        finally:
            await store.close()

    result = asyncio.run(_run())
    print(  # noqa: T201
        f"{result.source_id}: fetched={result.fetched} persisted={result.persisted} "
        f"near_dupes={result.near_duplicates} exact_dupes={result.exact_duplicates} "
        f"batches={result.batches_committed} cursor={result.final_cursor}"
    )


@ingest_app.command("status")
def ingest_status() -> None:
    """Show per-source document counts and cursor positions."""
    settings = Settings()
    store = PostgresDocumentStore(settings.postgres_dsn)

    async def _status() -> list[SourceStatus]:
        try:
            return await store.status()
        finally:
            await store.close()

    rows = asyncio.run(_status())
    if not rows:
        print("no sources registered")  # noqa: T201
        return
    print(f"{'source_id':<20} {'docs':>6} {'dupes':>6} {'cursor':>8}  updated_at")  # noqa: T201
    for row in rows:
        updated = row.cursor_updated_at.isoformat() if row.cursor_updated_at else "-"
        print(  # noqa: T201
            f"{row.source_id:<20} {row.document_count:>6} {row.duplicate_count:>6} "
            f"{row.cursor_position or '-':>8}  {updated}"
        )


entities_app = typer.Typer(help="Entity resolution (mentions → LEIs).", no_args_is_help=True)
app.add_typer(entities_app, name="entities")


@entities_app.command("resolve")
def entities_resolve() -> None:
    """Load the GLEIF reference table and resolve all ingested mentions."""
    new_run_id()
    settings = Settings()

    async def _run() -> tuple[int, int]:
        from fi_intel.ingest.resolve import EntityResolver
        from fi_intel.ingest.resolve_store import PostgresResolutionStore
        from fi_intel.sources.adapters.gleif import gleif_fixture

        doc_store = PostgresDocumentStore(settings.postgres_dsn)
        res_store = PostgresResolutionStore(settings.postgres_dsn)
        try:
            reference_docs = [d async for d in gleif_fixture().fetch()]
            await res_store.load_reference(reference_docs)
            resolver = EntityResolver(res_store)
            docs = await doc_store.load_documents("synthetic_wire")
            if not docs:
                print("no ingested documents; run 'fi-intel ingest run' first")  # noqa: T201
                return (0, 0)
            for doc in docs:
                await resolver.resolve_document(doc)
            return (len(await res_store.resolutions()), len(await res_store.queue()))
        finally:
            await doc_store.close()
            await res_store.close()

    resolved, queued = asyncio.run(_run())
    print(f"resolutions recorded: {resolved}; queued for review: {queued}")  # noqa: T201


@entities_app.command("queue")
def entities_queue() -> None:
    """Show mentions awaiting human review."""
    settings = Settings()

    async def _queue() -> list[QueuedMention]:
        from fi_intel.ingest.resolve_store import PostgresResolutionStore

        res_store = PostgresResolutionStore(settings.postgres_dsn)
        try:
            return await res_store.queue()
        finally:
            await res_store.close()

    rows = asyncio.run(_queue())
    if not rows:
        print("resolution queue is empty")  # noqa: T201
        return
    print(f"{'doc_id':<16} {'mention':<40} {'candidate':<24} score")  # noqa: T201
    for row in rows:
        score = f"{row.best_score:.1f}" if row.best_score is not None else "-"
        print(  # noqa: T201
            f"{row.doc_id:<16} {row.mention_text:<40} {row.candidate_lei or '-':<24} {score}"
        )


@app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="Search text.")],
    as_of: Annotated[
        str | None, typer.Option(help="ISO date; only docs recorded on/before it.")
    ] = None,
    group: Annotated[str, typer.Option(help="Entitlement group.")] = "fi_gcc_public",
    principal: Annotated[str, typer.Option(help="Caller identity for the audit log.")] = "cli.user",
    side: Annotated[str, typer.Option(help="Barrier side: public|private.")] = "public",
    entity_lei: Annotated[str | None, typer.Option(help="Restrict to one entity.")] = None,
    limit: Annotated[int, typer.Option(min=1)] = 10,
) -> None:
    """Hybrid corpus search with entitlement enforced in SQL and every
    retrieval written to access_log."""
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.retrieval.chunking import HashingEmbedder
    from fi_intel.retrieval.corpus import CorpusSearch, ScoredChunk
    from fi_intel.retrieval.entitlement import Principal, Side
    from fi_intel.retrieval.service import RetrievalService
    from fi_intel.retrieval.store import PostgresCorpusStore

    run_id = new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC) if as_of else None
    caller = Principal(
        principal_id=principal, entitlement_group=group, side=Side(side)
    )

    async def _run() -> list[ScoredChunk]:
        store = PostgresCorpusStore(settings.postgres_dsn)
        audit = PostgresAuditLog(settings.postgres_dsn)
        try:
            embedder = HashingEmbedder()
            indexed = await store.index_chunks(embedder)
            if indexed:
                print(f"indexed {indexed} chunks")  # noqa: T201
            service = RetrievalService(CorpusSearch(store, embedder), audit, run_id)
            return await service.search(
                query, caller, as_of=as_of_dt, entity_lei=entity_lei, limit=limit
            )
        finally:
            await store.close()
            await audit.close()

    results = asyncio.run(_run())
    if not results:
        print("no results")  # noqa: T201
        return
    for r in results:
        span = f"{r.chunk.char_start}-{r.chunk.char_end}"
        print(  # noqa: T201
            f"{r.score:.4f}  {r.doc.source_id}/{r.doc.doc_id} [{span}]  {r.doc.title}"
        )


@app.command("migrate")
def migrate() -> None:
    """Apply pending graph schema migrations to Neo4j."""
    from fi_intel.graph.client import GraphClient

    settings = Settings()

    async def _migrate() -> int:
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            return await client.migrate()
        finally:
            await client.close()

    version = asyncio.run(_migrate())
    print(f"graph schema at version {version}")  # noqa: T201


@app.command("extract")
def extract(
    source: Annotated[str, typer.Option(help="Source whose docs to extract.")] = "synthetic_wire",
) -> None:
    """Run constrained extraction over ingested documents into the graph.

    NOTE: requires a live StructuredExtractor (LLM) which is not configured
    in this scaffold; the command demonstrates wiring and reports per-doc
    results. Unit tests cover behaviour with a stubbed model.
    """
    from fi_intel.graph.client import GraphClient

    new_run_id()
    settings = Settings()

    async def _run() -> list[object]:
        doc_store = PostgresDocumentStore(settings.postgres_dsn)
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            await client.migrate()
            docs = await doc_store.load_documents(source)
            if not docs:
                print(f"no ingested documents for {source!r}")  # noqa: T201
                return []
            # No live extractor is configured in this scaffold.
            print(  # noqa: T201
                "no StructuredExtractor configured; wire an LLM client to run "
                "extraction. See fi_intel.ingest.extract.StructuredExtractor."
            )
            return []
        finally:
            await doc_store.close()
            await client.close()

    asyncio.run(_run())


patterns_app = typer.Typer(help="Deterministic pattern detectors.", no_args_is_help=True)
app.add_typer(patterns_app, name="patterns")


@patterns_app.command("run")
def patterns_run(
    as_of: Annotated[str, typer.Option(help="ISO date; detectors pin to it.")],
    only: Annotated[
        str | None, typer.Option(help="Comma-separated pattern names to enable.")
    ] = None,
    window_days: Annotated[int, typer.Option(min=1)] = 395,
) -> None:
    """Run the pattern registry at an as-of date and list fired signals."""
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.registry import PatternRegistry, Signal

    new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
    enabled = set(only.split(",")) if only else None

    async def _run() -> list[Signal]:
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            await client.migrate()
            return await PatternRegistry(client).run(
                as_of_dt, enabled=enabled, window_days=window_days
            )
        finally:
            await client.close()

    signals = asyncio.run(_run())
    if not signals:
        print("no signals fired")  # noqa: T201
        return
    for s in signals:
        print(f"[{s.priority:>3}] {s.pattern:<42} {s.entity_name} ({s.entity_key})")  # noqa: T201


@patterns_app.command("seed-fixture")
def patterns_seed_fixture() -> None:
    """Write the synthetic episode graph fixture (demo/test data)."""
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.writer import AssertionWriter
    from fi_intel.synth.graph_fixture import (
        gulf_meridian_assertions,
        northern_harbour_assertions,
    )

    settings = Settings()

    async def _seed() -> int:
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            await client.migrate()
            writer = AssertionWriter(client)
            assertions = gulf_meridian_assertions() + northern_harbour_assertions()
            for a in assertions:
                await writer.write(a)
            return len(assertions)
        finally:
            await client.close()

    n = asyncio.run(_seed())
    print(f"wrote {n} fixture assertions")  # noqa: T201


@patterns_app.command("explain")
def patterns_explain(
    signal_id: Annotated[str, typer.Argument(help="Signal id to explain.")],
) -> None:
    """Show the evidence (subgraph + documents) behind a signal."""
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.registry import PatternRegistry, Signal

    settings = Settings()

    async def _explain() -> Signal | None:
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            return await PatternRegistry(client).explain(signal_id)
        finally:
            await client.close()

    signal = asyncio.run(_explain())
    if signal is None:
        print(f"no signal {signal_id!r}")  # noqa: T201
        return
    print(f"{signal.pattern} v on {signal.entity_name} (priority {signal.priority})")  # noqa: T201
    for k, v in signal.evidence.items():
        print(f"  {k}: {v}")  # noqa: T201


@app.command("research")
def research(
    signal_id: Annotated[str, typer.Option(help="Signal id to research.")],
    as_of: Annotated[str, typer.Option(help="ISO date for the temporal pin.")],
    group: Annotated[str, typer.Option()] = "fi_gcc_public",
    principal: Annotated[str, typer.Option()] = "cli.user",
    side: Annotated[str, typer.Option()] = "public",
) -> None:
    """Research a fired signal into a validated Opportunity.

    No live reasoning model is configured in this scaffold; the command runs
    signal intake, graph hydration, and precedent retrieval, then reports.
    Wire a ReasoningModel to complete hypothesis scoring.
    """
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.registry import PatternRegistry
    from fi_intel.retrieval.chunking import HashingEmbedder
    from fi_intel.retrieval.corpus import CorpusSearch
    from fi_intel.retrieval.entitlement import Principal, Side
    from fi_intel.retrieval.service import RetrievalService
    from fi_intel.retrieval.store import PostgresCorpusStore
    from fi_intel.tools.research_tools import ResearchTools, ToolContext

    new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
    caller = Principal(principal_id=principal, entitlement_group=group, side=Side(side))

    async def _run() -> None:
        store = PostgresCorpusStore(settings.postgres_dsn)
        audit = PostgresAuditLog(settings.postgres_dsn)
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            await client.migrate()
            registry = PatternRegistry(client)
            signal = await registry.explain(signal_id)
            if signal is None:
                print(f"no signal {signal_id!r}; run 'fi-intel patterns run' first")  # noqa: T201
                return
            embedder = HashingEmbedder()
            await store.index_chunks(embedder)
            retrieval = RetrievalService(CorpusSearch(store, embedder), audit, new_run_id())
            ctx = ToolContext(principal=caller, as_of=as_of_dt)
            tools = ResearchTools(retrieval, client, registry, ctx)
            profile = await tools.entity_profile(signal.entity_key)
            evidence = await tools.corpus_search(signal.entity_name, entity_lei=signal.entity_key)
            print(f"signal: {signal.pattern} on {signal.entity_name}")  # noqa: T201
            print(f"graph assertions: {profile['assertion_count']}")  # noqa: T201
            print(f"corpus evidence: {len(evidence)}")  # noqa: T201
            if not evidence and profile["assertion_count"] == 0:
                print("outcome: insufficient evidence")  # noqa: T201
        finally:
            await store.close()
            await audit.close()
            await client.close()

    asyncio.run(_run())


@app.command("brief")
def brief(
    as_of: Annotated[str, typer.Option(help="ISO date; the temporal pin.")],
    desk: Annotated[str, typer.Option(help="Desk id, e.g. fi_gcc.")],
    out: Annotated[str, typer.Option(help="Output HTML path.")],
    group: Annotated[str, typer.Option()] = "fi_gcc_public",
    budget: Annotated[float, typer.Option(min=1.0)] = 1000.0,
) -> None:
    """Compile the daily brief and write static HTML."""
    from fi_intel.agents.brief import BriefCompiler, BudgetExceededError
    from fi_intel.agents.opportunity_research import OpportunityResearcher
    from fi_intel.agents.render import render_html
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.registry import PatternRegistry
    from fi_intel.retrieval.chunking import HashingEmbedder
    from fi_intel.retrieval.corpus import CorpusSearch
    from fi_intel.retrieval.entitlement import Principal, Side
    from fi_intel.retrieval.service import RetrievalService
    from fi_intel.retrieval.store import PostgresCorpusStore
    from fi_intel.tools.research_tools import ResearchTools, ToolContext

    new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
    caller = Principal(principal_id="cli.user", entitlement_group=group, side=Side.PUBLIC)

    from fi_intel.agents.opportunity_research import ResearchRequest, ResearchResponse

    class _StubModel:
        async def research(self, request: ResearchRequest) -> ResearchResponse:
            return ResearchResponse(
                title=f"Opportunity: {request.signal_pattern}",
                summary="Stub summary (no live reasoning model wired).",
                falsifier="See desk review.",
                evidence_indices=[0, 1],
            )

    async def _run() -> str:
        store = PostgresCorpusStore(settings.postgres_dsn)
        audit = PostgresAuditLog(settings.postgres_dsn)
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            await client.migrate()
            registry = PatternRegistry(client)
            embedder = HashingEmbedder()
            await store.index_chunks(embedder)
            retrieval = RetrievalService(CorpusSearch(store, embedder), audit, new_run_id())
            ctx = ToolContext(principal=caller, as_of=as_of_dt)
            tools = ResearchTools(retrieval, client, registry, ctx)
            researcher = OpportunityResearcher(tools, _StubModel())
            compiler = BriefCompiler(registry, tools, researcher, budget_ceiling=budget)
            result = await compiler.compile(as_of_dt, desk=desk)
            return render_html(result)
        finally:
            await store.close()
            await audit.close()
            await client.close()

    try:
        html = asyncio.run(_run())
    except BudgetExceededError as exc:
        raise typer.BadParameter(str(exc)) from exc
    from pathlib import Path

    Path(out).write_text(html, encoding="utf-8")
    print(f"brief written to {out}")  # noqa: T201


@app.command("backtest")
def backtest(
    from_date: Annotated[str, typer.Option("--from", help="ISO start date.")],
    to_date: Annotated[str, typer.Option("--to", help="ISO end date.")],
    step: Annotated[str, typer.Option(help="Step, e.g. 7d.")] = "7d",
) -> None:
    """Measure lead time against the outcome ledger."""
    from evals.backtest import Backtester, BacktestResult, Outcome
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.registry import PatternRegistry
    from fi_intel.synth.episodes import GULF_MERIDIAN_LEI

    new_run_id()
    settings = Settings()
    start = datetime.fromisoformat(from_date).date()
    end = datetime.fromisoformat(to_date).date()
    step_days = int(step.rstrip("d"))

    mandate = Outcome(
        outcome_id="mandate:gm-2024-07-10",
        entity_key=GULF_MERIDIAN_LEI,
        outcome_date=datetime(2024, 7, 10).date(),
        kind="mandate_announced",
    )

    async def _run() -> BacktestResult:
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        try:
            await client.migrate()
            return await Backtester(client, PatternRegistry(client)).run(
                start, end, step_days, outcomes=[mandate]
            )
        finally:
            await client.close()

    result = asyncio.run(_run())
    print(  # noqa: T201
        f"precision@10={result.precision_at_10}  recall={result.recall}  "
        f"signals={result.total_signals}"
    )
    for a in result.attribution:
        leads = a.lead_days
        dist = f"min={leads[0]} median={leads[len(leads)//2]} max={leads[-1]}" if leads else "n/a"
        print(  # noqa: T201
            f"  {a.pattern:<40} fired={a.fired} preceded={a.preceded_outcome} lead({dist})d"
        )


@app.command("version")
def version() -> None:
    """Print version and active configuration summary."""
    import fi_intel

    settings = Settings()
    print(f"fi-intel {fi_intel.__version__}")  # noqa: T201
    print(f"postgres: {settings.postgres_dsn}")  # noqa: T201
    print(f"neo4j:    {settings.neo4j_uri}")  # noqa: T201


if __name__ == "__main__":
    app()
