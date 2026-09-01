"""Single configuration-driven construction path for model-backed components."""

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
from fi_intel.governance.model_registry import ModelComponent
from fi_intel.governance.model_usage import ModelUsageLog
from fi_intel.governance.routing import ModelCallLineage, configured_model_lineage
from fi_intel.ingest.extract import PROMPT_VERSION as EXTRACTION_PROMPT_VERSION
from fi_intel.ingest.extractors.openai_compatible_extractor import (
    OpenAICompatibleStructuredExtractor,
    build_structured_extractor,
)
from fi_intel.retrieval.embedders.openai_compatible_embedder import (
    INDEX_IDENTITY_SCHEMA,
    INPUT_PREPROCESSING_VERSION,
    OpenAICompatibleEmbedder,
    build_embedder,
)
from fi_intel.retrieval.reranking import (
    RERANKER_PROMPT_VERSION,
    RERANKER_SCHEMA_VERSION,
    OpenAICompatibleReranker,
    build_reranker,
)


@dataclass(frozen=True, slots=True)
class ModelBundle:
    extractor: OpenAICompatibleStructuredExtractor
    reasoner: OpenAICompatibleReasoningModel
    embedder: OpenAICompatibleEmbedder
    reranker: OpenAICompatibleReranker
    entailment: OpenAICompatibleEntailmentVerifier
    lineages: tuple[ModelCallLineage, ...]

    @classmethod
    async def build(
        cls,
        *,
        settings: Settings,
        usage_log: ModelUsageLog,
        run_id: str,
        subject_id: str = "local-analyst",
    ) -> ModelBundle:
        del subject_id
        embedding_model = settings.embedding_model
        if embedding_model is None:
            raise RuntimeError("FI_INTEL_EMBEDDING_MODEL is required")
        specifications = (
            (
                ModelComponent.EXTRACTION,
                settings.extraction_model,
                EXTRACTION_PROMPT_VERSION,
                "extraction-response-v1",
            ),
            (
                ModelComponent.REASONING,
                settings.research_model,
                RESEARCH_PROMPT_VERSION,
                "opportunity-v2",
            ),
            (
                ModelComponent.EMBEDDING,
                embedding_model,
                INPUT_PREPROCESSING_VERSION,
                INDEX_IDENTITY_SCHEMA,
            ),
            (
                ModelComponent.RERANKER,
                settings.reranker_model,
                RERANKER_PROMPT_VERSION,
                RERANKER_SCHEMA_VERSION,
            ),
            (
                ModelComponent.ENTAILMENT,
                settings.entailment_model,
                ENTAILMENT_PROMPT_VERSION,
                ENTAILMENT_SCHEMA_VERSION,
            ),
        )
        common = {
            "preprocessing_version": "canonical-document-v1",
            "tool_contract_version": "bounded-research-tools-v2",
        }
        lineages = tuple(
            configured_model_lineage(
                component=component,
                model_id=model_id,
                prompt_version=prompt_version,
                schema_version=schema_version,
                **common,
                settings={
                    "analysis_mode": settings.analysis_mode,
                    "model_id": model_id,
                    "embedding_dim": (
                        settings.embedding_dim if component is ModelComponent.EMBEDDING else None
                    ),
                },
            )
            for component, model_id, prompt_version, schema_version in specifications
        )
        built_embedder = build_embedder(settings, None, usage_log, run_id)
        if not isinstance(built_embedder, OpenAICompatibleEmbedder):
            raise RuntimeError("canonical serving cannot use a fixture hashing embedder")
        return cls(
            extractor=build_structured_extractor(settings, usage_log, run_id),
            reasoner=build_reasoning_model(settings, usage_log, run_id),
            embedder=built_embedder,
            reranker=build_reranker(settings, usage_log, run_id),
            entailment=build_entailment_verifier(settings, usage_log, run_id),
            lineages=lineages,
        )


__all__ = ["ModelBundle"]
