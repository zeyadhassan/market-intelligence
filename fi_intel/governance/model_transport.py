"""Explicit transport policy for internal model gateways."""

from __future__ import annotations

import httpx
import openai
from pydantic import SecretStr

from fi_intel.config import Settings


def _secret_value(value: SecretStr | None) -> str | None:
    return value.get_secret_value() if value is not None else None


def _basic_auth(
    username: str | None,
    password: SecretStr | None,
    *,
    setting_prefix: str,
) -> tuple[str, str] | None:
    secret = _secret_value(password)
    if bool(username) != bool(secret):
        raise RuntimeError(
            f"{setting_prefix}_BASIC_AUTH_USERNAME and "
            f"{setting_prefix}_BASIC_AUTH_PASSWORD must be configured together"
        )
    return (username, secret) if username is not None and secret is not None else None


def build_llm_client(settings: Settings) -> openai.AsyncOpenAI:
    """Build the shared chat client without implicit proxy/TLS behavior."""

    if settings.llm_base_url is None:
        raise RuntimeError("FI_INTEL_LLM_BASE_URL is required")
    auth = _basic_auth(
        settings.llm_basic_auth_username,
        settings.llm_basic_auth_password,
        setting_prefix="FI_INTEL_LLM",
    )
    http_client = openai.DefaultAsyncHttpxClient(
        auth=auth,
        verify=settings.llm_tls_verify,
        trust_env=settings.llm_trust_env,
        timeout=settings.llm_timeout_seconds,
    )
    return openai.AsyncOpenAI(
        base_url=settings.llm_base_url,
        api_key=settings.llm_api_key,
        http_client=http_client,
    )


def build_ollama_http_client(settings: Settings) -> httpx.AsyncClient:
    """Build a native Ollama client rooted at the configured ``/api/`` URL."""

    if settings.embedding_base_url is None:
        raise RuntimeError("FI_INTEL_EMBEDDING_BASE_URL is required")
    auth = _basic_auth(
        settings.embedding_basic_auth_username,
        settings.embedding_basic_auth_password,
        setting_prefix="FI_INTEL_EMBEDDING",
    )
    headers: dict[str, str] = {"Accept": "application/json"}
    if auth is None and settings.embedding_api_key != "not-needed":
        headers["Authorization"] = f"Bearer {settings.embedding_api_key}"
    return httpx.AsyncClient(
        base_url=settings.embedding_base_url.rstrip("/") + "/",
        auth=httpx.BasicAuth(*auth) if auth is not None else None,
        headers=headers,
        verify=settings.embedding_tls_verify,
        trust_env=settings.embedding_trust_env,
        timeout=settings.embedding_timeout_seconds,
    )


__all__ = ["build_llm_client", "build_ollama_http_client"]
