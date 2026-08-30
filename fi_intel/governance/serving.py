"""Single registry-routed construction path for every model-backed component."""

from __future__ import annotations

from dataclasses import dataclass

from fi_intel.agents.entailment import (
    ENTAILMENT_PROMPT_VERSION,
    ENTAILMENT_SCHEMA_VERSION,
    OpenAICompatibleEntailmentVerifier,
    build_entailment_verifier,
)
from fi_intel.agents.opportunity_research import RESEARCH_PROMPT_VERSION
from fi_intel.agents.reasoning.openai_compatible_reasoning import (
    OpenAICompatibleReasoningModel,
    build_reasoning_model,
)
from fi_intel.config import Settings
from fi_intel.governance.model_registry import (
    ModelArtifact,
    ModelComponent,
    ModelRegistry,
)
from fi_intel.governance.model_usage import ModelUsageLog
from fi_intel.governance.routing import ModelCallLineage, model_call_lineage, route_model_release
from fi_intel.ingest.extract import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
from fi_intel.ingest.extractors.openai_compatible_extractor import (
    OpenAICompatibleStructuredExtractor,
    build_structured_extractor,
)
from fi_intel.retrieval.embedders.ollama_embedder import (
    INDEX_IDENTITY_SCHEMA,
    INPUT_PREPROCESSING_VERSION,
    OllamaEmbedder,
    build_embedder,
)
from fi_intel.retrieval.reranking import (
    RERANKER_PROMPT_VERSION,
    RERANKER_SCHEMA_VERSION,
    OpenAICompatibleReranker,
    build_reranker,
)


@dataclass(frozen=True, slots=True)
class GovernedModelBundle:
    extractor: OpenAICompatibleStructuredExtractor
    reasoner: OpenAICompatibleReasoningModel
    embedder: OllamaEmbedder
    reranker: OpenAICompatibleReranker
    entailment: OpenAICompatibleEntailmentVerifier
    artifacts: tuple[ModelArtifact, ...]
    lineages: tuple[ModelCallLineage, ...]

    @classmethod
    async def build(
        cls,
        *,
        settings: Settings,
        registry: ModelRegistry,
        usage_log: ModelUsageLog,
        run_id: str,
        subject_id: str,
    ) -> GovernedModelBundle:
        extraction = await route_model_release(
            registry,
            ModelComponent.EXTRACTION,
            subject_id,
            prompt_version=EXTRACTION_PROMPT_VERSION,
            schema_version="extraction-response-v1",
        )
        reasoning = await route_model_release(
            registry,
            ModelComponent.REASONING,
            subject_id,
            prompt_version=RESEARCH_PROMPT_VERSION,
            schema_version="opportunity-v2",
        )
        embedding = await route_model_release(
            registry,
            ModelComponent.EMBEDDING,
            subject_id,
            prompt_version=INPUT_PREPROCESSING_VERSION,
            schema_version=INDEX_IDENTITY_SCHEMA,
        )
        reranker = await route_model_release(
            registry,
            ModelComponent.RERANKER,
            subject_id,
            prompt_version=RERANKER_PROMPT_VERSION,
            schema_version=RERANKER_SCHEMA_VERSION,
        )
        entailment = await route_model_release(
            registry,
            ModelComponent.ENTAILMENT,
            subject_id,
            prompt_version=ENTAILMENT_PROMPT_VERSION,
            schema_version=ENTAILMENT_SCHEMA_VERSION,
        )
        artifacts = (extraction, reasoning, embedding, reranker, entailment)
        common = {
            "preprocessing_version": "canonical-document-v1",
            "tool_contract_version": "bounded-research-tools-v2",
        }
        lineages = tuple(
            model_call_lineage(
                artifact,
                **common,
                settings={
                    "analysis_mode": settings.analysis_mode,
                    "model_id": artifact.model_id,
                    "embedding_dim": (
                        settings.embedding_dim
                        if artifact.component is ModelComponent.EMBEDDING
                        else None
                    ),
                },
            )
            for artifact in artifacts
        )
        built_embedder = build_embedder(settings, embedding, usage_log, run_id)
        if not isinstance(built_embedder, OllamaEmbedder):
            raise RuntimeError("governed serving cannot use a fixture hashing embedder")
        return cls(
            extractor=build_structured_extractor(settings, usage_log, run_id, extraction),
            reasoner=build_reasoning_model(settings, usage_log, run_id, reasoning),
            embedder=built_embedder,
            reranker=build_reranker(settings, usage_log, run_id, reranker),
            entailment=build_entailment_verifier(settings, usage_log, run_id, entailment),
            artifacts=artifacts,
            lineages=lineages,
        )


__all__ = ["GovernedModelBundle"]
