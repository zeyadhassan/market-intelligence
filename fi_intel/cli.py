"""Command-line entry point.

This is the only module where print() is permitted.
"""

import asyncio
from collections.abc import AsyncIterator
from datetime import UTC, datetime
from typing import Annotated
from urllib.parse import urlsplit

import typer

from fi_intel.config import Settings
from fi_intel.governance.policy import GraphAccessContext, PostgresEntitlementResolver
from fi_intel.graph.precision import PostgresPatternPrecisionProvider
from fi_intel.ingest.pipeline import IngestPipeline, IngestResult
from fi_intel.ingest.resolve import QueuedMention
from fi_intel.ingest.store import PostgresDocumentStore, SourceStatus
from fi_intel.logging import configure_logging, get_logger, new_run_id
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.sources.base import SourceAdapter
from fi_intel.sources.canonical import CanonicalDocument
from fi_intel.sources.fixture import synthetic_wire

app = typer.Typer(name="fi-intel", help="FI market intelligence platform.", no_args_is_help=True)

# Open-web sources (not licensed vendor feeds; see deploy/init.sql
# licence_group='open_web_public' and fi_intel/sources/adapters/rss.py).
_OPEN_WEB_SOURCES = ("sec_edgar_8k", "fed_press_releases")


def _configured_principal(settings: Settings) -> Principal:
    """Identity established by deployment configuration, never CLI flags."""
    return Principal(
        principal_id=settings.access_principal_id,
        entitlement_group=settings.access_entitlement_group,
        side=Side(settings.access_side),
    )


def _postgres_target(dsn: str) -> str:
    """Return a diagnostic target without credentials or query parameters."""
    parsed = urlsplit(dsn)
    host = parsed.hostname or "configured-host"
    port = f":{parsed.port}" if parsed.port is not None else ""
    database = parsed.path.lstrip("/") or "configured-database"
    return f"{host}{port}/{database}"


async def _resolve_graph_access(
    settings: Settings,
    principal: Principal,
    run_id: str,
) -> GraphAccessContext:
    resolver = PostgresEntitlementResolver(settings.postgres_dsn)
    try:
        return await resolver.resolve(principal, run_id)
    finally:
        await resolver.close()


def _resolve_adapter(source: str) -> SourceAdapter:
    if source == "synthetic_wire":
        return synthetic_wire()
    if source == "synthetic_wire_private":
        from fi_intel.sources.fixture import synthetic_wire_private

        return synthetic_wire_private()
    if source in _OPEN_WEB_SOURCES:
        raise typer.BadParameter(
            f"{source!r} is a raw-first feed/detail source and cannot use the "
            "prototype canonical-document ingestion path"
        )
    raise typer.BadParameter(f"unknown source {source!r}")


@app.callback()
def _main(log_level: Annotated[str, typer.Option(envvar="FI_INTEL_LOG_LEVEL")] = "INFO") -> None:
    configure_logging(log_level)


sources_app = typer.Typer(help="Inspect registered sources.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@sources_app.command("list")
def sources_list() -> None:
    """List registered source adapters."""
    from fi_intel.sources.catalog import production_source_catalog

    print(f"{synthetic_wire().source_id}\tfixture")  # noqa: T201
    catalog = production_source_catalog()
    enabled = {item.source_id for item in catalog.enabled()}
    for source_id in _OPEN_WEB_SOURCES:
        state = "enabled" if source_id in enabled else "disabled-noncoverage"
        print(f"{source_id}\topen_web\t{state}")  # noqa: T201


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


ingest_app = typer.Typer(
    help="Prototype v1 canonical-document ingestion (non-production).",
    no_args_is_help=True,
)
app.add_typer(ingest_app, name="ingest")


@ingest_app.command("run")
def ingest_run(
    source: Annotated[str, typer.Option(help="Source adapter id.")],
    batch_size: Annotated[int, typer.Option(min=1)] = 5,
) -> None:
    """Prototype only: ingest already-canonical documents into the v1 store."""
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


entities_app = typer.Typer(help="Entity resolution (mentions -> LEIs).", no_args_is_help=True)
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


index_app = typer.Typer(
    help="Corpus chunk/embedding index (retrieval plane).", no_args_is_help=True
)
app.add_typer(index_app, name="index")


@index_app.command("run")
def index_run() -> None:
    """Chunk+embed any ingested documents that have no chunks yet."""
    from fi_intel.retrieval.embedders.openai_compatible_embedder import build_embedder
    from fi_intel.retrieval.store import PostgresCorpusStore

    new_run_id()
    settings = Settings()
    embedder = build_embedder(settings)

    async def _run() -> int:
        store = PostgresCorpusStore(settings.postgres_dsn)
        try:
            return await store.index_chunks(embedder)
        finally:
            await store.close()

    n = asyncio.run(_run())
    print(f"indexed {n} chunks (embed_model_version={embedder.model_version})")  # noqa: T201


@index_app.command("reembed")
def index_reembed(
    yes: Annotated[bool, typer.Option("--yes", help="Skip the confirmation prompt.")] = False,
) -> None:
    """Delete every indexed chunk and re-chunk+re-embed all documents.

    Use this — never `index run` — after changing FI_INTEL_EMBEDDING_MODEL
    or the embedder backend: `index run` is incremental and will never
    revisit an already-chunked document, so a model swap would otherwise
    leave old and new vectors mixed in the same table permanently (see
    PostgresCorpusStore.index_chunks and embed_model_version).
    """
    from fi_intel.retrieval.embedders.openai_compatible_embedder import build_embedder
    from fi_intel.retrieval.store import PostgresCorpusStore

    new_run_id()
    settings = Settings()
    embedder = build_embedder(settings)
    if not yes and not typer.confirm(
        f"This deletes ALL indexed chunks and rebuilds them with "
        f"embed_model_version={embedder.model_version!r}. Continue?"
    ):
        raise typer.Abort()

    async def _run() -> int:
        store = PostgresCorpusStore(settings.postgres_dsn)
        try:
            return await store.index_chunks(embedder, force=True)
        finally:
            await store.close()

    n = asyncio.run(_run())
    print(f"reembedded {n} chunks (embed_model_version={embedder.model_version})")  # noqa: T201


@app.command("search")
def search(
    query: Annotated[str, typer.Argument(help="Search text.")],
    as_of: Annotated[
        str | None, typer.Option(help="ISO date; only docs recorded on/before it.")
    ] = None,
    entity_lei: Annotated[str | None, typer.Option(help="Restrict to one entity.")] = None,
    limit: Annotated[int, typer.Option(min=1)] = 10,
) -> None:
    """Hybrid corpus search with entitlement enforced in SQL and every
    retrieval written to access_log."""
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.retrieval.corpus import CorpusSearch, ScoredChunk
    from fi_intel.retrieval.embedders.openai_compatible_embedder import build_embedder
    from fi_intel.retrieval.service import RetrievalService
    from fi_intel.retrieval.store import PostgresCorpusStore

    run_id = new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC) if as_of else None
    caller = _configured_principal(settings)

    async def _run() -> list[ScoredChunk]:
        store = PostgresCorpusStore(settings.postgres_dsn)
        audit = PostgresAuditLog(settings.postgres_dsn)
        try:
            embedder = build_embedder(settings)
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


db_app = typer.Typer(help="PostgreSQL schema lifecycle.", no_args_is_help=True)
app.add_typer(db_app, name="db")


@db_app.command("migrate")
def db_migrate() -> None:
    """Apply ordered, checksummed PostgreSQL migrations."""
    from fi_intel.db.migrations import PostgresMigrationRunner

    settings = Settings()
    applied = asyncio.run(PostgresMigrationRunner(settings.postgres_dsn).apply())
    if not applied:
        print("postgres schema is current")  # noqa: T201
        return
    for migration in applied:
        print(  # noqa: T201
            f"applied {migration.version_key} {migration.filename} "
            f"sha256={migration.checksum[:12]}"
        )


@db_app.command("status")
def db_status() -> None:
    """Show applied and pending PostgreSQL migrations."""
    from fi_intel.db.migrations import PostgresMigrationRunner

    settings = Settings()
    applied, pending = asyncio.run(
        PostgresMigrationRunner(settings.postgres_dsn).status()
    )
    for applied_migration in applied:
        print(  # noqa: T201
            f"applied\t{applied_migration.version_key}\t{applied_migration.filename}"
        )
    for pending_migration in pending:
        print(  # noqa: T201
            f"pending\t{pending_migration.version_key}\t{pending_migration.filename}"
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
    """Prototype only: extract v1 documents directly into the graph.

    Requires FI_INTEL_LLM_BASE_URL; fails with a clear error otherwise
    rather than silently running a stub (see build_structured_extractor).
    """
    from fi_intel.governance.model_usage import PostgresModelUsageLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.writer import AssertionWriter
    from fi_intel.ingest.extract_pipeline import (
        ExtractionPipeline,
        ExtractionResult,
        PostgresProposedTypeSink,
    )
    from fi_intel.ingest.extractors.openai_compatible_extractor import build_structured_extractor
    from fi_intel.ingest.resolve import EntityResolver
    from fi_intel.ingest.resolve_store import PostgresResolutionStore

    run_id = new_run_id()
    settings = Settings()

    async def _run() -> list[ExtractionResult]:
        doc_store = PostgresDocumentStore(settings.postgres_dsn)
        client = GraphClient(settings.neo4j_uri, settings.neo4j_user, settings.neo4j_password)
        usage_log = PostgresModelUsageLog(settings.postgres_dsn)
        sink = PostgresProposedTypeSink(settings.postgres_dsn)
        resolution_store = PostgresResolutionStore(settings.postgres_dsn)
        try:
            await client.migrate()
            docs = await doc_store.load_documents(source)
            if not docs:
                print(f"no ingested documents for {source!r}")  # noqa: T201
                return []
            extractor = build_structured_extractor(settings, usage_log, run_id)
            pipeline = ExtractionPipeline(
                extractor,
                AssertionWriter(client),
                sink,
                EntityResolver(resolution_store),
                min_confidence=settings.min_extraction_confidence,
            )
            recorded_at = datetime.now(UTC)
            return [await pipeline.extract_document(doc, recorded_at) for doc in docs]
        finally:
            await doc_store.close()
            await client.close()
            await usage_log.close()
            await sink.close()
            await resolution_store.close()

    try:
        results = asyncio.run(_run())
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    for r in results:
        print(  # noqa: T201
            f"{r.doc_id}: written={r.assertions_written} "
            f"low_confidence_dropped={r.low_confidence_dropped} "
            f"offset_rejected={r.offset_rejections} proposed_types={r.proposed_types}"
        )


patterns_app = typer.Typer(help="Deterministic pattern detectors.", no_args_is_help=True)
app.add_typer(patterns_app, name="patterns")


@patterns_app.command("run")
def patterns_run(
    as_of: Annotated[str, typer.Option(help="ISO date; detectors pin to it.")],
    only: Annotated[
        str | None, typer.Option(help="Comma-separated pattern names to enable.")
    ] = None,
) -> None:
    """Run the pattern registry at an as-of date and list fired signals."""
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.coverage import (
        SourceOperationsCoverageProvider,
        source_coverage_policy,
    )
    from fi_intel.graph.registry import PatternRegistry, Signal
    from fi_intel.sources.operations import PostgresSourceOperationsStore

    run_id = new_run_id()
    settings = Settings()
    caller = _configured_principal(settings)
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
    enabled = set(only.split(",")) if only else None

    async def _run() -> list[Signal]:
        audit = PostgresAuditLog(settings.postgres_dsn)
        operations = PostgresSourceOperationsStore(settings.postgres_dsn)
        precision = PostgresPatternPrecisionProvider(
            settings.postgres_dsn,
            minimum_samples=settings.historical_precision_min_feedback,
        )
        access = await _resolve_graph_access(settings, caller, run_id)
        client = GraphClient(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            audit=audit,
        )
        try:
            await client.migrate()
            coverage_sources = frozenset(
                item.strip()
                for item in settings.coverage_required_source_ids.split(",")
                if item.strip()
            )
            covered_entities = frozenset(
                item.strip()
                for item in settings.covered_entity_leis.split(",")
                if item.strip()
            )
            coverage = SourceOperationsCoverageProvider(
                operations,
                required_source_ids=source_coverage_policy(coverage_sources),
                covered_entity_keys=covered_entities,
            )
            return await PatternRegistry(
                client,
                access=access,
                coverage=coverage,
                precision=precision,
            ).run(as_of_dt, enabled=enabled)
        finally:
            await client.close()
            await audit.close()
            await operations.close()
            await precision.close()

    signals = asyncio.run(_run())
    if not signals:
        print("no signals fired")  # noqa: T201
        return
    for s in signals:
        print(  # noqa: T201
            f"[{s.priority:>3}] {s.lifecycle_state.value:<12} "
            f"{s.pattern:<42} {s.entity_name} ({s.entity_key})"
        )


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
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.registry import PatternRegistry, Signal

    run_id = new_run_id()
    settings = Settings()
    caller = _configured_principal(settings)

    async def _explain() -> Signal | None:
        audit = PostgresAuditLog(settings.postgres_dsn)
        access = await _resolve_graph_access(settings, caller, run_id)
        client = GraphClient(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            audit=audit,
        )
        try:
            return await PatternRegistry(client, access=access).explain(signal_id)
        finally:
            await client.close()
            await audit.close()

    signal = asyncio.run(_explain())
    if signal is None:
        print(f"no signal {signal_id!r}")  # noqa: T201
        return
    print(  # noqa: T201
        f"{signal.pattern} v{signal.pattern_version} on {signal.entity_name} "
        f"(state={signal.lifecycle_state.value}, score={signal.opportunity_score:.3f})"
    )
    print(f"  hypothesis: {signal.hypothesis}")  # noqa: T201
    print(f"  assertions: {', '.join(signal.matched_assertion_ids)}")  # noqa: T201
    for contribution in signal.score_contributions:
        print(  # noqa: T201
            f"  score.{contribution.component}: {contribution.weighted_value:.3f} "
            f"(raw={contribution.raw_value:.3f}, weight={contribution.weight:.2f})"
        )
    for k, v in signal.evidence.items():
        print(f"  {k}: {v}")  # noqa: T201


@app.command("research")
def research(
    signal_id: Annotated[str, typer.Option(help="Signal id to research.")],
    as_of: Annotated[str, typer.Option(help="ISO date for the temporal pin.")],
) -> None:
    """Prototype only: research a v1 graph signal into an Opportunity.

    Requires FI_INTEL_LLM_BASE_URL; fails with a clear error otherwise
    rather than silently running a stub (see build_reasoning_model).
    """
    from fi_intel.agents.opportunity_research import OpportunityResearcher
    from fi_intel.agents.reasoning.openai_compatible_reasoning import build_reasoning_model
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.governance.model_usage import PostgresModelUsageLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.coverage import (
        SourceOperationsCoverageProvider,
        source_coverage_policy,
    )
    from fi_intel.graph.registry import PatternRegistry
    from fi_intel.retrieval.corpus import CorpusSearch
    from fi_intel.retrieval.embedders.openai_compatible_embedder import build_embedder
    from fi_intel.retrieval.service import RetrievalService
    from fi_intel.retrieval.store import PostgresCorpusStore
    from fi_intel.sources.operations import PostgresSourceOperationsStore
    from fi_intel.tools.research_tools import ResearchTools, ToolContext

    run_id = new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
    caller = _configured_principal(settings)

    async def _run() -> None:
        store = PostgresCorpusStore(settings.postgres_dsn)
        document_store = PostgresDocumentStore(settings.postgres_dsn)
        audit = PostgresAuditLog(settings.postgres_dsn)
        access = await _resolve_graph_access(settings, caller, run_id)
        client = GraphClient(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            audit=audit,
        )
        usage_log = PostgresModelUsageLog(settings.postgres_dsn)
        operations = PostgresSourceOperationsStore(settings.postgres_dsn)
        try:
            await client.migrate()
            coverage_sources = frozenset(
                item.strip()
                for item in settings.coverage_required_source_ids.split(",")
                if item.strip()
            )
            covered_entities = frozenset(
                item.strip()
                for item in settings.covered_entity_leis.split(",")
                if item.strip()
            )
            registry = PatternRegistry(
                client,
                access=access,
                coverage=SourceOperationsCoverageProvider(
                    operations,
                    required_source_ids=source_coverage_policy(coverage_sources),
                    covered_entity_keys=covered_entities,
                ),
            )
            signal = await registry.explain(signal_id, as_of=as_of_dt)
            if signal is None:
                print(f"no signal {signal_id!r}; run 'fi-intel patterns run' first")  # noqa: T201
                return
            embedder = build_embedder(settings)
            await store.index_chunks(embedder)
            retrieval = RetrievalService(CorpusSearch(store, embedder), audit, run_id)
            ctx = ToolContext(principal=caller, as_of=as_of_dt)
            tools = ResearchTools(retrieval, client, registry, ctx)
            model = build_reasoning_model(settings, usage_log, run_id)
            opportunity, evidence = await OpportunityResearcher(
                tools, model, document_store
            ).research_signal(signal)
            print(f"signal: {signal.pattern} on {signal.entity_name}")  # noqa: T201
            print(f"corpus evidence shown: {len(evidence)}")  # noqa: T201
            if opportunity.insufficient_evidence:
                print("outcome: insufficient evidence")  # noqa: T201
                return
            print(f"title: {opportunity.title}")  # noqa: T201
            print(f"summary: {opportunity.summary}")  # noqa: T201
            print(f"falsifier: {opportunity.falsifier}")  # noqa: T201
            print(f"evidence: {', '.join(opportunity.evidence_ids)}")  # noqa: T201
        finally:
            await store.close()
            await document_store.close()
            await audit.close()
            await client.close()
            await usage_log.close()
            await operations.close()

    try:
        asyncio.run(_run())
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc


@app.command("brief")
def brief(
    as_of: Annotated[str, typer.Option(help="ISO date; the temporal pin.")],
    desk: Annotated[str, typer.Option(help="Desk id, e.g. fi_gcc.")],
    out: Annotated[str, typer.Option(help="Output HTML path.")],
    max_calls: Annotated[int, typer.Option(min=0)] = 5,
    max_tokens: Annotated[int, typer.Option(min=0)] = 100_000,
    max_model_latency_seconds: Annotated[float, typer.Option(min=0.0)] = 600.0,
) -> None:
    """Prototype only: compile a v1 daily brief and write static HTML.

    Requires FI_INTEL_LLM_BASE_URL; fails with a clear error otherwise
    rather than silently compiling a brief full of canned stub text (see
    build_reasoning_model).
    """
    from fi_intel.agents.brief import Brief, BriefCompiler
    from fi_intel.agents.opportunity_research import OpportunityResearcher
    from fi_intel.agents.reasoning.openai_compatible_reasoning import build_reasoning_model
    from fi_intel.agents.render import render_html
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.governance.model_usage import ModelCapacityLimits, PostgresModelUsageLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.coverage import (
        SourceOperationsCoverageProvider,
        source_coverage_policy,
    )
    from fi_intel.graph.registry import PatternRegistry
    from fi_intel.retrieval.corpus import CorpusSearch
    from fi_intel.retrieval.embedders.openai_compatible_embedder import build_embedder
    from fi_intel.retrieval.service import RetrievalService
    from fi_intel.retrieval.store import PostgresCorpusStore
    from fi_intel.sources.operations import PostgresSourceOperationsStore
    from fi_intel.tools.research_tools import ResearchTools, ToolContext

    run_id = new_run_id()
    settings = Settings()
    as_of_dt = datetime.fromisoformat(as_of).replace(tzinfo=UTC)
    caller = _configured_principal(settings)

    async def _run() -> Brief:
        store = PostgresCorpusStore(settings.postgres_dsn)
        document_store = PostgresDocumentStore(settings.postgres_dsn)
        audit = PostgresAuditLog(settings.postgres_dsn)
        precision = PostgresPatternPrecisionProvider(
            settings.postgres_dsn,
            minimum_samples=settings.historical_precision_min_feedback,
        )
        access = await _resolve_graph_access(settings, caller, run_id)
        client = GraphClient(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            audit=audit,
        )
        usage_log = PostgresModelUsageLog(settings.postgres_dsn)
        operations = PostgresSourceOperationsStore(settings.postgres_dsn)
        try:
            await client.migrate()
            coverage_sources = frozenset(
                item.strip()
                for item in settings.coverage_required_source_ids.split(",")
                if item.strip()
            )
            covered_entities = frozenset(
                item.strip()
                for item in settings.covered_entity_leis.split(",")
                if item.strip()
            )
            registry = PatternRegistry(
                client,
                access=access,
                coverage=SourceOperationsCoverageProvider(
                    operations,
                    required_source_ids=source_coverage_policy(coverage_sources),
                    covered_entity_keys=covered_entities,
                ),
                precision=precision,
            )
            embedder = build_embedder(settings)
            await store.index_chunks(embedder)
            retrieval = RetrievalService(CorpusSearch(store, embedder), audit, run_id)
            ctx = ToolContext(principal=caller, as_of=as_of_dt)
            tools = ResearchTools(retrieval, client, registry, ctx)
            model = build_reasoning_model(settings, usage_log, run_id)
            researcher = OpportunityResearcher(tools, model, document_store)
            compiler = BriefCompiler(
                registry,
                researcher,
                capacity_limits=ModelCapacityLimits(
                    max_calls=max_calls,
                    max_total_tokens=max_tokens,
                    max_latency_ms=max_model_latency_seconds * 1000.0,
                ),
                usage_log=usage_log,
                run_id=run_id,
                settings=settings,
            )
            return await compiler.compile(as_of_dt, desk=desk)
        finally:
            await store.close()
            await document_store.close()
            await audit.close()
            await client.close()
            await usage_log.close()
            await operations.close()
            await precision.close()

    try:
        brief_result = asyncio.run(_run())
    except RuntimeError as exc:
        raise typer.BadParameter(str(exc)) from exc
    from pathlib import Path

    Path(out).write_text(render_html(brief_result), encoding="utf-8")
    print(f"brief written to {out}")  # noqa: T201
    print(  # noqa: T201
        f"research usage: calls={brief_result.research_usage.calls} "
        f"tokens={brief_result.research_usage.total_tokens} "
        f"latency_seconds={brief_result.research_usage.latency_ms / 1000.0:.1f} "
        f"metered_cost=${brief_result.research_usage.cost_usd:.2f}"
    )


@app.command("backtest")
def backtest(
    from_date: Annotated[str, typer.Option("--from", help="ISO start date.")],
    to_date: Annotated[str, typer.Option("--to", help="ISO end date.")],
    step: Annotated[str, typer.Option(help="Step, e.g. 7d.")] = "7d",
) -> None:
    """Measure lead time against the outcome ledger."""
    from evals.backtest import Backtester, BacktestResult, Outcome
    from fi_intel.governance.audit import PostgresAuditLog
    from fi_intel.graph.client import GraphClient
    from fi_intel.graph.coverage import (
        SourceOperationsCoverageProvider,
        source_coverage_policy,
    )
    from fi_intel.graph.registry import PatternRegistry
    from fi_intel.sources.operations import PostgresSourceOperationsStore
    from fi_intel.synth.episodes import GULF_MERIDIAN_LEI

    run_id = new_run_id()
    settings = Settings()
    caller = _configured_principal(settings)
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
        audit = PostgresAuditLog(settings.postgres_dsn)
        operations = PostgresSourceOperationsStore(settings.postgres_dsn)
        precision = PostgresPatternPrecisionProvider(
            settings.postgres_dsn,
            minimum_samples=settings.historical_precision_min_feedback,
        )
        access = await _resolve_graph_access(settings, caller, run_id)
        client = GraphClient(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
            audit=audit,
        )
        try:
            await client.migrate()
            coverage_sources = frozenset(
                item.strip()
                for item in settings.coverage_required_source_ids.split(",")
                if item.strip()
            )
            covered_entities = frozenset(
                item.strip()
                for item in settings.covered_entity_leis.split(",")
                if item.strip()
            )
            registry = PatternRegistry(
                client,
                access=access,
                coverage=SourceOperationsCoverageProvider(
                    operations,
                    required_source_ids=source_coverage_policy(coverage_sources),
                    covered_entity_keys=covered_entities,
                ),
                precision=precision,
            )
            return await Backtester(client, registry).run(
                start, end, step_days, outcomes=[mandate]
            )
        finally:
            await client.close()
            await audit.close()
            await operations.close()
            await precision.close()

    result = asyncio.run(_run())
    print(  # noqa: T201
        f"precision@10={result.precision_at_10}  recall={result.recall}  "
        f"signals={result.total_signals}"
    )
    for a in result.attribution:
        leads = a.lead_days
        dist = f"min={leads[0]} median={leads[len(leads) // 2]} max={leads[-1]}" if leads else "n/a"
        print(  # noqa: T201
            f"  {a.pattern:<40} fired={a.fired} preceded={a.preceded_outcome} lead({dist})d"
        )


@app.command("version")
def version() -> None:
    """Print version and active configuration summary."""
    import fi_intel

    settings = Settings()
    print(f"fi-intel {fi_intel.__version__}")  # noqa: T201
    print(f"postgres: {_postgres_target(settings.postgres_dsn)}")  # noqa: T201
    print(f"neo4j:    {settings.neo4j_uri}")  # noqa: T201


if __name__ == "__main__":
    app()
