"""Append-only assertion writer.

``write`` merges by deterministic assertion ID, while ``correct`` appends a
replacement and marks the prior assertion superseded. Typed detector fields
are deterministic projections of immutable assertion properties and are safe
to replay.
"""

import json
from datetime import datetime

from fi_intel.graph.client import GraphClient
from fi_intel.graph.properties import project_typed_properties
from fi_intel.logging import get_logger
from fi_intel.ontology.schema import Assertion


class AssertionWriter:
    def __init__(self, client: GraphClient) -> None:
        self._client = client
        self._log = get_logger(component="graph.writer")

    async def write(self, assertion: Assertion) -> str:
        """Persist an assertion; returns its deterministic id. Idempotent."""
        for ref in (assertion.subject, assertion.object):
            await self._client.upsert_entity(ref)
        assertion_id = assertion.assertion_id()
        typed_properties = project_typed_properties(assertion.properties)
        async with self._client._driver.session() as session:  # noqa: SLF001
            await session.run(
                """
                MATCH (s:Entity {node_type: $s_type, key: $s_key})
                MATCH (o:Entity {node_type: $o_type, key: $o_key})
                MERGE (a:Assertion {assertion_id: $assertion_id})
                ON CREATE SET
                    a.predicate = $predicate,
                    a.source_id = $source_id,
                    a.source_doc_id = $source_doc_id,
                    a.barrier_side = $barrier_side,
                    a.policy_version = $policy_version,
                    a.snippet_start = $snippet_start,
                    a.snippet_end = $snippet_end,
                    a.extractor_version = $extractor_version,
                    a.confidence = $confidence,
                    a.valid_from = datetime($valid_from),
                    a.valid_to = $valid_to,
                    a.recorded_at = datetime($recorded_at),
                    a.properties_json = $properties_json,
                    a.superseded_at = null
                SET a += $typed_properties
                MERGE (a)-[:SUBJECT]->(s)
                MERGE (a)-[:OBJECT]->(o)
                WITH a, s, o
                MATCH (old:Assertion)-[:SUBJECT]->(s)
                MATCH (old)-[:OBJECT]->(o)
                WHERE old.assertion_id <> a.assertion_id
                  AND old.superseded_at IS NULL
                  AND old.predicate = a.predicate
                  AND old.source_id = a.source_id
                  AND old.source_doc_id = a.source_doc_id
                  AND old.policy_version = a.policy_version
                  AND old.snippet_start = a.snippet_start
                  AND old.snippet_end = a.snippet_end
                  AND old.valid_from = a.valid_from
                  AND old.properties_json = a.properties_json
                  AND old.extractor_version <> a.extractor_version
                  AND old.recorded_at <= a.recorded_at
                SET old.superseded_at = a.recorded_at
                MERGE (old)-[:SUPERSEDES]->(a)
                """,
                s_type=str(assertion.subject.node_type),
                s_key=assertion.subject.key,
                o_type=str(assertion.object.node_type),
                o_key=assertion.object.key,
                assertion_id=assertion_id,
                predicate=str(assertion.predicate),
                source_id=assertion.source_id,
                source_doc_id=assertion.source_doc_id,
                barrier_side=str(assertion.barrier_side),
                policy_version=assertion.policy_version,
                snippet_start=assertion.snippet_offset[0],
                snippet_end=assertion.snippet_offset[1],
                extractor_version=assertion.extractor_version,
                confidence=assertion.confidence,
                valid_from=assertion.valid_from.isoformat(),
                valid_to=assertion.valid_to.isoformat() if assertion.valid_to else None,
                recorded_at=assertion.recorded_at.isoformat(),
                # Neo4j properties must be primitives or arrays; the
                # assertion's extra properties travel as a JSON document.
                properties_json=json.dumps(assertion.properties),
                typed_properties=typed_properties,
            )
        self._log.info(
            "graph.assertion_written",
            assertion_id=assertion_id,
            predicate=str(assertion.predicate),
        )
        return assertion_id

    async def correct(self, old: Assertion, new: Assertion, corrected_at: datetime) -> str:
        """Supersede `old` with `new`. Both remain in the graph; the old one
        stays visible to as-of reads before `corrected_at`."""
        new_id = await self.write(new)
        old_id = old.assertion_id()
        async with self._client._driver.session() as session:  # noqa: SLF001
            result = await session.run(
                """
                MATCH (old:Assertion {assertion_id: $old_id})
                MATCH (new:Assertion {assertion_id: $new_id})
                SET old.superseded_at = datetime($corrected_at)
                MERGE (old)-[:SUPERSEDES]->(new)
                RETURN old.assertion_id AS id
                """,
                old_id=old_id,
                new_id=new_id,
                corrected_at=corrected_at.isoformat(),
            )
            if await result.single() is None:
                msg = f"cannot supersede unknown assertion {old_id}"
                raise ValueError(msg)
        self._log.info("graph.assertion_superseded", old_id=old_id, new_id=new_id)
        return new_id
