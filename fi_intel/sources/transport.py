"""Origin-locked, bounded HTTP acquisition for registered sources."""

from __future__ import annotations

import asyncio
import ipaddress
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from typing import Protocol, runtime_checkable
from urllib.parse import urljoin, urlsplit

import httpx


class SourceTransportError(RuntimeError):
    """A registered-source request failed."""


class DisallowedSourceUrlError(SourceTransportError):
    """A URL or redirect escaped the source's exact origin allowlist."""


class SourceResponseTooLargeError(SourceTransportError):
    """The declared or streamed response exceeded its configured bound."""


class SourceResponseTruncatedError(SourceTransportError):
    """The response ended before its declared Content-Length."""


class RetryableSourceError(SourceTransportError):
    def __init__(self, message: str, retry_after_seconds: float | None = None) -> None:
        super().__init__(message)
        self.retry_after_seconds = retry_after_seconds


@dataclass(frozen=True, slots=True)
class ConditionalRequest:
    etag: str | None = None
    last_modified: str | None = None


@dataclass(frozen=True, slots=True)
class TransportResponse:
    status_code: int
    headers: tuple[tuple[str, str], ...]
    payload: bytes

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((value for key, value in self.headers if key.lower() == lowered), None)


@dataclass(frozen=True, slots=True)
class SourceHttpResponse:
    requested_url: str
    final_url: str
    status_code: int
    headers: tuple[tuple[str, str], ...]
    payload: bytes
    fetched_at: datetime
    redirect_count: int

    @property
    def not_modified(self) -> bool:
        return self.status_code == 304

    def header(self, name: str) -> str | None:
        lowered = name.lower()
        return next((value for key, value in self.headers if key.lower() == lowered), None)


@runtime_checkable
class SourceHttpTransport(Protocol):
    async def send(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> TransportResponse: ...

    async def close(self) -> None: ...


class HttpxSourceTransport:
    """Streaming httpx transport that never follows redirects itself."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self._client = httpx.AsyncClient(transport=transport, follow_redirects=False)

    async def send(
        self,
        url: str,
        headers: Mapping[str, str],
        *,
        timeout_seconds: float,
        max_bytes: int,
    ) -> TransportResponse:
        try:
            async with self._client.stream(
                "GET", url, headers=headers, timeout=timeout_seconds
            ) as response:
                declared = response.headers.get("content-length")
                declared_size = int(declared) if declared is not None else None
                if declared_size is not None and declared_size > max_bytes:
                    raise SourceResponseTooLargeError(
                        f"response declares {declared_size} bytes; limit is {max_bytes}"
                    )
                body = bytearray()
                async for chunk in response.aiter_bytes():
                    body.extend(chunk)
                    if len(body) > max_bytes:
                        raise SourceResponseTooLargeError(
                            f"response exceeded the {max_bytes}-byte limit"
                        )
                if declared_size is not None and len(body) != declared_size:
                    raise SourceResponseTruncatedError(
                        f"response declared {declared_size} bytes but delivered {len(body)}"
                    )
                return TransportResponse(
                    status_code=response.status_code,
                    headers=tuple(response.headers.multi_items()),
                    payload=bytes(body),
                )
        except (SourceResponseTooLargeError, SourceResponseTruncatedError):
            raise
        except httpx.TransportError as exc:
            raise RetryableSourceError(str(exc) or type(exc).__name__) from exc

    async def close(self) -> None:
        await self._client.aclose()


class HardenedSourceClient:
    """HTTP policy enforcement above an injected byte transport."""

    _REDIRECTS = frozenset({301, 302, 303, 307, 308})

    def __init__(
        self,
        transport: SourceHttpTransport,
        *,
        allowed_origins: tuple[str, ...],
        user_agent: str,
        timeout_seconds: float,
        max_attempts: int,
        max_redirects: int,
        clock: Callable[[], datetime] | None = None,
        sleep: Callable[[float], Awaitable[None]] | None = None,
    ) -> None:
        if not user_agent.strip():
            raise ValueError("source HTTP user agent cannot be blank")
        if timeout_seconds <= 0 or max_attempts < 1 or max_redirects < 0:
            raise ValueError("source HTTP limits are invalid")
        self._transport = transport
        self._allowed_origins = frozenset(_normalize_origin(item) for item in allowed_origins)
        if not self._allowed_origins:
            raise ValueError("at least one source origin must be allowed")
        self._user_agent = user_agent
        self._timeout_seconds = timeout_seconds
        self._max_attempts = max_attempts
        self._max_redirects = max_redirects
        self._clock = clock or (lambda: datetime.now(UTC))
        self._sleep = sleep or asyncio.sleep

    async def fetch(
        self,
        url: str,
        *,
        max_bytes: int,
        conditional: ConditionalRequest | None = None,
        accept: str = "*/*",
    ) -> SourceHttpResponse:
        self.validate_url(url)
        if max_bytes < 1:
            raise ValueError("max_bytes must be positive")
        headers = {"Accept": accept, "User-Agent": self._user_agent}
        if conditional is not None:
            if conditional.etag:
                headers["If-None-Match"] = conditional.etag
            if conditional.last_modified:
                headers["If-Modified-Since"] = conditional.last_modified

        last_error: RetryableSourceError | None = None
        for attempt in range(1, self._max_attempts + 1):
            try:
                return await self._fetch_once(url, headers, max_bytes)
            except RetryableSourceError as exc:
                last_error = exc
                if attempt == self._max_attempts:
                    break
                delay = exc.retry_after_seconds
                await self._sleep(delay if delay is not None else min(2 ** (attempt - 1), 8))
        if last_error is None:
            raise SourceTransportError("source request exhausted without a response")
        raise last_error

    async def _fetch_once(
        self, url: str, headers: Mapping[str, str], max_bytes: int
    ) -> SourceHttpResponse:
        current = url
        redirects = 0
        while True:
            response = await self._transport.send(
                current,
                headers,
                timeout_seconds=self._timeout_seconds,
                max_bytes=max_bytes,
            )
            if response.status_code in self._REDIRECTS:
                location = response.header("location")
                if location is None:
                    raise SourceTransportError("redirect response omitted Location")
                if redirects >= self._max_redirects:
                    raise SourceTransportError("source exceeded its redirect limit")
                candidate = urljoin(current, location)
                self.validate_url(candidate)
                current = candidate
                redirects += 1
                continue
            if response.status_code in {429, 500, 502, 503, 504}:
                raise RetryableSourceError(
                    f"source returned HTTP {response.status_code}",
                    _retry_after(response.header("retry-after"), self._clock()),
                )
            if response.status_code not in {200, 304}:
                raise SourceTransportError(f"source returned HTTP {response.status_code}")
            if response.status_code == 304 and response.payload:
                raise SourceTransportError("304 response unexpectedly contained a body")
            return SourceHttpResponse(
                requested_url=url,
                final_url=current,
                status_code=response.status_code,
                headers=response.headers,
                payload=response.payload,
                fetched_at=self._aware_now(),
                redirect_count=redirects,
            )

    def validate_url(self, url: str) -> None:
        if any(ord(char) < 32 for char in url) or "\\" in url:
            raise DisallowedSourceUrlError("source URL contains unsafe characters")
        parsed = urlsplit(url)
        if parsed.scheme.lower() != "https" or parsed.hostname is None:
            raise DisallowedSourceUrlError("source URL must be absolute HTTPS")
        if parsed.username is not None or parsed.password is not None or parsed.fragment:
            raise DisallowedSourceUrlError("source URL cannot contain credentials or a fragment")
        origin = _origin(parsed.scheme, parsed.hostname, parsed.port)
        if origin not in self._allowed_origins:
            raise DisallowedSourceUrlError(f"source origin is not allowed: {origin}")

    async def close(self) -> None:
        await self._transport.close()

    def _aware_now(self) -> datetime:
        value = self._clock()
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("source HTTP clock must return an aware datetime")
        return value


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("allowed origins cannot contain paths, queries, or fragments")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("allowed origins cannot contain credentials")
    if parsed.scheme.lower() != "https" or parsed.hostname is None:
        raise ValueError("allowed origins must use HTTPS")
    return _origin(parsed.scheme, parsed.hostname, parsed.port)


def _origin(scheme: str, hostname: str, port: int | None) -> str:
    host = hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise DisallowedSourceUrlError("literal IP source origins are forbidden")
    suffix = "" if port in {None, 443} else f":{port}"
    return f"{scheme.lower()}://{host}{suffix}"


def _retry_after(value: str | None, now: datetime) -> float | None:
    if value is None:
        return None
    try:
        return min(max(float(value), 0.0), 60.0)
    except ValueError:
        try:
            target = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if target.tzinfo is None:
            return None
        return min(max((target - now).total_seconds(), 0.0), 60.0)
