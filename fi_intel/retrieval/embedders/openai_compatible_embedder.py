"""Embedding adapter for an OpenAI-compatible ``/v1/embeddings`` API."""

import hashlib
import json

import openai

from fi_intel.config import Settings
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
    ) -> None:
        self._client = client
        self._model = model
        self._dim = dim
        self._query_prefix = query_prefix
        self._document_prefix = document_prefix
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
        self._model_version = f"{INDEX_IDENTITY_SCHEMA}:{digest}"
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
        response = await self._client.embeddings.create(model=self._model, input=payload)
        self._log.info("embed.done", n_texts=len(texts), total_tokens=response.usage.total_tokens)
        # Servers document `data` as returned in request order but tagged
        # with `index`; sort defensively rather than trust that.
        by_index = sorted(response.data, key=lambda d: d.index)
        return [d.embedding for d in by_index]


def build_embedder(settings: Settings) -> Embedder:
    """Build the configured embedder or use the deterministic fallback.

    Partial endpoint configuration is rejected.
    """
    base_url = settings.embedding_base_url
    model = settings.embedding_model
    if base_url is None and model is None:
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
    )
