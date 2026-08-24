"""Graph client: connection, versioned migrations, as-of pinned reads.

Read sessions pin `recorded_at <= as_of` at the *query* level (invariant
10). A session constructed with an as_of cannot see the future regardless
of what the caller asks for — the pin is applied inside the read methods,
not left to the caller's discipline.
"""

from datetime import datetime
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from fi_intel.logging import get_logger
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType

SCHEMA_VERSION = 1

# Versioned migrations. Applied in order, each exactly once, recorded in a
# SchemaVersion node. Never edit an applied migration; add a new one.
MIGRATIONS: dict[int, list[str]] = {
    1: [
        "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE (e.node_type, e.key) IS UNIQUE",
        "CREATE CONSTRAINT assertion_id_unique IF NOT EXISTS "
        "FOR (a:Assertion) REQUIRE a.assertion_id IS UNIQUE",
        "CREATE INDEX assertion_recorded_at IF NOT EXISTS "
        "FOR (a:Assertion) ON (a.recorded_at)",
        "CREATE INDEX assertion_predicate IF NOT EXISTS "
        "FOR (a:Assertion) ON (a.predicate)",
    ],
}


class GraphClient:
    def __init__(self, uri: str, user: str, password: str) -> None:
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._log = get_logger(component="graph.client")

    async def close(self) -> None:
        await self._driver.close()

    async def migrate(self) -> int:
        """Apply pending migrations in order. Returns the schema version."""
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (v:SchemaVersion) RETURN max(v.version) AS v"
            )
            row = await result.single()
            current: int = row["v"] if row and row["v"] is not None else 0
            for version in sorted(MIGRATIONS):
                if version <= current:
                    continue
                for statement in MIGRATIONS[version]:
                    await session.run(statement)
                await session.run("CREATE (:SchemaVersion {version: $v})", v=version)
                self._log.info("graph.migrated", version=version)
            return max(MIGRATIONS)

    async def upsert_entity(self, ref: EntityRef) -> None:
        async with self._driver.session() as session:
            await session.run(
                """
                MERGE (e:Entity {node_type: $node_type, key: $key})
                ON CREATE SET e.display_name = $display_name
                """,
                node_type=str(ref.node_type),
                key=ref.key,
                display_name=ref.display_name,
            )

    async def read_assertions(
        self,
        as_of: datetime,
        subject_key: str | None = None,
        predicate: EdgeType | None = None,
    ) -> list[dict[str, Any]]:
        """Assertions visible at as_of: recorded on/before it and not yet
        superseded at it. The pin is here, in Cypher, not in Python."""
        query = """
            MATCH (a:Assertion)-[:SUBJECT]->(s:Entity), (a)-[:OBJECT]->(o:Entity)
            WHERE a.recorded_at <= datetime($as_of)
              AND (a.superseded_at IS NULL OR a.superseded_at > datetime($as_of))
        """
        params: dict[str, Any] = {"as_of": as_of.isoformat()}
        if subject_key is not None:
            query += " AND s.key = $subject_key"
            params["subject_key"] = subject_key
        if predicate is not None:
            query += " AND a.predicate = $predicate"
            params["predicate"] = str(predicate)
        query += " RETURN a, s, o ORDER BY a.recorded_at"
        async with self._driver.session() as session:
            result = await session.run(query, params)
            return [record.data() async for record in result]

    async def read_all_assertions_including_superseded(
        self, as_of: datetime
    ) -> list[dict[str, Any]]:
        """Everything recorded on/before as_of, including assertions already
        superseded by then. Used to verify corrections preserve history."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Assertion)-[:SUBJECT]->(s:Entity), (a)-[:OBJECT]->(o:Entity)
                WHERE a.recorded_at <= datetime($as_of)
                RETURN a, s, o ORDER BY a.recorded_at
                """,
                {"as_of": as_of.isoformat()},
            )
            return [record.data() async for record in result]

    async def assertion_count(self) -> int:
        async with self._driver.session() as session:
            result = await session.run("MATCH (a:Assertion) RETURN count(a) AS n")
            row = await result.single()
            return int(row["n"]) if row is not None else 0

    async def delete_all(self) -> None:
        """Test-only teardown. Never called by pipeline code."""
        async with self._driver.session() as session:
            await session.run("MATCH (n) DETACH DELETE n")


__all__ = [
    "Assertion",
    "EdgeType",
    "EntityRef",
    "GraphClient",
    "NodeType",
    "SCHEMA_VERSION",
]
