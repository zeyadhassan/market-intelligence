"""Command-line entry point.

This is the only module where print() is permitted.
"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated
from urllib.parse import urlsplit
from uuid import uuid4

import typer
from pydantic import BaseModel

from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.config import Settings
from fi_intel.logging import configure_logging

app = typer.Typer(name="fi-intel", help="FI market intelligence platform.", no_args_is_help=True)


def _postgres_target(dsn: str) -> str:
    """Return a diagnostic target without credentials or query parameters."""
    parsed = urlsplit(dsn)
    host = parsed.hostname or "configured-host"
    port = f":{parsed.port}" if parsed.port is not None else ""
    database = parsed.path.lstrip("/") or "configured-database"
    return f"{host}{port}/{database}"


@app.callback()
def _main(log_level: Annotated[str, typer.Option(envvar="FI_INTEL_LOG_LEVEL")] = "INFO") -> None:
    configure_logging(log_level)


worker_app = typer.Typer(
    help="Run one independently deployable durable worker.", no_args_is_help=True
)
app.add_typer(worker_app, name="worker")

scheduler_app = typer.Typer(help="Schedule durable daily analysis jobs.", no_args_is_help=True)
app.add_typer(scheduler_app, name="scheduler")

operator_app = typer.Typer(
    help="Inspect queues and perform explicit recovery.", no_args_is_help=True
)
app.add_typer(operator_app, name="operator")

notification_app = typer.Typer(
    help="Manage development notification preferences.", no_args_is_help=True
)
app.add_typer(notification_app, name="notification")


async def _with_resources(
    operation: Callable[[RuntimeResources], Awaitable[object]],
    *,
    graph_required: bool = True,
) -> object:
    """Open exactly one shared resource bundle for a bounded command."""

    async with await RuntimeResources.open(Settings(), graph_required=graph_required) as resources:
        return await operation(resources)


def _show(value: object) -> None:
    if isinstance(value, BaseModel):
        print(value.model_dump_json(indent=2))
    else:
        print(value)


@worker_app.command("source")
def worker_source(
    once: Annotated[bool, typer.Option("--once", help="Poll once and exit.")] = False,
    force: Annotated[bool, typer.Option(help="Ignore source cadence for this poll.")] = False,
) -> None:
    """Acquire registered sources into the raw archive and PostgreSQL ledger."""

    from fi_intel.application.workers import CanonicalSourceWorker, run_continuously

    async def run(resources: RuntimeResources) -> object:
        worker = CanonicalSourceWorker(resources)
        if once:
            return await worker.run_once(force=force)

        async def poll() -> object:
            return await worker.run_once(force=force)

        await run_continuously(
            poll,
            interval_seconds=resources.settings.worker_poll_interval_seconds,
        )
        return "stopped"

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@worker_app.command("projection")
def worker_projection(
    once: Annotated[bool, typer.Option("--once", help="Dispatch once and exit.")] = False,
) -> None:
    """Project committed document, assertion, and signal events."""

    from fi_intel.application.workers import CanonicalProjectionWorker, run_continuously

    async def run(resources: RuntimeResources) -> object:
        worker = CanonicalProjectionWorker(resources, worker_id=f"projection-{uuid4()}")
        if once:
            return await worker.run_once()
        await run_continuously(
            worker.run_once,
            interval_seconds=resources.settings.worker_poll_interval_seconds,
        )
        return "stopped"

    _show(asyncio.run(_with_resources(run)))


@worker_app.command("analysis")
def worker_analysis(
    once: Annotated[bool, typer.Option("--once", help="Claim once and exit.")] = False,
) -> None:
    """Analyze only frozen, already-processed daily inputs."""

    from fi_intel.application.daily_worker import CanonicalAnalysisJobWorker
    from fi_intel.application.workers import run_continuously

    async def run(resources: RuntimeResources) -> object:
        worker = CanonicalAnalysisJobWorker(resources, worker_id=f"analysis-{uuid4()}")
        if once:
            return await worker.run_once() or "idle"
        await run_continuously(
            worker.run_once,
            interval_seconds=resources.settings.worker_poll_interval_seconds,
        )
        return "stopped"

    _show(asyncio.run(_with_resources(run)))


@worker_app.command("search")
def worker_search(
    once: Annotated[bool, typer.Option("--once", help="Claim once and exit.")] = False,
) -> None:
    """Execute typed, bounded, asynchronous interactive searches."""

    from fi_intel.application.search import CanonicalSearchWorker
    from fi_intel.application.workers import run_continuously

    async def run(resources: RuntimeResources) -> object:
        worker = CanonicalSearchWorker(resources, worker_id=f"search-{uuid4()}")
        if once:
            return await worker.run_once() or "idle"
        await run_continuously(
            worker.run_once,
            interval_seconds=resources.settings.worker_poll_interval_seconds,
        )
        return "stopped"

    _show(asyncio.run(_with_resources(run)))


@worker_app.command("delivery")
def worker_delivery(
    once: Annotated[bool, typer.Option("--once", help="Assemble and deliver once.")] = False,
) -> None:
    """Assemble immutable digests and deliver through sandbox SMTP."""

    from fi_intel.application.delivery import PostgresNotificationService
    from fi_intel.application.workers import run_continuously

    async def run(resources: RuntimeResources) -> object:
        service = PostgresNotificationService(
            resources.settings,
            pool=resources.postgres_pool,
        )

        async def deliver() -> object:
            assembly = await service.assemble_due()
            delivery = await service.deliver_once(worker_id=f"delivery-{uuid4()}")
            for state in (
                "accepted",
                "suppressed",
                "retryable_failed",
                "permanent_failed",
                "acceptance_unknown",
            ):
                resources.telemetry.record_delivery_transition(state, int(getattr(delivery, state)))
            return {"assembly": assembly.model_dump(), "delivery": delivery.model_dump()}

        if once:
            return await deliver()
        await run_continuously(
            deliver,
            interval_seconds=resources.settings.worker_poll_interval_seconds,
        )
        return "stopped"

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@scheduler_app.command("run")
def scheduler_run(
    once: Annotated[bool, typer.Option("--once", help="Schedule once and exit.")] = False,
) -> None:
    """Coalesce active subscriptions into deterministic daily jobs."""

    from fi_intel.application.scheduler import CanonicalScheduler
    from fi_intel.application.workers import run_continuously

    async def run(resources: RuntimeResources) -> object:
        scheduler = CanonicalScheduler(resources)
        if once:
            return await scheduler.run_once()
        await run_continuously(
            scheduler.run_once,
            interval_seconds=resources.settings.worker_poll_interval_seconds,
        )
        return "stopped"

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@operator_app.command("status")
def operator_status() -> None:
    """Show durable queue, dead-letter, and delivery state."""

    from fi_intel.application.operations import OperatorService

    async def run(resources: RuntimeResources) -> object:
        return await OperatorService(resources).queue_status()

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@operator_app.command("sync-access")
def operator_sync_access(
    confirm: Annotated[
        str,
        typer.Option(help="Type ACCESS to apply the configured OIDC access assignment."),
    ] = "",
) -> None:
    """Create or reactivate the FI_INTEL_ACCESS_* principal assignment."""

    if confirm != "ACCESS":
        raise typer.BadParameter("--confirm ACCESS is required")
    from fi_intel.application.operations import OperatorService

    async def run(resources: RuntimeResources) -> object:
        return await OperatorService(resources).synchronize_configured_principal()

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@operator_app.command("dead-letters")
def operator_dead_letters(
    limit: Annotated[int, typer.Option(min=1, max=1000)] = 100,
) -> None:
    """List quarantined outbox events without exposing payloads."""

    from fi_intel.application.operations import OperatorService

    async def run(resources: RuntimeResources) -> object:
        return [
            item.model_dump(mode="json")
            for item in await OperatorService(resources).dead_letters(limit=limit)
        ]

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@operator_app.command("replay-outbox")
def operator_replay_outbox(
    dead_letter_id: Annotated[str, typer.Argument(help="Immutable dead-letter ID.")],
) -> None:
    """Append a correlated replay event for one dead letter."""

    from fi_intel.application.operations import OperatorService

    async def run(resources: RuntimeResources) -> object:
        return await OperatorService(resources).replay_dead_letter(dead_letter_id)

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@operator_app.command("rebuild-graph")
def operator_rebuild_graph(
    confirm: Annotated[
        str,
        typer.Option(help="Type REBUILD to clear and reconstruct the disposable projection."),
    ] = "",
) -> None:
    """Rebuild Neo4j from authoritative PostgreSQL projection events."""

    if confirm != "REBUILD":
        raise typer.BadParameter("--confirm REBUILD is required")
    from fi_intel.application.projection_rebuild import GraphProjectionRebuilder

    async def run(resources: RuntimeResources) -> object:
        report = await GraphProjectionRebuilder(resources).rebuild()
        if not report.equivalent:
            raise RuntimeError("rebuilt projection does not match PostgreSQL authority")
        return report

    _show(asyncio.run(_with_resources(run)))


@operator_app.command("replay-document")
def operator_replay_document(
    document_version_id: Annotated[str, typer.Argument(help="Immutable document-version UUID.")],
) -> None:
    """Requeue one archived document version without a source refetch."""

    from uuid import UUID

    from fi_intel.application.operations import OperatorService

    try:
        parsed = UUID(document_version_id)
    except ValueError as exc:
        raise typer.BadParameter("document version must be a UUID") from exc

    async def run(resources: RuntimeResources) -> object:
        return await OperatorService(resources).replay_document_version(parsed)

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@notification_app.command("set-email")
def notification_set_email(
    destination: Annotated[str, typer.Argument(help="Allowlisted sandbox email address.")],
    topics: Annotated[str, typer.Option(help="Comma-separated governed topic IDs.")],
    timezone_name: Annotated[str, typer.Option("--timezone")] = "Europe/Berlin",
    send_time: Annotated[str, typer.Option(help="Local HH:MM send time.")] = "07:00",
    frequency: Annotated[str, typer.Option(help="daily, weekdays, or paused.")] = "weekdays",
    include_nothing_new: Annotated[bool, typer.Option()] = False,
    link_only: Annotated[bool, typer.Option()] = False,
    unsubscribe: Annotated[bool, typer.Option()] = False,
) -> None:
    """Append an encrypted development email preference transition."""

    from datetime import time

    from fi_intel.application.delivery import PostgresNotificationService

    topic_ids = tuple(sorted({item.strip() for item in topics.split(",") if item.strip()}))
    try:
        parsed_time = time.fromisoformat(send_time)
    except ValueError as exc:
        raise typer.BadParameter("send time must be HH:MM") from exc

    async def run(resources: RuntimeResources) -> object:
        service = PostgresNotificationService(
            resources.settings,
            pool=resources.postgres_pool,
        )
        return await service.set_preference(
            principal_id=resources.settings.access_principal_id,
            destination=destination,
            timezone_name=timezone_name,
            local_send_time=parsed_time,
            frequency=frequency,
            topic_ids=topic_ids,
            include_nothing_new=include_nothing_new,
            link_only=link_only,
            unsubscribed=unsubscribe,
        )

    _show(asyncio.run(_with_resources(run, graph_required=False)))


@app.command("serve")
def serve(
    port: Annotated[int, typer.Option(min=1, max=65535, help="Local HTTP port.")] = 8765,
    host: Annotated[
        str,
        typer.Option(help="Bind address; use 0.0.0.0 only inside the Podman network."),
    ] = "127.0.0.1",
) -> None:
    """Serve the canonical governed GCC analyst application on localhost."""
    import uvicorn

    from fi_intel.application.preflight import canonical_configuration_errors
    from fi_intel.sources.adapters.gcc_official import GCC_OFFICIAL_SOURCES

    errors = canonical_configuration_errors(Settings())
    if errors:
        print("Canonical analysis service cannot start:")  # noqa: T201
        for error in errors:
            print(f"- {error}")  # noqa: T201
        print("Set the missing FI_INTEL_* values; fixtures are never substituted.")  # noqa: T201
        raise typer.Exit(code=2)
    print(  # noqa: T201
        f"Canonical governed analysis: configured sources across "
        f"{len({source.country for source in GCC_OFFICIAL_SOURCES})} GCC countries."
    )
    print(f"Open http://127.0.0.1:{port}/ in a browser. Press Ctrl+C to stop.")  # noqa: T201
    uvicorn.run(
        "fi_intel.api.app:create_production_app",
        factory=True,
        host=host,
        port=port,
        reload=False,
    )


sources_app = typer.Typer(help="Inspect canonical source registration.", no_args_is_help=True)
app.add_typer(sources_app, name="sources")


@sources_app.command("list")
def sources_list() -> None:
    """List the exact official GCC sources used by the canonical source worker."""

    from fi_intel.sources.adapters.gcc_official import GCC_OFFICIAL_SOURCES

    for source in GCC_OFFICIAL_SOURCES:
        print(  # noqa: T201
            f"{source.source_id}\t{source.country}\t{source.source_type}\t{source.display_name}"
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
            f"applied {migration.version_key} {migration.filename} sha256={migration.checksum[:12]}"
        )


@db_app.command("status")
def db_status() -> None:
    """Show applied and pending PostgreSQL migrations."""
    from fi_intel.db.migrations import PostgresMigrationRunner

    settings = Settings()
    applied, pending = asyncio.run(PostgresMigrationRunner(settings.postgres_dsn).status())
    for applied_migration in applied:
        print(  # noqa: T201
            f"applied\t{applied_migration.version_key}\t{applied_migration.filename}"
        )
    for pending_migration in pending:
        print(  # noqa: T201
            f"pending\t{pending_migration.version_key}\t{pending_migration.filename}"
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
