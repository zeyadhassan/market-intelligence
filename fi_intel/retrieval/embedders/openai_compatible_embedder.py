"""Embedding adapter for an OpenAI-compatible ``/v1/embeddings`` API."""

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Literal

import openai

from fi_intel.config import Settings
from fi_intel.governance.model_registry import ModelArtifact, ModelComponent
from fi_intel.governance.model_usage import ModelCallEvent, ModelUsageLog
from fi_intel.logging import get_logger
from fi_intel.retrieval.chunking import Embedder, HashingEmbedder

INDEX_IDENTITY_SCHEMA = "embedding-index-v1"
INPUT_PREPROCESSING_VERSION = "literal-prefix-v1"
SIMILARITY_FUNCTION = "cosine"


class OpenAICompatibleEmbedder:
    """Embedder backed by any OpenAI-compatible /v1/embeddings server."""

    def __init__(
        self,
        client: openai.AsyncOpenAI,
        model: str,
        dim: int,
        query_prefix: str = "",
        document_prefix: str = "",
        artifact: ModelArtifact | None = None,
        usage_log: ModelUsageLog | None = None,
        run_id: str | None = None,
    ) -> None:
        if artifact is not None and (
            artifact.component is not ModelComponent.EMBEDDING or artifact.model_id != model
        ):
            raise ValueError("embedding artifact does not match the configured serving model")
        if (usage_log is None) != (run_id is None):
            raise ValueError("embedding usage_log and run_id must be supplied together")
        self._client = client
        self._model = model
        self._dim = dim
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
        self._artifact = artifact
        self._usage_log = usage_log
        self._run_id = run_id
        identity = json.dumps(
            {
                "dimension": dim,
                "document_prefix": document_prefix,
                "input_preprocessing": INPUT_PREPROCESSING_VERSION,
                "model": model,
                "query_prefix": query_prefix,
                "similarity": SIMILARITY_FUNCTION,
            },
            ensure_ascii=True,
            separators=(",", ":"),
            sort_keys=True,
        )
        digest = hashlib.sha256(identity.encode("utf-8")).hexdigest()
        release = f":release:{artifact.release_id}:{artifact.artifact_digest}" if artifact else ""
        self._model_version = f"{INDEX_IDENTITY_SCHEMA}:{digest}{release}"
        self._log = get_logger(component="retrieval.embedders.openai_compatible")

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        """Stable identity of every setting that defines this vector space.

        ``model`` must be an immutable artifact revision or digest in a
        production deployment. Prefix/preprocessing, dimension, and cosine
        semantics are fingerprinted here so changing any of them forces an
        explicit re-index instead of silently mixing incompatible vectors.
        """
        return self._model_version

    async def embed_batch(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if not texts:
            return []
        # Some embedding models require different query and document prefixes.
        prefix = self._query_prefix if kind == "query" else self._document_prefix
        payload = [prefix + t for t in texts] if prefix else texts

        self._log.info("embed.start", n_texts=len(texts), model=self._model)
        started = time.monotonic()
        subject_id = "embedding-batch:" + hashlib.sha256("\x1f".join(payload).encode()).hexdigest()
        try:
            response = await self._client.embeddings.create(model=self._model, input=payload)
        except Exception as exc:
            await self._record_call(
                subject_id=subject_id,
                latency_ms=(time.monotonic() - started) * 1_000.0,
                input_tokens=0,
                status="timed_out" if isinstance(exc, openai.APITimeoutError) else "failed",
                error_type=type(exc).__name__,
            )
            raise
        await self._record_call(
            subject_id=subject_id,
            latency_ms=(time.monotonic() - started) * 1_000.0,
            input_tokens=response.usage.total_tokens,
            status="succeeded",
        )
        self._log.info("embed.done", n_texts=len(texts), total_tokens=response.usage.total_tokens)
        # Servers document `data` as returned in request order but tagged
        # with `index`; sort defensively rather than trust that.
        by_index = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in by_index]

    async def _record_call(
        self,
        *,
        subject_id: str,
        latency_ms: float,
        input_tokens: int,
        status: Literal["succeeded", "failed", "timed_out"],
        error_type: str | None = None,
    ) -> None:
        if self._usage_log is None or self._run_id is None:
            return
        await self._usage_log.record(
            ModelCallEvent(
                run_id=self._run_id,
                component="embedding",
                model=self._model,
                input_tokens=input_tokens,
                output_tokens=0,
                cost_usd=0.0,
                latency_ms=latency_ms,
                subject_id=subject_id,
                recorded_at=datetime.now(UTC),
                status=status,
                error_type=error_type,
                release_id=self._artifact.release_id if self._artifact else None,
                artifact_digest=(self._artifact.artifact_digest if self._artifact else None),
                prompt_version=INPUT_PREPROCESSING_VERSION,
                schema_version=INDEX_IDENTITY_SCHEMA,
            )
        )


def build_embedder(
    settings: Settings,
    artifact: ModelArtifact | None = None,
    usage_log: ModelUsageLog | None = None,
    run_id: str | None = None,
) -> Embedder:
    """Build the configured embedder or use the deterministic fallback.

    Partial endpoint configuration is rejected.
    """
    base_url = settings.embedding_base_url
    model = settings.embedding_model
    if settings.analysis_mode in {"shadow", "pilot", "production"} and artifact is None:
        raise RuntimeError("governed analysis requires a registry-routed embedding release")
    if base_url is None and model is None:
        if artifact is not None:
            raise RuntimeError("a governed embedding release cannot route to HashingEmbedder")
        return HashingEmbedder()
    if base_url is None or model is None:
        msg = (
            "Partial embedding config: FI_INTEL_EMBEDDING_BASE_URL and "
            "FI_INTEL_EMBEDDING_MODEL must both be set together (or neither, "
            "to keep using the deterministic HashingEmbedder)."
        )
        raise RuntimeError(msg)
    client = openai.AsyncOpenAI(base_url=base_url, api_key=settings.embedding_api_key)
    return OpenAICompatibleEmbedder(
        client=client,
        model=model,
        dim=settings.embedding_dim,
        query_prefix=settings.embedding_query_prefix,
        document_prefix=settings.embedding_document_prefix,
        artifact=artifact,
        usage_log=usage_log,
        run_id=run_id,
    )
