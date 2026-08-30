"""Service-free proof that graph rebuilds start from PostgreSQL authority."""

from types import SimpleNamespace

from fi_intel.application import projection_rebuild
from fi_intel.application.projection_rebuild import GraphProjectionRebuilder


class _Pool:
    async def fetch(self, query: str):  # noqa: ANN202
        if "FROM transactional_outbox" in query:
            return []
        if "FROM entity_identity" in query:
            return [
                {
                    "entity_id": "entity-1",
                    "node_type": "Organization",
                    "node_key": "549300EXAMPLE000001",
                    "display_name": "Example Bank",
                }
            ]
        raise AssertionError(f"unexpected rebuild query: {query}")


class _Graph:
    cleared = False
    migrated = False
    entities = []

    async def clear_projection(self) -> None:
        self.cleared = True
        self.entities = []

    async def migrate(self) -> None:
        self.migrated = True

    async def upsert_entity(self, entity) -> None:  # noqa: ANN001
        self.entities.append(entity)

    async def entity_count(self) -> int:
        return len(self.entities)

    async def assertion_count(self) -> int:
        return 0

    async def signal_count(self) -> int:
        return 0


async def test_rebuild_clears_projection_and_verifies_postgres_equivalence(monkeypatch) -> None:  # noqa: ANN001
    synchronized = False

    class EntityProjection:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def synchronize(self, *_: object) -> None:
            nonlocal synchronized
            synchronized = True

    class Resolver:
        def __init__(self, *_: object, **__: object) -> None:
            pass

        async def resolve(self, *_: object):  # noqa: ANN202
            return SimpleNamespace()

    class Registry:
        def __init__(self, *_: object, **__: object) -> None:
            pass

    monkeypatch.setattr(projection_rebuild, "EntityReferenceProjection", EntityProjection)
    monkeypatch.setattr(projection_rebuild, "PostgresEntitlementResolver", Resolver)
    monkeypatch.setattr(projection_rebuild, "PatternRegistry", Registry)
    graph = _Graph()
    resources = SimpleNamespace(
        settings=SimpleNamespace(
            postgres_dsn="unused",
            access_entitlement_group="fi_gcc_public",
            access_side="public",
        ),
        postgres_pool=_Pool(),
        graph=graph,
    )

    report = await GraphProjectionRebuilder(resources).rebuild()  # type: ignore[arg-type]

    assert graph.cleared and graph.migrated and synchronized
    assert report.entities_projected == 1
    assert report.graph_entity_count == 1
    assert report.assertions_projected == report.graph_assertion_count == 0
    assert report.signals_projected == report.graph_signal_count == 0
    assert report.equivalent
