"""Process-owned resources shared by canonical application adapters."""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import TracebackType
from typing import Protocol

import asyncpg

from fi_intel.application.raw import FileRawArchive
from fi_intel.config import Settings
from fi_intel.graph.client import GraphClient
from fi_intel.telemetry import Telemetry, TelemetryConfig


class PostgresPoolProvider(Protocol):
    async def get_pool(self) -> asyncpg.Pool: ...


class SharedPostgresPool:
    """Lazily create and own one PostgreSQL pool for synchronous factories."""

    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        self._pool: asyncpg.Pool | None = None
        self._lock = asyncio.Lock()

    async def get_pool(self) -> asyncpg.Pool:
        if self._pool is not None:
            return self._pool
        async with self._lock:
            if self._pool is None:
                self._pool = await asyncpg.create_pool(
                    self._settings.postgres_dsn,
                    min_size=self._settings.postgres_pool_min_size,
                    max_size=self._settings.postgres_pool_max_size,
                    command_timeout=self._settings.postgres_command_timeout_seconds,
                )
        return self._pool

    async def close(self) -> None:
        if self._pool is not None:
            await self._pool.close()
            self._pool = None


class RuntimeResources:
    """One PostgreSQL pool, Neo4j driver, and archive per process.

    Adapters receiving ``postgres_pool`` are borrowers and therefore do not
    close it.  The process entry point is the sole lifetime owner.
    """

    def __init__(
        self,
        settings: Settings,
        postgres_pool: asyncpg.Pool,
        graph: GraphClient,
        raw_archive: FileRawArchive,
        telemetry: Telemetry | None = None,
        *,
        graph_required: bool = True,
    ) -> None:
        self.settings = settings
        self.postgres_pool = postgres_pool
        self.graph = graph
        self.raw_archive = raw_archive
        self.telemetry = telemetry or Telemetry(
            TelemetryConfig(service_name="fi-intel-process", environment=settings.analysis_mode)
        )
        self._graph_required = graph_required
        self._closed = False

    @classmethod
    async def open(
        cls,
        settings: Settings,
        *,
        service_name: str = "fi-intel-worker",
        graph_required: bool = True,
    ) -> RuntimeResources:
        pool = await asyncpg.create_pool(
            settings.postgres_dsn,
            min_size=settings.postgres_pool_min_size,
            max_size=settings.postgres_pool_max_size,
            command_timeout=settings.postgres_command_timeout_seconds,
        )
        graph = GraphClient(
            settings.neo4j_uri,
            settings.neo4j_user,
            settings.neo4j_password,
        )
        telemetry = Telemetry(
            TelemetryConfig(
                service_name=service_name,
                environment=settings.analysis_mode,
                trace_endpoint=settings.telemetry_trace_endpoint,
                metric_endpoint=settings.telemetry_metric_endpoint,
            )
        )
        return cls(
            settings,
            pool,
            graph,
            FileRawArchive(Path(settings.raw_archive_path)),
            telemetry,
            graph_required=graph_required,
        )

    async def ready(self) -> None:
        await self.postgres_pool.fetchval("SELECT 1")
        if self._graph_required:
            await self.graph.migrate()

    async def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        errors: list[Exception] = []
        for closer in (self.graph.close, self.postgres_pool.close):
            try:
                await closer()
            except Exception as exc:  # pragma: no cover - shutdown best effort
                errors.append(exc)
        try:
            self.telemetry.shutdown()
        except Exception as exc:  # pragma: no cover - exporter-specific
            errors.append(exc)
        if errors:
            raise ExceptionGroup("runtime resource shutdown failed", errors)

    async def __aenter__(self) -> RuntimeResources:
        try:
            await self.ready()
        except BaseException:
            await self.close()
            raise
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc_type, exc, traceback
        await self.close()


__all__ = ["PostgresPoolProvider", "RuntimeResources", "SharedPostgresPool"]
