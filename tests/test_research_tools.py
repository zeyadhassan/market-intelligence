from datetime import UTC, datetime
from types import SimpleNamespace
from typing import cast
from unittest.mock import AsyncMock

from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.retrieval.entitlement import Principal, Side
from fi_intel.retrieval.service import RetrievalService
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass
from fi_intel.tools.evidence import EvidenceItem, GraphFact
from fi_intel.tools.research_tools import ResearchTools, ToolContext


async def test_precedent_search_reads_resolved_episodes_not_corpus_alias() -> None:
    principal = Principal(principal_id="analyst", entitlement_group="test", side=Side.PUBLIC)
    access = trusted_test_access("wire", principal_id=principal.principal_id)
    graph = SimpleNamespace(
        read_resolved_signal_precedents=AsyncMock(
            return_value=[
                {
                    "s": {
                        "signal_id": "signal-1",
                        "pattern": "maturity_wall_no_refi",
                        "entity_key": "LEI-1",
                        "entity_name": "Example Bank",
                        "resolved_at": datetime(2024, 5, 1, tzinfo=UTC),
                        "outcome_ids": ["mandate-1"],
                    },
                    "refs": [
                        {
                            "source_id": "wire",
                            "doc_id": "doc-1",
                            "snippet_start": 4,
                            "snippet_end": 19,
                        }
                    ],
                }
            ]
        )
    )
    registry = SimpleNamespace(access=access)
    tools = ResearchTools(
        cast(RetrievalService, object()),
        cast(GraphClient, graph),
        cast(PatternRegistry, registry),
        ToolContext(principal=principal, as_of=datetime(2024, 6, 1, tzinfo=UTC)),
    )

    precedents = await tools.precedent_search("maturity refinancing")

    assert precedents[0].signal_id == "signal-1"
    assert precedents[0].outcome_ids == ("mandate-1",)
    assert precedents[0].evidence_ids == ("wire/doc-1:4-19",)
    graph.read_resolved_signal_precedents.assert_awaited_once()


async def test_entity_profile_returns_typed_assertions_with_exact_evidence_span() -> None:
    as_of = datetime(2024, 6, 1, tzinfo=UTC)
    principal = Principal(principal_id="analyst", entitlement_group="test", side=Side.PUBLIC)
    access = trusted_test_access("wire", principal_id=principal.principal_id)
    doc = CanonicalDocument(
        source_id="wire",
        doc_id="doc-1",
        published_at=as_of,
        recorded_at=as_of,
        title="Programme",
        body="Example Bank approved a programme.",
        document_class=DocumentClass.NEWS_WIRE,
    )
    retrieval = SimpleNamespace(
        resolve_spans=AsyncMock(
            return_value={("wire", "doc-1", 10, 31): (doc, "Example Bank approved")}
        )
    )
    graph = SimpleNamespace(
        read_assertions=AsyncMock(
            return_value=[
                {
                    "a": {
                        "assertion_id": "assertion-1",
                        "predicate": "PROGRAMME_APPROVED_BY",
                        "source_id": "wire",
                        "source_doc_id": "doc-1",
                        "snippet_start": 10,
                        "snippet_end": 31,
                        "properties_json": '{"programme":"EMTN"}',
                        "valid_from": as_of,
                        "confidence": 0.9,
                    },
                    "s": {
                        "node_type": "Programme",
                        "key": "programme-1",
                        "display_name": "EMTN",
                    },
                    "o": {
                        "node_type": "Organization",
                        "key": "LEI-1",
                        "display_name": "Example Bank",
                    },
                }
            ]
        )
    )
    tools = ResearchTools(
        cast(RetrievalService, retrieval),
        cast(GraphClient, graph),
        cast(PatternRegistry, SimpleNamespace(access=access)),
        ToolContext(principal=principal, as_of=as_of),
    )

    profile = await tools.entity_profile("LEI-1")

    facts = profile["assertions"]
    evidence = profile["evidence"]
    assert isinstance(facts, list) and isinstance(facts[0], GraphFact)
    assert facts[0].object_key == "LEI-1"
    assert facts[0].properties == {"programme": "EMTN"}
    assert isinstance(evidence, list) and isinstance(evidence[0], EvidenceItem)
    assert evidence[0].evidence_id == facts[0].evidence_id
    graph.read_assertions.assert_awaited_once_with(
        as_of=as_of,
        access=access,
        endpoint_key="LEI-1",
    )
