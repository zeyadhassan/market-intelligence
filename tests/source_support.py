"""Deterministic network-free source transport fixtures."""

from __future__ import annotations

from collections.abc import Mapping

from fi_intel.sources.transport import SourceHttpTransport, TransportResponse


def source_response(
    status_code: int,
    payload: bytes = b"",
    *,
    headers: tuple[tuple[str, str], ...] = (),
) -> TransportResponse:
    return TransportResponse(status_code=status_code, headers=headers, payload=payload)


class ScriptedSourceTransport(SourceHttpTransport):
    def __init__(self, exchanges: list[tuple[str, TransportResponse | Exception]]) -> None:
        self._exchanges = list(exchanges)
        self.requests: list[tuple[str, dict[str, str], float, int]] = []
        self.closed = False

    async def send(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> TransportResponse:
        self.requests.append((url, dict(headers), timeout_seconds, max_bytes))
        if not self._exchanges:
            raise AssertionError(f"unexpected source request: {url}")
        expected_url, result = self._exchanges.pop(0)
        if url != expected_url:
            raise AssertionError(f"expected request {expected_url!r}, got {url!r}")
        if isinstance(result, Exception):
            raise result
        return result

    async def close(self) -> None:
        self.closed = True

    def assert_exhausted(self) -> None:
        assert self._exchanges == []
