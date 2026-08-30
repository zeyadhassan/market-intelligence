"""Outcome and lineage accounting for governed verifier/reranker calls."""

import json
from datetime import UTC, datetime
from uuid import UUID

import httpx2
import openai
import pytest
from pydantic import ValidationError

from fi_intel.agents.entailment import (
    ENTAILMENT_PROMPT_VERSION,
    ENTAILMENT_SCHEMA_VERSION,
    OpenAICompatibleEntailmentVerifier,
)
from fi_intel.governance.model_registry import ModelArtifact, ModelComponent
from fi_intel.governance.model_usage import InMemoryModelUsageLog
from fi_intel.retrieval.chunking import Chunk
from fi_intel.retrieval.corpus import ScoredChunk
from fi_intel.retrieval.reranking import (
    RERANKER_PROMPT_VERSION,
    RERANKER_SCHEMA_VERSION,
    OpenAICompatibleReranker,
)
from fi_intel.sources.canonical import BarrierSide, CanonicalDocument, DocumentClass
from fi_intel.tools.evidence import EvidenceItem

NOW = datetime(2026, 8, 27, tzinfo=UTC)


def _artifact(component: ModelComponent, model: str) -> ModelArtifact:
    return ModelArtifact(
        release_id=UUID(f"00000000-0000-0000-0000-{len(component.value):012d}"),
        component=component,
        model_id=model,
        artifact_digest="a" * 64,
        prompt_version="registered-prompt-v1",
        schema_version="registered-schema-v1",
        evaluation_dataset_digest="b" * 64,
        evaluation_report_digest="c" * 64,
        quality_gate_passed=True,
        evaluated_at=NOW,
        created_at=NOW,
        created_by="model-risk",
    )


def _client_returning(
    content: str | None, *, refusal: str | None = None
) -> tuple[openai.AsyncOpenAI, list[httpx2.Request]]:
    captured: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        captured.append(request)
        return httpx2.Response(
            200,
            json={
                "id": "chatcmpl-test",
                "object": "chat.completion",
                "created": 0,
                "model": "governed-model",
                "choices": [
                    {
                        "index": 0,
                        "message": {
                            "role": "assistant",
                            "content": content,
                            "refusal": refusal,
                        },
                        "finish_reason": "stop",
                    }
                ],
                "usage": {
                    "prompt_tokens": 20,
                    "completion_tokens": 5,
                    "total_tokens": 25,
                },
            },
        )

    return (
        openai.AsyncOpenAI(
            base_url="http://localhost:9999/v1",
            api_key="not-needed",
            http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
        ),
        captured,
    )


def _client_timing_out() -> openai.AsyncOpenAI:
    def handler(request: httpx2.Request) -> httpx2.Response:
        raise httpx2.ReadTimeout("model deadline exceeded", request=request)

    return openai.AsyncOpenAI(
        base_url="http://localhost:9999/v1",
        api_key="not-needed",
        max_retries=0,
        http_client=httpx2.AsyncClient(transport=httpx2.MockTransport(handler)),
    )


def _candidate() -> ScoredChunk:
    doc = CanonicalDocument(
        doc_id="doc-1",
        source_id="official",
        published_at=NOW,
        recorded_at=NOW,
        title="Programme approved",
        body="Example Bank approved its issuance programme.",
        document_class=DocumentClass.REGULATORY,
        barrier_side=BarrierSide.PUBLIC,
    )
    return ScoredChunk(
        chunk=Chunk(
            source_id=doc.source_id,
            doc_id=doc.doc_id,
            chunk_index=0,
            char_start=0,
            char_end=20,
            text="Programme approved",
        ),
        doc=doc,
        score=0.8,
        bm25_rank=1,
        vector_rank=1,
    )


async def test_reranker_records_governed_lineage_and_reasoning_setting() -> None:
    client, captured = _client_returning(json.dumps({"results": [{"index": 0, "score": 0.95}]}))
    usage = InMemoryModelUsageLog()
    artifact = _artifact(ModelComponent.RERANKER, "governed-model")
    reranker = OpenAICompatibleReranker(
        client=client,
        model="governed-model",
        usage_log=usage,
        run_id="run-1",
        artifact=artifact,
        reasoning_effort="medium",
    )

    ranked = await reranker.rerank("issuance programme", [_candidate()], limit=1)

    assert ranked[0].reranker_score == 0.95
    sent = json.loads(captured[0].content)
    assert sent["reasoning_effort"] == "medium"
    (event,) = usage.events
    assert event.status == "succeeded"
    assert event.release_id == artifact.release_id
    assert event.artifact_digest == artifact.artifact_digest
    assert event.prompt_version == RERANKER_PROMPT_VERSION
    assert event.schema_version == RERANKER_SCHEMA_VERSION


async def test_entailment_malformed_output_is_recorded_before_rejection() -> None:
    client, _ = _client_returning('{"status":"supported"}')
    usage = InMemoryModelUsageLog()
    artifact = _artifact(ModelComponent.ENTAILMENT, "governed-model")
    verifier = OpenAICompatibleEntailmentVerifier(
        client=client,
        model="governed-model",
        temperature=0.0,
        reasoning_effort=None,
        usage_log=usage,
        run_id="run-1",
        artifact=artifact,
    )
    evidence = EvidenceItem(
        evidence_id="official/doc-1:0-20",
        source_id="official",
        doc_id="doc-1",
        char_start=0,
        char_end=20,
        excerpt="Programme approved",
    )

    with pytest.raises(ValidationError):
        await verifier.verify("The programme was approved.", [evidence])

    (event,) = usage.events
    assert event.status == "malformed"
    assert event.release_id == artifact.release_id
    assert event.prompt_version == ENTAILMENT_PROMPT_VERSION
    assert event.schema_version == ENTAILMENT_SCHEMA_VERSION


async def test_entailment_refusal_is_recorded_before_rejection() -> None:
    client, _ = _client_returning(None, refusal="Unable to assess this claim")
    usage = InMemoryModelUsageLog()
    artifact = _artifact(ModelComponent.ENTAILMENT, "governed-model")
    verifier = OpenAICompatibleEntailmentVerifier(
        client=client,
        model="governed-model",
        temperature=0.0,
        reasoning_effort=None,
        usage_log=usage,
        run_id="run-1",
        artifact=artifact,
    )

    with pytest.raises(ValueError, match="returned no content"):
        await verifier.verify(
            "The programme was approved.",
            [
                EvidenceItem(
                    evidence_id="official/doc-1:0-20",
                    source_id="official",
                    doc_id="doc-1",
                    char_start=0,
                    char_end=20,
                    excerpt="Programme approved",
                )
            ],
        )

    (event,) = usage.events
    assert event.status == "refused"
    assert event.error_type is None


async def test_entailment_timeout_is_recorded_before_propagation() -> None:
    usage = InMemoryModelUsageLog()
    artifact = _artifact(ModelComponent.ENTAILMENT, "governed-model")
    verifier = OpenAICompatibleEntailmentVerifier(
        client=_client_timing_out(),
        model="governed-model",
        temperature=0.0,
        reasoning_effort=None,
        usage_log=usage,
        run_id="run-1",
        artifact=artifact,
    )

    with pytest.raises(openai.APITimeoutError):
        await verifier.verify(
            "The programme was approved.",
            [
                EvidenceItem(
                    evidence_id="official/doc-1:0-20",
                    source_id="official",
                    doc_id="doc-1",
                    char_start=0,
                    char_end=20,
                    excerpt="Programme approved",
                )
            ],
        )

    (event,) = usage.events
    assert event.status == "timed_out"
    assert event.error_type == "APITimeoutError"
