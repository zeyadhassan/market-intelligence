"""The five agent tools, built on our corpus and graph.

Each tool is a thin, entitlement-checked capability. They are plain async
callables (not LLM-tool-decorated) so they can be unit-tested without a
model; the agent layer binds them. graph_query uses only the parameterized
pattern templates — never free-form Cypher (injection + accuracy risk).
"""

import json
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict

from fi_intel.governance.policy import GraphAccessContext
from fi_intel.graph.client import GraphClient
from fi_intel.graph.entry import GraphEntryRequest, GraphEntryResolverPort, GraphEntryResult
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.retrieval.corpus import ScoredChunk
from fi_intel.retrieval.entitlement import Principal
from fi_intel.retrieval.planning import (
    EvidenceBundle,
    EvidencePolarity,
    RetrievalDiagnostics,
    RetrievalFallbackTier,
    RetrievalQueryPlan,
    diversify_results,
    query_digest,
)
from fi_intel.retrieval.reranking import Reranker
from fi_intel.retrieval.service import RetrievalService
from fi_intel.tools.evidence import EvidenceItem, GraphFact, GraphPath, PrecedentEpisode


def _polarity_candidates(
    candidates: list[ScoredChunk],
    plan: RetrievalQueryPlan,
    polarity: EvidencePolarity,
) -> list[ScoredChunk]:
    if polarity is EvidencePolarity.SUPPORT:
        return candidates
    terms = tuple(item.casefold() for item in plan.contradiction_terms)
    return [
        item for item in candidates if any(term in item.chunk.text.casefold() for term in terms)
    ]


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
        entry_resolver: GraphEntryResolverPort | None = None,
        reranker: Reranker | None = None,
    ) -> None:
        if registry.access.principal != context.principal:
            msg = "tool context principal does not match graph policy context"
            raise ValueError(msg)
        self._retrieval = retrieval
        self._graph = graph
        self._registry = registry
        self._ctx = context
        self._entry_resolver = entry_resolver
        self._reranker = reranker

    @property
    def access(self) -> GraphAccessContext:
        """Verified graph/corpus policy context for publication rechecks."""
        return self._registry.access

    @property
    def supports_planned_search(self) -> bool:
        return True

    @property
    def supports_graph_entry(self) -> bool:
        return self._entry_resolver is not None

    @property
    def supports_neighborhood(self) -> bool:
        return True

    @property
    def supports_timeseries(self) -> bool:
        return True

    @property
    def supports_precedents(self) -> bool:
        return True

    async def corpus_search(
        self, query: str, limit: int = 10, entity_lei: str | None = None
    ) -> list[EvidenceItem]:
        """Hybrid search over the licensed corpus, entitlement-checked."""
        results = await self._retrieval.search(
            query, self._ctx.principal, as_of=self._ctx.as_of, entity_lei=entity_lei, limit=limit
        )
        if self._reranker is not None:
            results = await self._reranker.rerank(query, results, limit=min(50, limit * 3))
        results = diversify_results(results, limit=limit)
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
                source_version_id=r.doc.metadata.get("ledger_document_version_id"),
                content_hash=r.doc.content_hash(),
                lexical_score=r.bm25_score,
                vector_score=r.vector_score,
                reranker_score=r.reranker_score,
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
        spans = tuple(
            (
                str(row["a"]["source_id"]),
                str(row["a"]["source_doc_id"]),
                int(row["a"]["snippet_start"]),
                int(row["a"]["snippet_end"]),
            )
            for row in rows
        )
        resolved_spans = (
            await self._retrieval.resolve_spans(
                self._ctx.principal,
                spans,
                as_of=self._ctx.as_of,
            )
            if spans
            else {}
        )
        for row in rows:
            assertion = row["a"]
            subject = row["s"]
            object_ = row["o"]
            source_id = str(assertion["source_id"])
            doc_id = str(assertion["source_doc_id"])
            start = int(assertion["snippet_start"])
            end = int(assertion["snippet_end"])
            evidence_id = EvidenceItem.make_id(source_id, doc_id, start, end)
            resolved = resolved_spans.get((source_id, doc_id, start, end))
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
                        source_version_id=doc.metadata.get("ledger_document_version_id"),
                        content_hash=doc.content_hash(),
                        extraction_version=str(assertion.get("model_version") or "unknown"),
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
                    properties={str(key): value for key, value in properties.items()},
                    valid_from=str(assertion["valid_from"]),
                    valid_to=(
                        str(assertion["valid_to"])
                        if assertion.get("valid_to") is not None
                        else None
                    ),
                    recorded_at=str(assertion.get("recorded_at")),
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
                outcome_status=str(row["s"].get("analyst_disposition") or "unknown_outcome"),
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

    async def evidence_search(
        self,
        plan: RetrievalQueryPlan,
        *,
        polarity: EvidencePolarity,
    ) -> EvidenceBundle:
        """Execute the governed tier ladder and expose admission diagnostics."""

        query = plan.query(polarity)
        tier = RetrievalFallbackTier.CANONICAL_ENTITY
        source_ids = set(plan.source_ids) if plan.source_ids else None
        raw = _polarity_candidates(
            await self._retrieval.search(
                query,
                self._ctx.principal,
                as_of=self._ctx.as_of,
                entity_lei=plan.canonical_entity_lei,
                source_ids=source_ids,
                date_from=plan.date_from,
                date_to=plan.date_to,
                limit=min(50, plan.limit * 5),
            ),
            plan,
            polarity,
        )
        if not raw:
            for related_lei in plan.related_entity_leis:
                raw = _polarity_candidates(
                    await self._retrieval.search(
                        query,
                        self._ctx.principal,
                        as_of=self._ctx.as_of,
                        entity_lei=related_lei,
                        source_ids=source_ids,
                        date_from=plan.date_from,
                        date_to=plan.date_to,
                        limit=min(50, plan.limit * 5),
                    ),
                    plan,
                    polarity,
                )
                if raw:
                    tier = RetrievalFallbackTier.RELATED_ENTITY
                    break
        if not raw:
            broad = await self._retrieval.search(
                query,
                self._ctx.principal,
                as_of=self._ctx.as_of,
                source_ids=source_ids,
                date_from=plan.date_from,
                date_to=plan.date_to,
                limit=min(50, plan.limit * 5),
            )
            identity_terms = {
                plan.entity_name.casefold(),
                *(item.casefold() for item in plan.aliases),
            }
            raw = _polarity_candidates(
                [
                    item
                    for item in broad
                    if any(term in item.chunk.text.casefold() for term in identity_terms)
                ],
                plan,
                polarity,
            )
            tier = RetrievalFallbackTier.BROADER_CORPUS if raw else RetrievalFallbackTier.ABSTAINED
        candidate_count = len(raw)
        if self._reranker is not None:
            raw = await self._reranker.rerank(query, raw, limit=min(50, plan.limit * 3))
        admitted = diversify_results(raw, limit=plan.limit)
        return EvidenceBundle(
            results=tuple(admitted),
            diagnostics=RetrievalDiagnostics(
                query_digest=query_digest(query),
                fallback_tier=tier,
                polarity=polarity,
                candidate_count=candidate_count,
                admitted_count=len(admitted),
                model_version=(
                    f"{self._retrieval.model_version}+{self._reranker.model_version}"
                    if self._reranker is not None
                    else self._retrieval.model_version
                ),
                filters={
                    "entity_lei": plan.canonical_entity_lei or "",
                    "as_of": self._ctx.as_of.isoformat(),
                    "date_from": plan.date_from.isoformat() if plan.date_from else "",
                    "date_to": plan.date_to.isoformat() if plan.date_to else "",
                },
            ),
        )

    async def planned_corpus_search(
        self,
        plan: RetrievalQueryPlan,
        *,
        polarity: EvidencePolarity,
    ) -> list[EvidenceItem]:
        """Return analyst evidence while retaining planned-search diagnostics."""

        bundle = await self.evidence_search(plan, polarity=polarity)
        diagnostics = bundle.diagnostics
        return [
            EvidenceItem(
                evidence_id=EvidenceItem.make_id(
                    item.doc.source_id,
                    item.doc.doc_id,
                    item.chunk.char_start,
                    item.chunk.char_end,
                ),
                source_id=item.doc.source_id,
                doc_id=item.doc.doc_id,
                source_url=item.doc.url,
                char_start=item.chunk.char_start,
                char_end=item.chunk.char_end,
                excerpt=item.chunk.text,
                source_version_id=item.doc.metadata.get("ledger_document_version_id"),
                content_hash=item.doc.content_hash(),
                lexical_score=item.bm25_score,
                vector_score=item.vector_score,
                reranker_score=item.reranker_score,
                fallback_tier=diagnostics.fallback_tier.value,
                admission_reason=(
                    f"{diagnostics.polarity.value} query {diagnostics.query_digest[:12]} "
                    f"via {diagnostics.model_version}"
                ),
            )
            for item in bundle.results
        ]

    async def contradiction_search(
        self,
        query: str,
        *,
        limit: int = 5,
        entity_lei: str | None = None,
    ) -> list[EvidenceItem]:
        """Search a separately recorded, deterministic contradiction query."""

        contradiction_terms = (
            "withdrawn cancelled denied rejected completed refinanced superseded corrected"
        )
        return await self.corpus_search(
            f"{query} {contradiction_terms}",
            limit=limit,
            entity_lei=entity_lei,
        )

    async def resolve_graph_entry(self, request: GraphEntryRequest) -> GraphEntryResult:
        if request.principal != self._ctx.principal or request.as_of != self._ctx.as_of:
            raise ValueError("graph entry request does not match the authorized tool context")
        if self._entry_resolver is None:
            raise RuntimeError("governed graph entry resolver is not configured")
        return await self._entry_resolver.resolve(request)

    async def entity_neighborhood(  # noqa: C901
        self,
        entity_key: str,
        *,
        allowed_predicates: frozenset[str],
        max_hops: int = 2,
        max_nodes: int = 40,
        max_assertions: int = 80,
        max_paths: int = 60,
    ) -> list[GraphPath]:
        """Bounded allowlisted traversal over current, authorized assertions."""

        if not 1 <= max_hops <= 2:
            raise ValueError("max_hops must be in [1, 2]")
        if not allowed_predicates:
            raise ValueError("at least one governed predicate is required")
        if not (1 <= max_nodes <= 200 and 1 <= max_assertions <= 500 and 1 <= max_paths <= 200):
            raise ValueError("neighborhood limits exceed policy bounds")
        frontier = {entity_key}
        seen_nodes = {entity_key}
        paths: list[GraphPath] = []
        assertion_count = 0
        for hop in range(1, max_hops + 1):
            next_frontier: set[str] = set()
            for key in sorted(frontier):
                rows = await self._graph.read_assertions(
                    as_of=self._ctx.as_of,
                    access=self._registry.access,
                    endpoint_key=key,
                    limit=min(max_assertions - assertion_count, 250),
                )
                for row in rows:
                    assertion = row["a"]
                    predicate = str(assertion["predicate"])
                    if predicate not in allowed_predicates:
                        continue
                    subject, object_ = row["s"], row["o"]
                    end_key = (
                        str(object_["key"]) if str(subject["key"]) == key else str(subject["key"])
                    )
                    fact = self._graph_fact(row)
                    path_id = f"{entity_key}:{hop}:{fact.assertion_id}"
                    paths.append(
                        GraphPath(
                            path_id=path_id,
                            hop_count=hop,
                            start_key=key,
                            end_key=end_key,
                            assertions=(fact,),
                        )
                    )
                    assertion_count += 1
                    if end_key not in seen_nodes and len(seen_nodes) < max_nodes:
                        seen_nodes.add(end_key)
                        next_frontier.add(end_key)
                    if assertion_count >= max_assertions or len(paths) >= max_paths:
                        return paths
            frontier = next_frontier
            if not frontier:
                break
        return paths

    @staticmethod
    def _graph_fact(row: dict[str, Any]) -> GraphFact:
        assertion = row["a"]
        subject = row["s"]
        object_ = row["o"]
        properties = json.loads(assertion.get("properties_json") or "{}")
        source_id = str(assertion["source_id"])
        doc_id = str(assertion["source_doc_id"])
        start = int(assertion["snippet_start"])
        end = int(assertion["snippet_end"])
        return GraphFact(
            assertion_id=str(assertion["assertion_id"]),
            predicate=str(assertion["predicate"]),
            subject_type=str(subject["node_type"]),
            subject_key=str(subject["key"]),
            subject_name=str(subject["display_name"]),
            object_type=str(object_["node_type"]),
            object_key=str(object_["key"]),
            object_name=str(object_["display_name"]),
            properties={str(key): value for key, value in properties.items()},
            valid_from=str(assertion["valid_from"]),
            valid_to=str(assertion["valid_to"]) if assertion.get("valid_to") else None,
            recorded_at=str(assertion.get("recorded_at")),
            confidence=float(assertion["confidence"]),
            evidence_id=EvidenceItem.make_id(source_id, doc_id, start, end),
        )

    async def signals(self, as_of: datetime | None = None) -> list[Signal]:
        """Run all detectors and return fired signals at as_of."""
        return await self._registry.run(as_of or self._ctx.as_of)
