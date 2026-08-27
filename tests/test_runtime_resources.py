"""Process readiness only requires infrastructure used by that process."""

from pathlib import Path

from fi_intel.application.raw import FileRawArchive
from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.config import Settings


class _Pool:
    ready = False

    async def fetchval(self, _: str) -> int:
        self.ready = True
        return 1

    async def close(self) -> None:
        pass


class _Graph:
    migrated = False

    async def migrate(self) -> int:
        self.migrated = True
        return 1

    async def close(self) -> None:
        pass


async def test_postgres_only_process_does_not_wait_for_neo4j(tmp_path: Path) -> None:
    pool = _Pool()
    graph = _Graph()
    resources = RuntimeResources(
        Settings(),
        pool,  # type: ignore[arg-type]
        graph,  # type: ignore[arg-type]
        FileRawArchive(tmp_path),
        graph_required=False,
    )

    await resources.ready()

    assert pool.ready
    assert not graph.migrated


async def test_graph_process_migrates_projection_during_readiness(tmp_path: Path) -> None:
    pool = _Pool()
    graph = _Graph()
    resources = RuntimeResources(
        Settings(),
        pool,  # type: ignore[arg-type]
        graph,  # type: ignore[arg-type]
        FileRawArchive(tmp_path),
    )

    await resources.ready()

    assert pool.ready and graph.migrated
