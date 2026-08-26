"""Graph client: connection, versioned migrations, as-of pinned reads.

Read sessions pin `recorded_at <= as_of` at the *query* level (invariant
10). A session constructed with an as_of cannot see the future regardless
of what the caller asks for — the pin is applied inside the read methods,
not left to the caller's discipline.
"""

from datetime import UTC, datetime
from typing import Any

from neo4j import AsyncDriver, AsyncGraphDatabase

from fi_intel.governance.audit import AccessEvent, AuditLog
from fi_intel.governance.policy import GraphAccessContext
from fi_intel.logging import get_logger
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType

SCHEMA_VERSION = 3

# Versioned migrations. Applied in order, each exactly once, recorded in a
# SchemaVersion node. Never edit an applied migration; add a new one.
MIGRATIONS: dict[int, list[str]] = {
    1: [
        "CREATE CONSTRAINT entity_key_unique IF NOT EXISTS "
        "FOR (e:Entity) REQUIRE (e.node_type, e.key) IS UNIQUE",
        "CREATE CONSTRAINT assertion_id_unique IF NOT EXISTS "
        "FOR (a:Assertion) REQUIRE a.assertion_id IS UNIQUE",
        "CREATE INDEX assertion_recorded_at IF NOT EXISTS FOR (a:Assertion) ON (a.recorded_at)",
        "CREATE INDEX assertion_predicate IF NOT EXISTS FOR (a:Assertion) ON (a.predicate)",
    ],
    2: [
        "CREATE INDEX assertion_source_id IF NOT EXISTS FOR (a:Assertion) ON (a.source_id)",
        "CREATE INDEX assertion_barrier_side IF NOT EXISTS FOR (a:Assertion) ON (a.barrier_side)",
        "CREATE INDEX signal_barrier_side IF NOT EXISTS FOR (s:Signal) ON (s.barrier_side)",
    ],
    3: [
        "CREATE CONSTRAINT signal_id_unique IF NOT EXISTS "
        "FOR (s:Signal) REQUIRE s.signal_id IS UNIQUE",
        "CREATE CONSTRAINT signal_observation_id_unique IF NOT EXISTS "
        "FOR (o:SignalObservation) REQUIRE o.observation_id IS UNIQUE",
        "CREATE INDEX signal_lifecycle_state IF NOT EXISTS FOR (s:Signal) ON (s.lifecycle_state)",
        "CREATE INDEX signal_authorization_scope IF NOT EXISTS "
        "FOR (s:Signal) ON (s.authorization_scope)",
        "CREATE INDEX signal_pattern_version IF NOT EXISTS "
        "FOR (s:Signal) ON (s.pattern, s.pattern_version)",
    ],
}


class GraphClient:
    def __init__(
        self,
        uri: str,
        user: str,
        password: str,
        *,
        audit: AuditLog | None = None,
    ) -> None:
        self._driver: AsyncDriver = AsyncGraphDatabase.driver(uri, auth=(user, password))
        self._audit = audit
        self._log = get_logger(component="graph.client")

    async def close(self) -> None:
        await self._driver.close()

    async def migrate(self) -> int:
        """Apply pending migrations in order. Returns the schema version."""
        async with self._driver.session() as session:
            result = await session.run("MATCH (v:SchemaVersion) RETURN max(v.version) AS v")
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
        access: GraphAccessContext,
        subject_key: str | None = None,
        predicate: EdgeType | None = None,
        endpoint_key: str | None = None,
    ) -> list[dict[str, Any]]:
        """Assertions visible at as_of: recorded on/before it and not yet
        superseded at it. The pin is here, in Cypher, not in Python."""
        query = """
            MATCH (a:Assertion)-[:SUBJECT]->(s:Entity), (a)-[:OBJECT]->(o:Entity)
            WHERE a.recorded_at <= datetime($as_of)
              AND (a.superseded_at IS NULL OR a.superseded_at > datetime($as_of))
              AND a.source_id IN $allowed_source_ids
              AND (a.barrier_side = 'public' OR $side = 'private')
        """
        params: dict[str, Any] = {
            "as_of": as_of.isoformat(),
            "allowed_source_ids": sorted(access.allowed_source_ids),
            "side": str(access.principal.side),
        }
        if subject_key is not None:
            query += " AND s.key = $subject_key"
            params["subject_key"] = subject_key
        if predicate is not None:
            query += " AND a.predicate = $predicate"
            params["predicate"] = str(predicate)
        if endpoint_key is not None:
            query += " AND (s.key = $endpoint_key OR o.key = $endpoint_key)"
            params["endpoint_key"] = endpoint_key
        query += " RETURN a, s, o ORDER BY a.recorded_at"
        async with self._driver.session() as session:
            result = await session.run(query, params)
            rows = [record.data() async for record in result]
        await self.audit_access(
            access,
            [(row["a"]["source_id"], row["a"]["source_doc_id"]) for row in rows],
        )
        return rows

    async def read_all_assertions_including_superseded(
        self, as_of: datetime, access: GraphAccessContext
    ) -> list[dict[str, Any]]:
        """Everything recorded on/before as_of, including assertions already
        superseded by then. Used to verify corrections preserve history."""
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (a:Assertion)-[:SUBJECT]->(s:Entity), (a)-[:OBJECT]->(o:Entity)
                WHERE a.recorded_at <= datetime($as_of)
                  AND a.source_id IN $allowed_source_ids
                  AND (a.barrier_side = 'public' OR $side = 'private')
                RETURN a, s, o ORDER BY a.recorded_at
                """,
                {
                    "as_of": as_of.isoformat(),
                    "allowed_source_ids": sorted(access.allowed_source_ids),
                    "side": str(access.principal.side),
                },
            )
            rows = [record.data() async for record in result]
        await self.audit_access(
            access,
            [(row["a"]["source_id"], row["a"]["source_doc_id"]) for row in rows],
        )
        return rows

    async def audit_access(
        self,
        access: GraphAccessContext,
        refs: list[tuple[str, str]],
    ) -> None:
        """Fail closed for production contexts before protected data returns."""
        if self._audit is None:
            if access.require_audit:
                msg = "protected graph read requires an audit log"
                raise RuntimeError(msg)
            return
        seen: set[tuple[str, str]] = set()
        events: list[AccessEvent] = []
        accessed_at = datetime.now(tz=UTC)
        if not refs:
            events.append(
                AccessEvent(
                    run_id=access.run_id,
                    principal=access.principal.principal_id,
                    entitlement_group=access.principal.entitlement_group,
                    operation="graph_read",
                    result_count=0,
                    accessed_at=accessed_at,
                )
            )
        for source_id, doc_id in refs:
            ref = (source_id, doc_id)
            if ref in seen:
                continue
            seen.add(ref)
            events.append(
                AccessEvent(
                    run_id=access.run_id,
                    principal=access.principal.principal_id,
                    entitlement_group=access.principal.entitlement_group,
                    source_id=source_id,
                    doc_id=doc_id,
                    operation="graph_read",
                    result_count=len(refs),
                    accessed_at=accessed_at,
                )
            )
        await self._audit.record(events)

    async def read_resolved_signal_precedents(
        self,
        query: str,
        *,
        as_of: datetime,
        access: GraphAccessContext,
        limit: int = 5,
    ) -> list[dict[str, Any]]:
        """Search resolved signal episodes, never the raw document corpus."""
        terms = sorted(
            {
                token.lower()
                for token in query.split()
                if len(token) >= 3
            }
        )
        if not terms:
            raise ValueError("precedent query must contain a material search term")
        if not 1 <= limit <= 50:
            raise ValueError("precedent limit must be in [1, 50]")
        async with self._driver.session() as session:
            result = await session.run(
                """
                MATCH (s:Signal)
                WHERE s.lifecycle_state = 'resolved'
                  AND s.resolved_at <= datetime($as_of)
                  AND size(coalesce(s.source_ids, [])) > 0
                  AND all(source_id IN s.source_ids
                          WHERE source_id IN $allowed_source_ids)
                  AND (s.barrier_side = 'public' OR $side = 'private')
                  AND any(term IN $terms WHERE
                      toLower(coalesce(s.entity_name, '') + ' ' +
                              coalesce(s.pattern, '') + ' ' +
                              coalesce(s.evidence_json, '')) CONTAINS term)
                OPTIONAL MATCH (s)-[:SUPPORTED_BY]->(a:Assertion)
                WHERE a.source_id IN $allowed_source_ids
                  AND (a.barrier_side = 'public' OR $side = 'private')
                WITH s, collect(CASE WHEN a IS NULL THEN null ELSE {
                    source_id: a.source_id,
                    doc_id: a.source_doc_id,
                    snippet_start: a.snippet_start,
                    snippet_end: a.snippet_end
                } END) AS raw_refs
                RETURN s, [ref IN raw_refs WHERE ref IS NOT NULL] AS refs
                ORDER BY s.resolved_at DESC, s.signal_id
                LIMIT $limit
                """,
                as_of=as_of.isoformat(),
                allowed_source_ids=sorted(access.allowed_source_ids),
                side=access.principal.side.value,
                terms=terms,
                limit=limit,
            )
            rows = [record.data() async for record in result]
        refs = [
            (str(ref["source_id"]), str(ref["doc_id"]))
            for row in rows
            for ref in row["refs"]
        ]
        await self.audit_access(access, refs)
        return rows

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
