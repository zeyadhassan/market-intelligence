"""Rebuild the disposable Neo4j projection entirely from PostgreSQL."""

from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict

from fi_intel.application.entity_projection import EntityReferenceProjection
from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.governance.policy import PostgresEntitlementResolver
from fi_intel.graph.registry import PatternRegistry
from fi_intel.graph.signals import Signal
from fi_intel.graph.writer import AssertionWriter
from fi_intel.ledger.models import AccessPolicy
from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import NodeType
from fi_intel.retrieval.entitlement import Principal, Side


class ProjectionRebuildReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    entities_projected: int
    assertions_projected: int
    signals_projected: int
    graph_entity_count: int
    graph_assertion_count: int
    graph_signal_count: int
    equivalent: bool


class GraphProjectionRebuilder:
    def __init__(self, resources: RuntimeResources) -> None:
        self._resources = resources

    async def rebuild(self) -> ProjectionRebuildReport:
        settings = self._resources.settings
        pool = self._resources.postgres_pool
        rows = await pool.fetch(
            """
            SELECT event_type, payload, aggregate_version, occurred_at, event_id
            FROM transactional_outbox
            WHERE event_type IN ('assertion.accepted.v1','signal.transitioned.v1')
            ORDER BY occurred_at, aggregate_version, event_id
            """
        )
        assertions: dict[str, Assertion] = {}
        signals: dict[str, tuple[Signal, float]] = {}
        for row in rows:
            payload = _json(row["payload"])
            if row["event_type"] == "assertion.accepted.v1":
                projection = payload.get("projection")
                if not isinstance(projection, dict):
                    raise ValueError("authoritative assertion event lacks projection payload")
                assertion = Assertion.model_validate(projection)
                assertions[assertion.assertion_id()] = assertion
            elif payload.get("ledger_status") != "candidate":
                signal_payload = payload.get("signal")
                anchor = payload.get("score_anchor")
                if not isinstance(signal_payload, dict) or not isinstance(anchor, int | float):
                    raise ValueError("authoritative signal event lacks projection payload")
                signal = Signal.model_validate(signal_payload)
                signals[signal.signal_id] = (signal, float(anchor))
        graph = self._resources.graph
        await graph.clear_projection()
        await graph.migrate()
        await EntityReferenceProjection(settings.postgres_dsn, pool=pool).synchronize(
            _public_reference_policy()
        )
        entity_rows = await pool.fetch(
            """
            SELECT identity.entity_id,
                   CASE identity.entity_type
                     WHEN 'organization' THEN 'Organization'
                     WHEN 'Organization' THEN 'Organization'
                     WHEN 'instrument' THEN 'Instrument'
                     WHEN 'Instrument' THEN 'Instrument'
                   END AS node_type,
                   identifier.normalized_value AS node_key,
                   identity.canonical_name AS display_name
            FROM entity_identity identity
            LEFT JOIN LATERAL (
              SELECT normalized_value FROM entity_identifier_v2 identifier
              WHERE identifier.entity_id=identity.entity_id
              ORDER BY CASE identifier.scheme
                         WHEN 'lei' THEN 1 WHEN 'isin' THEN 2 ELSE 3 END,
                       identifier.recorded_at DESC LIMIT 1
            ) identifier ON TRUE
            WHERE identifier.normalized_value IS NOT NULL
            ORDER BY identity.entity_id
            """
        )
        for row in entity_rows:
            if row["node_type"] is None:
                raise ValueError("authoritative entity has no graph node-type mapping")
            await graph.upsert_entity(
                EntityRef(
                    node_type=NodeType(str(row["node_type"])),
                    key=str(row["node_key"]),
                    display_name=str(row["display_name"]),
                )
            )
        entities = len(entity_rows)
        writer = AssertionWriter(graph)
        for assertion in assertions.values():
            await writer.write(assertion)
        resolver = PostgresEntitlementResolver(settings.postgres_dsn, pool=pool)
        access = await resolver.resolve(
            Principal(
                principal_id="graph-rebuild",
                entitlement_group=settings.access_entitlement_group,
                side=Side(settings.access_side),
            ),
            "graph-rebuild",
        )
        registry = PatternRegistry(graph, access=access)
        for signal, anchor in signals.values():
            await registry.project_signal(signal, anchor)
        graph_entities = await graph.entity_count()
        graph_assertions = await graph.assertion_count()
        graph_signals = await graph.signal_count()
        return ProjectionRebuildReport(
            entities_projected=entities,
            assertions_projected=len(assertions),
            signals_projected=len(signals),
            graph_entity_count=graph_entities,
            graph_assertion_count=graph_assertions,
            graph_signal_count=graph_signals,
            equivalent=(
                graph_assertions == len(assertions)
                and graph_signals == len(signals)
                and graph_entities >= entities
            ),
        )


def _json(value: object) -> dict[str, object]:
    decoded = json.loads(value) if isinstance(value, str) else value
    if not isinstance(decoded, dict):
        raise TypeError("outbox projection payload must be a JSON object")
    return {str(key): item for key, item in decoded.items()}


def _public_reference_policy() -> AccessPolicy:
    # The entity projection retains each row's authoritative policy ID; this
    # supplied policy is used only for any legacy GLEIF reference rows.
    from fi_intel.application.policies import reference_source_policy

    return reference_source_policy()


__all__ = ["GraphProjectionRebuilder", "ProjectionRebuildReport"]
