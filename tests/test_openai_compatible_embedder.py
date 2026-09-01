"""Transport-level tests for the OpenAI-compatible embedding adapter."""

import json

import httpx
import pytest

from fi_intel.config import Settings
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.embedders.openai_compatible_embedder import (
    OpenAICompatibleEmbedder,
    build_embedder,
)


def _embedder_returning(
    vectors: list[list[float]], **kwargs: object
) -> tuple[OpenAICompatibleEmbedder, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        data = [{"object": "embedding", "embedding": v, "index": i} for i, v in enumerate(vectors)]
        body = {
            "object": "list",
            "data": data,
            "model": "local-embedder",
            "usage": {"prompt_tokens": 42, "total_tokens": 42},
        }
        return httpx.Response(200, json=body)

    client = httpx.AsyncClient(
        base_url="http://localhost:9998/v1",
        transport=httpx.MockTransport(handler),
    )
    dim = len(vectors[0]) if vectors else 0
    embedder = OpenAICompatibleEmbedder(client, model="local-embedder", dim=dim, **kwargs)
    return embedder, captured


async def test_embed_batch_documents_uses_document_prefix_and_parses_vectors() -> None:
    embedder, captured = _embedder_returning(
        [[0.1, 0.2], [0.3, 0.4]], document_prefix="passage: ", query_prefix="query: "
    )

    result = await embedder.embed_batch(["chunk one", "chunk two"], kind="document")

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    (request,) = captured
    body = json.loads(request.content)
    assert request.url.path == "/v1/embeddings"
    assert body == {
        "input": ["passage: chunk one", "passage: chunk two"],
        "model": "local-embedder",
        "input_type": "passage",
        "modality": "text",
        "encoding_format": "float",
    }


async def test_embed_batch_query_uses_query_prefix() -> None:
    embedder, captured = _embedder_returning(
        [[0.5, 0.6]], document_prefix="passage: ", query_prefix="query: "
    )

    await embedder.embed_batch(["what is the maturity date?"], kind="query")

    (request,) = captured
    body = json.loads(request.content)
    assert body["input"] == ["query: what is the maturity date?"]
    assert body["input_type"] == "query"


async def test_no_prefix_by_default() -> None:
    """No embedding model is chosen yet, so prefixes default to blank —
    symmetric treatment until a model that needs them is configured."""
    embedder, captured = _embedder_returning([[1.0]])
    await embedder.embed_batch(["plain text"], kind="query")
    assert json.loads(captured[0].content)["input"] == ["plain text"]


async def test_embed_batch_empty_input_makes_no_request() -> None:
    embedder, captured = _embedder_returning([])
    result = await embedder.embed_batch([])
    assert result == []
    assert captured == []


async def test_embed_batch_sorts_by_response_index_defensively() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        data = [
            {"object": "embedding", "embedding": [9.0], "index": 1},
            {"object": "embedding", "embedding": [1.0], "index": 0},
        ]
        return httpx.Response(
            200,
            json={
                "object": "list",
                "data": data,
                "model": "m",
                "usage": {"prompt_tokens": 1, "total_tokens": 1},
            },
        )

    client = httpx.AsyncClient(
        base_url="http://localhost:9998/v1",
        transport=httpx.MockTransport(handler),
    )
    embedder = OpenAICompatibleEmbedder(client, model="m", dim=1)
    result = await embedder.embed_batch(["first", "second"])
    assert result == [[1.0], [9.0]]


async def test_dim_and_model_version() -> None:
    client = httpx.AsyncClient(base_url="http://localhost:9998/v1")
    embedder = OpenAICompatibleEmbedder(client, model="local-embedder", dim=2048)
    assert embedder.dim == 2048
    assert embedder.model_version.startswith("embedding-index-v1:")


async def test_model_version_fingerprints_vector_space_configuration() -> None:
    baseline, _ = _embedder_returning([[1.0]], query_prefix="query: ")
    same, _ = _embedder_returning([[1.0]], query_prefix="query: ")
    changed_query_prefix, _ = _embedder_returning([[1.0]], query_prefix="search: ")
    changed_document_prefix, _ = _embedder_returning([[1.0]], document_prefix="passage: ")

    assert baseline.model_version == same.model_version
    assert changed_query_prefix.model_version != baseline.model_version
    assert changed_document_prefix.model_version != baseline.model_version


async def test_embed_batch_rejects_wrong_dimension() -> None:
    embedder, _ = _embedder_returning([[0.1, 0.2]])
    embedder._dim = 3

    with pytest.raises(RuntimeError, match="configured dimension 3"):
        await embedder.embed_batch(["text"])


def test_build_embedder_falls_back_to_hashing_when_unconfigured() -> None:
    settings = Settings(analysis_mode="fixture", embedding_base_url=None, embedding_model=None)
    embedder = build_embedder(settings)
    assert isinstance(embedder, HashingEmbedder)


def test_build_embedder_returns_real_embedder_when_configured() -> None:
    settings = Settings(
        analysis_mode="fixture",
        embedding_base_url="http://localhost:9998/v1",
        embedding_model="local-embedder",
        embedding_dim=2048,
    )
    embedder = build_embedder(settings)
    assert isinstance(embedder, OpenAICompatibleEmbedder)
    assert embedder.model_version.startswith("embedding-index-v1:")
    assert embedder.dim == 2048


def test_build_embedder_raises_on_partial_config() -> None:
    settings = Settings(
        analysis_mode="fixture",
        embedding_base_url="http://localhost:9998/v1",
        embedding_model=None,
    )
    try:
        build_embedder(settings)
        raise AssertionError("expected RuntimeError")
    except RuntimeError as exc:
        assert "must both be set together" in str(exc)
