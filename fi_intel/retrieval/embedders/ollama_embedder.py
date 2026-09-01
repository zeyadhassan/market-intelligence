"""Legacy adapter for Ollama's native ``/api/embed`` endpoint.

The canonical governed serving path uses the NVIDIA NIM adapter. This module
remains available only for controlled rollback and fixture compatibility.
"""

from __future__ import annotations

import hashlib
import json
import time
from datetime import UTC, datetime
from typing import Literal

import httpx

from fi_intel.config import Settings
from fi_intel.governance.model_registry import ModelArtifact, ModelComponent
from fi_intel.governance.model_transport import build_ollama_http_client
from fi_intel.governance.model_usage import ModelCallEvent, ModelUsageLog
from fi_intel.logging import get_logger
from fi_intel.retrieval.chunking import Embedder, HashingEmbedder

INDEX_IDENTITY_SCHEMA = "embedding-index-v1"
INPUT_PREPROCESSING_VERSION = "literal-prefix-v1"
SIMILARITY_FUNCTION = "cosine"


class OllamaEmbedder:
    """Batch embedder backed by Ollama's native JSON API."""

    def __init__(
        self,
        client: httpx.AsyncClient,
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
                "provider": "ollama-native-api-v1",
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
        self._log = get_logger(component="retrieval.embedders.ollama")

    @property
    def dim(self) -> int:
        return self._dim

    @property
    def model_version(self) -> str:
        return self._model_version

    async def embed_batch(self, texts: list[str], *, kind: str = "document") -> list[list[float]]:
        if not texts:
            return []
        prefix = self._query_prefix if kind == "query" else self._document_prefix
        payload = [prefix + text for text in texts] if prefix else texts
        subject_id = "embedding-batch:" + hashlib.sha256("\x1f".join(payload).encode()).hexdigest()
        self._log.info("embed.start", n_texts=len(texts), model=self._model)
        started = time.monotonic()
        try:
            response = await self._client.post(
                "embed",
                json={"model": self._model, "input": payload, "truncate": True},
            )
            response.raise_for_status()
            body: object = response.json()
            vectors, input_tokens = self._validated_response(body, len(texts))
        except Exception as exc:
            await self._record_call(
                subject_id=subject_id,
                latency_ms=(time.monotonic() - started) * 1_000.0,
                input_tokens=0,
                status="timed_out" if isinstance(exc, httpx.TimeoutException) else "failed",
                error_type=type(exc).__name__,
            )
            raise
        await self._record_call(
            subject_id=subject_id,
            latency_ms=(time.monotonic() - started) * 1_000.0,
            input_tokens=input_tokens,
            status="succeeded",
        )
        self._log.info("embed.done", n_texts=len(texts), total_tokens=input_tokens)
        return vectors

    def _validated_response(
        self,
        body: object,
        expected_count: int,
    ) -> tuple[list[list[float]], int]:
        if not isinstance(body, dict):
            raise RuntimeError("Ollama embed response must be a JSON object")
        raw_embeddings = body.get("embeddings")
        if not isinstance(raw_embeddings, list) or len(raw_embeddings) != expected_count:
            raise RuntimeError(
                "Ollama embed response count does not match the submitted input count"
            )
        vectors: list[list[float]] = []
        for raw_vector in raw_embeddings:
            if not isinstance(raw_vector, list) or len(raw_vector) != self._dim:
                raise RuntimeError(
                    f"Ollama model returned a vector incompatible with configured dimension "
                    f"{self._dim}"
                )
            if any(
                isinstance(value, bool) or not isinstance(value, (int, float))
                for value in raw_vector
            ):
                raise RuntimeError("Ollama embed response contains a non-numeric vector value")
            vectors.append([float(value) for value in raw_vector])
        raw_tokens = body.get("prompt_eval_count", 0)
        input_tokens = raw_tokens if isinstance(raw_tokens, int) and raw_tokens >= 0 else 0
        return vectors, input_tokens

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
    """Build the legacy Ollama embedder or the fixture-only fallback."""

    base_url = settings.embedding_base_url
    model = settings.embedding_model
    if settings.analysis_mode in {"shadow", "pilot", "production"} and artifact is None:
        raise RuntimeError("governed analysis requires a registry-routed embedding release")
    if base_url is None and model is None:
        if artifact is not None:
            raise RuntimeError("a governed embedding release cannot route to HashingEmbedder")
        return HashingEmbedder()
    if base_url is None or model is None:
        raise RuntimeError(
            "Partial embedding config: FI_INTEL_EMBEDDING_BASE_URL and "
            "FI_INTEL_EMBEDDING_MODEL must both be set together (or neither, "
            "to keep using the deterministic HashingEmbedder)."
        )
    return OllamaEmbedder(
        client=build_ollama_http_client(settings),
        model=model,
        dim=settings.embedding_dim,
        query_prefix=settings.embedding_query_prefix,
        document_prefix=settings.embedding_document_prefix,
        artifact=artifact,
        usage_log=usage_log,
        run_id=run_id,
    )


__all__ = [
    "INDEX_IDENTITY_SCHEMA",
    "INPUT_PREPROCESSING_VERSION",
    "OllamaEmbedder",
    "build_embedder",
]
