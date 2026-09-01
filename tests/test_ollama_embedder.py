"""Transport and response contracts for native Ollama embeddings."""

import base64
import json

import httpx
import pytest

from fi_intel.config import Settings
from fi_intel.retrieval.chunking import HashingEmbedder
from fi_intel.retrieval.embedders.ollama_embedder import OllamaEmbedder, build_embedder


def _embedder_returning(
    vectors: list[list[float]],
    **kwargs: object,
) -> tuple[OllamaEmbedder, list[httpx.Request]]:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"model": "nomic-embed-text:v1.5", "embeddings": vectors, "prompt_eval_count": 7},
        )

    client = httpx.AsyncClient(
        base_url="https://ollama.example/ollama/api/",
        transport=httpx.MockTransport(handler),
    )
    dim = len(vectors[0]) if vectors else 0
    return (
        OllamaEmbedder(client, model="nomic-embed-text:v1.5", dim=dim, **kwargs),
        captured,
    )


async def test_native_embed_uses_task_prefix_and_api_path() -> None:
    embedder, captured = _embedder_returning(
        [[0.1, 0.2]],
        query_prefix="search_query: ",
        document_prefix="search_document: ",
    )

    result = await embedder.embed_batch(["capital ratio"], kind="query")

    assert result == [[0.1, 0.2]]
    assert captured[0].url.path == "/ollama/api/embed"
    assert json.loads(captured[0].content) == {
        "model": "nomic-embed-text:v1.5",
        "input": ["search_query: capital ratio"],
        "truncate": True,
    }


async def test_native_embed_rejects_wrong_dimension() -> None:
    embedder, _ = _embedder_returning([[0.1, 0.2]])
    embedder._dim = 3

    with pytest.raises(RuntimeError, match="configured dimension 3"):
        await embedder.embed_batch(["text"])


async def test_ollama_transport_sends_basic_auth_without_proxy_inheritance() -> None:
    captured: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(request)
        return httpx.Response(
            200,
            json={"embeddings": [[0.0] * 768], "prompt_eval_count": 1},
        )

    settings = Settings(
        analysis_mode="fixture",
        embedding_base_url="https://ollama.example/ollama/api/",
        embedding_model="nomic-embed-text:v1.5",
        embedding_dim=768,
        embedding_basic_auth_username="ollama",
        embedding_basic_auth_password="rotated-password",  # noqa: S106 - test credential
        embedding_trust_env=False,
    )
    embedder = build_embedder(settings)
    assert isinstance(embedder, OllamaEmbedder)
    await embedder._client.aclose()
    embedder._client = httpx.AsyncClient(
        base_url="https://ollama.example/ollama/api/",
        auth=httpx.BasicAuth("ollama", "rotated-password"),
        trust_env=False,
        transport=httpx.MockTransport(handler),
    )

    await embedder.embed_batch(["text"])

    expected = base64.b64encode(b"ollama:rotated-password").decode()
    assert captured[0].headers["Authorization"] == f"Basic {expected}"


def test_build_embedder_falls_back_only_for_unconfigured_fixture() -> None:
    settings = Settings(analysis_mode="fixture", embedding_base_url=None, embedding_model=None)

    assert isinstance(build_embedder(settings), HashingEmbedder)
