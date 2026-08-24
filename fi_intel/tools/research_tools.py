"""The five agent tools, built on our corpus and graph.

Each tool is a thin, entitlement-checked capability. They are plain async
callables (not LLM-tool-decorated) so they can be unit-tested without a
model; the agent layer binds them. graph_query uses only the parameterized
pattern templates — never free-form Cypher (injection + accuracy risk).
"""

from datetime import datetime

from pydantic import BaseModel, ConfigDict

from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.retrieval.entitlement import Principal
from fi_intel.retrieval.service import RetrievalService
from fi_intel.tools.evidence import EvidenceItem


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
        self._retrieval = retrieval
        self._graph = graph
        self._registry = registry
        self._ctx = context

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
            {"entity": s.entity_name, "entity_key": s.entity_key, **s.evidence}
            for s in signals
        ]

    async def entity_profile(self, entity_key: str) -> dict[str, object]:
        """All assertions about one entity, visible at as_of."""
        rows = await self._graph.read_assertions(
            as_of=self._ctx.as_of, subject_key=entity_key
        )
        return {
            "entity_key": entity_key,
            "assertion_count": len(rows),
            "predicates": sorted({r["a"]["predicate"] for r in rows}),
        }

    async def timeseries_lookup(self, entity_key: str, metric: str) -> list[dict[str, str]]:
        """Metric assertions over time for an entity (e.g. CET1 trend)."""
        rows = await self._graph.read_assertions(
            as_of=self._ctx.as_of, subject_key=entity_key
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

    async def precedent_search(self, query: str, limit: int = 5) -> list[EvidenceItem]:
        """Search historical episodes (corpus) for precedents to a situation."""
        return await self.corpus_search(query, limit=limit)

    async def signals(self, as_of: datetime | None = None) -> list[Signal]:
        """Run all detectors and return fired signals at as_of."""
        return await self._registry.run(as_of or self._ctx.as_of)
