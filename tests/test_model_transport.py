"""Model gateway transport settings must not be inherited implicitly."""

import httpx2
import openai
import pytest

from fi_intel.config import Settings
from fi_intel.governance.model_transport import build_llm_client


async def test_llm_client_uses_explicit_direct_transport(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured: dict[str, object] = {}

    def client_factory(**kwargs: object) -> httpx2.AsyncClient:
        captured.update(kwargs)
        return httpx2.AsyncClient(transport=httpx2.MockTransport(lambda _request: None))

    monkeypatch.setattr(openai, "DefaultAsyncHttpxClient", client_factory)
    settings = Settings(
        analysis_mode="fixture",
        llm_base_url="https://llm.example/v1",
        llm_trust_env=False,
        llm_tls_verify=False,
        llm_timeout_seconds=120,
        llm_basic_auth_username="gateway-user",
        llm_basic_auth_password="test-only-password",  # noqa: S106
    )

    client = build_llm_client(settings)

    assert captured == {
        "auth": ("gateway-user", "test-only-password"),
        "verify": False,
        "trust_env": False,
        "timeout": 120.0,
    }
    await client.close()


def test_llm_client_rejects_partial_basic_auth() -> None:
    settings = Settings(
        analysis_mode="fixture",
        llm_base_url="https://llm.example/v1",
        llm_basic_auth_username="gateway-user",
    )

    with pytest.raises(RuntimeError, match="must be configured together"):
        build_llm_client(settings)
