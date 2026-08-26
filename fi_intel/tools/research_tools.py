"""The five agent tools, built on our corpus and graph.

Each tool is a thin, entitlement-checked capability. They are plain async
callables (not LLM-tool-decorated) so they can be unit-tested without a
model; the agent layer binds them. graph_query uses only the parameterized
pattern templates — never free-form Cypher (injection + accuracy risk).
"""

import json
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fi_intel.governance.policy import GraphAccessContext
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.retrieval.entitlement import Principal
from fi_intel.retrieval.service import RetrievalService
from fi_intel.tools.evidence import EvidenceItem, GraphFact, PrecedentEpisode


class ToolContext(BaseModel):
    """What every tool call carries: who is asking and the temporal pin."""

    model_config = ConfigDict(frozen=True)

    principal: Principal
    as_of: datetime


class ResearchTools:
    """Bundles the corpus and graph behind the five agent capabilities."""

    def __init__(
        self,
        retrieval: RetrievalService,
        graph: GraphClient,
        registry: PatternRegistry,
        context: ToolContext,
    ) -> None:
        if registry.access.principal != context.principal:
            msg = "tool context principal does not match graph policy context"
            raise ValueError(msg)
        self._retrieval = retrieval
        self._graph = graph
        self._registry = registry
        self._ctx = context

    @property
    def access(self) -> GraphAccessContext:
        """Verified graph/corpus policy context for publication rechecks."""
        return self._registry.access

    async def corpus_search(
        self, query: str, limit: int = 10, entity_lei: str | None = None
    ) -> list[EvidenceItem]:
        """Hybrid search over the licensed corpus, entitlement-checked."""
        results = await self._retrieval.search(
            query, self._ctx.principal, as_of=self._ctx.as_of, entity_lei=entity_lei, limit=limit
        )
        return [
            EvidenceItem(
                evidence_id=EvidenceItem.make_id(
                    r.doc.source_id, r.doc.doc_id, r.chunk.char_start, r.chunk.char_end
                ),
                source_id=r.doc.source_id,
                doc_id=r.doc.doc_id,
                source_url=r.doc.url,
                char_start=r.chunk.char_start,
                char_end=r.chunk.char_end,
                excerpt=r.chunk.text,
            )
            for r in results
        ]

    async def graph_query(self, pattern: str) -> list[dict[str, str]]:
        """Run ONE named parameterized pattern template. No free-form Cypher."""
        if pattern not in self._registry.pattern_names():
            msg = f"unknown pattern {pattern!r}; only named templates are allowed"
            raise ValueError(msg)
        signals = await self._registry.run(self._ctx.as_of, enabled={pattern})
        return [
            {"entity": s.entity_name, "entity_key": s.entity_key, **s.evidence} for s in signals
        ]

    async def entity_profile(self, entity_key: str) -> dict[str, object]:
        """Typed assertions and their exact, entitlement-resolved source spans."""
        rows = await self._graph.read_assertions(
            as_of=self._ctx.as_of,
            access=self._registry.access,
            endpoint_key=entity_key,
        )
        facts: list[GraphFact] = []
        evidence: list[EvidenceItem] = []
        for row in rows:
            assertion = row["a"]
            subject = row["s"]
            object_ = row["o"]
            source_id = str(assertion["source_id"])
            doc_id = str(assertion["source_doc_id"])
            start = int(assertion["snippet_start"])
            end = int(assertion["snippet_end"])
            evidence_id = EvidenceItem.make_id(source_id, doc_id, start, end)
            resolved = await self._retrieval.resolve_span(
                self._ctx.principal,
                source_id,
                doc_id,
                start,
                end,
                as_of=self._ctx.as_of,
            )
            if resolved is not None:
                doc, excerpt = resolved
                evidence.append(
                    EvidenceItem(
                        evidence_id=evidence_id,
                        source_id=source_id,
                        doc_id=doc_id,
                        source_url=doc.url,
                        char_start=start,
                        char_end=end,
                        excerpt=excerpt,
                    )
                )
            properties = json.loads(assertion.get("properties_json") or "{}")
            facts.append(
                GraphFact(
                    assertion_id=str(assertion["assertion_id"]),
                    predicate=str(assertion["predicate"]),
                    subject_type=str(subject["node_type"]),
                    subject_key=str(subject["key"]),
                    subject_name=str(subject["display_name"]),
                    object_type=str(object_["node_type"]),
                    object_key=str(object_["key"]),
                    object_name=str(object_["display_name"]),
                    properties={str(key): str(value) for key, value in properties.items()},
                    valid_from=str(assertion["valid_from"]),
                    confidence=float(assertion["confidence"]),
                    evidence_id=evidence_id,
                )
            )
        return {
            "entity_key": entity_key,
            "assertion_count": len(rows),
            "predicates": sorted({fact.predicate for fact in facts}),
            "assertions": facts,
            "evidence": evidence,
        }

    async def timeseries_lookup(self, entity_key: str, metric: str) -> list[dict[str, str]]:
        """Metric assertions over time for an entity (e.g. CET1 trend)."""
        rows = await self._graph.read_assertions(
            as_of=self._ctx.as_of,
            access=self._registry.access,
            subject_key=entity_key,
        )
        out = []
        for r in rows:
            if r["a"]["predicate"] != "REPORTS_METRIC":
                continue
            import json

            props = json.loads(r["a"].get("properties_json") or "{}")
            if props.get("metric") == metric:
                out.append({"valid_from": str(r["a"]["valid_from"]), **props})
        return sorted(out, key=lambda x: x["valid_from"])

    async def precedent_search(self, query: str, limit: int = 5) -> list[PrecedentEpisode]:
        """Search prior resolved signals and outcomes, not document lookalikes."""
        rows = await self._graph.read_resolved_signal_precedents(
            query,
            as_of=self._ctx.as_of,
            access=self._registry.access,
            limit=limit,
        )
        return [
            PrecedentEpisode(
                signal_id=str(row["s"]["signal_id"]),
                pattern=str(row["s"]["pattern"]),
                entity_key=str(row["s"]["entity_key"]),
                entity_name=str(row["s"]["entity_name"]),
                resolved_at=str(row["s"]["resolved_at"]),
                outcome_ids=tuple(str(item) for item in row["s"].get("outcome_ids", [])),
                evidence_ids=tuple(
                    EvidenceItem.make_id(
                        str(ref["source_id"]),
                        str(ref["doc_id"]),
                        int(ref["snippet_start"]),
                        int(ref["snippet_end"]),
                    )
                    for ref in row["refs"]
                ),
            )
            for row in rows
        ]

    async def signals(self, as_of: datetime | None = None) -> list[Signal]:
        """Run all detectors and return fired signals at as_of."""
        return await self._registry.run(as_of or self._ctx.as_of)
