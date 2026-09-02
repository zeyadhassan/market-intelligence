"""Security and boundedness contracts for registered-source HTTP."""

import gzip
from datetime import UTC, datetime

import httpx
import pytest

from fi_intel.logging import safe_error_summary
from fi_intel.sources.transport import (
    ConditionalRequest,
    DisallowedSourceUrlError,
    HardenedSourceClient,
    HttpxSourceTransport,
    RetryableSourceError,
    SourceResponseTooLargeError,
)
from tests.source_support import ScriptedSourceTransport, source_response

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)


def _client(transport: ScriptedSourceTransport) -> HardenedSourceClient:
    async def no_sleep(delay: float) -> None:
        del delay

    return HardenedSourceClient(
        transport,
        allowed_origins=("https://www.sec.gov",),
        user_agent="fi-intel-test test@example.invalid",
        timeout_seconds=2,
        max_attempts=2,
        max_redirects=2,
        clock=lambda: NOW,
        sleep=no_sleep,
    )


async def test_initial_and_redirect_urls_cannot_escape_registered_origin() -> None:
    transport = ScriptedSourceTransport([])
    client = _client(transport)

    with pytest.raises(DisallowedSourceUrlError):
        await client.fetch("http://127.0.0.1/latest", max_bytes=100)
    with pytest.raises(DisallowedSourceUrlError):
        await client.fetch("https://www.sec.gov.evil.example/latest", max_bytes=100)
    assert transport.requests == []

    allowed = "https://www.sec.gov/feed"
    transport = ScriptedSourceTransport(
        [
            (
                allowed,
                source_response(
                    302,
                    headers=(("location", "http://169.254.169.254/latest/meta-data"),),
                ),
            )
        ]
    )
    client = _client(transport)
    with pytest.raises(DisallowedSourceUrlError):
        await client.fetch(allowed, max_bytes=100)
    assert len(transport.requests) == 1


async def test_conditional_headers_and_transient_retry_are_bounded() -> None:
    url = "https://www.sec.gov/feed"
    transport = ScriptedSourceTransport(
        [
            (url, source_response(503, headers=(("retry-after", "0"),))),
            (url, source_response(304)),
        ]
    )
    response = await _client(transport).fetch(
        url,
        max_bytes=100,
        conditional=ConditionalRequest(etag='"feed-v1"', last_modified="yesterday"),
    )

    assert response.not_modified
    assert len(transport.requests) == 2
    for _, headers, timeout, byte_limit in transport.requests:
        assert headers["If-None-Match"] == '"feed-v1"'
        assert headers["If-Modified-Since"] == "yesterday"
        assert timeout == 2
        assert byte_limit == 100


async def test_httpx_transport_rejects_oversize_but_accepts_length_mismatch() -> None:
    oversize = httpx.MockTransport(
        lambda request: httpx.Response(200, content=b"12345", request=request)
    )
    transport = HttpxSourceTransport(oversize)
    with pytest.raises(SourceResponseTooLargeError):
        await transport.send("https://www.sec.gov/x", {}, timeout_seconds=1, max_bytes=4)
    await transport.close()

    truncated = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=b"abc",
            headers={"content-length": "10"},
            request=request,
        )
    )
    transport = HttpxSourceTransport(truncated)
    response = await transport.send(
        "https://www.sec.gov/x", {}, timeout_seconds=1, max_bytes=20
    )
    assert response.payload == b"abc"
    await transport.close()


@pytest.mark.parametrize(
    ("message", "reason"),
    (
        ("407 Proxy Authentication Required", "proxy_authentication_required"),
        ("[Errno -2] Name or service not known", "dns_resolution_failed"),
        (
            "[SSL: CERTIFICATE_VERIFY_FAILED] certificate verify failed",
            "tls_certificate_verification_failed",
        ),
    ),
)
def test_source_network_failures_have_safe_actionable_reasons(
    message: str, reason: str
) -> None:
    summary = safe_error_summary(RetryableSourceError(message))

    assert f"reason={reason}" in summary
    assert message not in summary


async def test_httpx_transport_validates_wire_length_for_compressed_responses() -> None:
    decoded = b"a valid compressed response body" * 20
    encoded = gzip.compress(decoded)
    compressed = httpx.MockTransport(
        lambda request: httpx.Response(
            200,
            content=encoded,
            headers={
                "content-encoding": "gzip",
                "content-length": str(len(encoded)),
            },
            request=request,
        )
    )
    transport = HttpxSourceTransport(compressed)

    response = await transport.send(
        "https://www.sec.gov/x", {}, timeout_seconds=1, max_bytes=len(decoded)
    )

    assert response.payload == decoded
    await transport.close()
