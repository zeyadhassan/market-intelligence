"""Verify the configured chat and embedding endpoints without printing secrets."""

from __future__ import annotations

import argparse
import asyncio
import sys

from fi_intel.config import Settings
from fi_intel.governance.model_transport import build_embedding_http_client, build_llm_client
from fi_intel.retrieval.embedders.openai_compatible_embedder import OpenAICompatibleEmbedder

try:
    from deploy.podman_infra import _load_app_environment
except ModuleNotFoundError:  # Direct execution: python deploy/model_smoke.py
    from podman_infra import _load_app_environment

MODEL_FIELDS = {
    "llm_base_url",
    "llm_api_key",
    "llm_basic_auth_username",
    "llm_basic_auth_password",
    "extraction_model",
    "embedding_base_url",
    "embedding_api_key",
    "embedding_basic_auth_username",
    "embedding_basic_auth_password",
    "embedding_model",
}


def _settings_from_app_env() -> Settings:
    environment = _load_app_environment(required=True)
    values = {
        field_name: environment[f"FI_INTEL_{field_name.upper()}"]
        for field_name in Settings.model_fields
        if f"FI_INTEL_{field_name.upper()}" in environment
    }
    for field_name in MODEL_FIELDS:
        value = values.get(field_name)
        if isinstance(value, str) and "replace_with" in value.casefold():
            raise RuntimeError(f"FI_INTEL_{field_name.upper()} still contains a placeholder")
    return Settings.model_validate(values)


async def smoke_chat(settings: Settings) -> None:
    print("Checking OpenAI-compatible chat endpoint ...", flush=True)
    llm = build_llm_client(settings)
    try:
        await llm.chat.completions.create(
            model=settings.extraction_model,
            messages=[{"role": "user", "content": "Reply with OK."}],
            temperature=0.0,
            max_tokens=8,
        )
    finally:
        await llm.close()
    print("Chat endpoint OK", flush=True)


async def smoke_embedding(settings: Settings) -> None:
    if settings.embedding_model is None:
        raise RuntimeError("FI_INTEL_EMBEDDING_MODEL is required")
    print("Checking NVIDIA NIM embedding endpoint ...", flush=True)
    client = build_embedding_http_client(settings)
    try:
        embedder = OpenAICompatibleEmbedder(
            client,
            model=settings.embedding_model,
            dim=settings.embedding_dim,
            query_prefix=settings.embedding_query_prefix,
            document_prefix=settings.embedding_document_prefix,
        )
        vectors = await embedder.embed_batch(["connectivity check"], kind="query")
    finally:
        await client.aclose()
    print(f"NVIDIA NIM embedding endpoint OK ({len(vectors[0])} dimensions)", flush=True)


async def _smoke(settings: Settings, *, chat: bool = True, embedding: bool = True) -> None:
    if chat:
        await smoke_chat(settings)
    if embedding:
        await smoke_embedding(settings)


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--chat-only", action="store_true")
    selection.add_argument("--embedding-only", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        asyncio.run(
            _smoke(
                _settings_from_app_env(),
                chat=not args.embedding_only,
                embedding=not args.chat_only,
            )
        )
    except Exception as exc:
        print(f"Model smoke check failed: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
