"""Versioned registrations for every network-capable source."""

from __future__ import annotations

import ipaddress
from enum import StrEnum
from urllib.parse import urlsplit

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fi_intel.config import Settings
from fi_intel.sources.canonical import BarrierSide


class SourceKind(StrEnum):
    FEED_DETAIL = "feed_detail"
    REFERENCE_API = "reference_api"
    REFERENCE_BULK = "reference_bulk"


class LicenceClass(StrEnum):
    OPEN_GOVERNMENT = "open_government"
    OPEN_REFERENCE = "open_reference"
    LICENSED_VENDOR = "licensed_vendor"


class SourceRegistration(BaseModel):
    """Operational and policy contract for one source version."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    catalog_version: str = Field(min_length=1)
    display_name: str = Field(min_length=1)
    kind: SourceKind
    discovery_url: str = Field(min_length=1)
    allowed_origins: tuple[str, ...] = Field(min_length=1)
    cadence_seconds: int = Field(gt=0)
    freshness_sla_seconds: int = Field(gt=0)
    silence_sla_seconds: int = Field(gt=0)
    expected_min_items: int = Field(ge=0)
    expected_max_items: int = Field(gt=0)
    licence_group: str = Field(min_length=1)
    licence_class: LicenceClass
    raw_retention_days: int = Field(gt=0)
    barrier_side: BarrierSide = BarrierSide.PUBLIC
    allowed_entitlement_groups: frozenset[str] = Field(min_length=1)
    max_feed_bytes: int = Field(gt=0)
    max_detail_bytes: int = Field(gt=0)
    request_timeout_seconds: float = Field(gt=0)
    max_attempts: int = Field(ge=1, le=10)
    max_redirects: int = Field(ge=0, le=10)
    cursor_history_limit: int = Field(ge=1)
    enabled: bool = True

    @model_validator(mode="after")
    def _validate_registration(self) -> SourceRegistration:
        normalized = tuple(_normalize_origin(item) for item in self.allowed_origins)
        if len(set(normalized)) != len(normalized):
            raise ValueError("allowed source origins must be unique")
        discovery_origin = _origin_for_url(self.discovery_url)
        if discovery_origin not in normalized:
            raise ValueError("discovery URL origin is not in allowed_origins")
        if self.freshness_sla_seconds < self.cadence_seconds:
            raise ValueError("freshness SLA cannot be shorter than source cadence")
        if self.silence_sla_seconds < self.freshness_sla_seconds:
            raise ValueError("silence SLA cannot be shorter than freshness SLA")
        if self.expected_max_items < self.expected_min_items:
            raise ValueError("expected maximum volume is below expected minimum")
        if self.max_detail_bytes < self.max_feed_bytes:
            raise ValueError("detail byte limit cannot be smaller than feed byte limit")
        return self


class SourceCatalog:
    """Immutable lookup that rejects duplicate registrations."""

    def __init__(self, registrations: tuple[SourceRegistration, ...]) -> None:
        by_id: dict[str, SourceRegistration] = {}
        for registration in registrations:
            if registration.source_id in by_id:
                raise ValueError(f"duplicate source registration: {registration.source_id}")
            by_id[registration.source_id] = registration
        self._registrations = by_id

    def require(self, source_id: str) -> SourceRegistration:
        try:
            return self._registrations[source_id]
        except KeyError as exc:
            raise KeyError(f"source {source_id!r} is not registered") from exc

    def enabled(self) -> tuple[SourceRegistration, ...]:
        return tuple(
            item
            for item in sorted(self._registrations.values(), key=lambda row: row.source_id)
            if item.enabled
        )


def production_source_catalog(settings: Settings | None = None) -> SourceCatalog:
    """Return the checked-in production-source policy catalog."""
    active = settings or Settings()
    return SourceCatalog(
        (
            SourceRegistration(
                source_id="sec_edgar_8k",
                catalog_version="source-catalog-v1",
                display_name="SEC EDGAR current 8-K filings",
                kind=SourceKind.FEED_DETAIL,
                discovery_url=active.sec_edgar_feed_url,
                allowed_origins=("https://www.sec.gov",),
                cadence_seconds=300,
                freshness_sla_seconds=1_800,
                silence_sla_seconds=259_200,
                expected_min_items=0,
                expected_max_items=500,
                licence_group="open_web_public",
                licence_class=LicenceClass.OPEN_GOVERNMENT,
                raw_retention_days=active.source_raw_retention_days,
                barrier_side=BarrierSide.PUBLIC,
                allowed_entitlement_groups=frozenset(
                    {"fi_gcc_public", "open_web_public"}
                ),
                max_feed_bytes=active.source_max_feed_bytes,
                max_detail_bytes=active.source_max_detail_bytes,
                request_timeout_seconds=active.source_http_timeout_seconds,
                max_attempts=active.source_http_max_attempts,
                max_redirects=active.source_http_max_redirects,
                cursor_history_limit=active.source_cursor_history_limit,
                enabled=active.enable_sec_edgar_source,
            ),
            SourceRegistration(
                source_id="fed_press_releases",
                catalog_version="source-catalog-v1",
                display_name="Federal Reserve press releases",
                kind=SourceKind.FEED_DETAIL,
                discovery_url=active.fed_press_feed_url,
                allowed_origins=("https://www.federalreserve.gov",),
                cadence_seconds=900,
                freshness_sla_seconds=3_600,
                silence_sla_seconds=604_800,
                expected_min_items=0,
                expected_max_items=100,
                licence_group="open_web_public",
                licence_class=LicenceClass.OPEN_GOVERNMENT,
                raw_retention_days=active.source_raw_retention_days,
                barrier_side=BarrierSide.PUBLIC,
                allowed_entitlement_groups=frozenset(
                    {"fi_gcc_public", "open_web_public"}
                ),
                max_feed_bytes=active.source_max_feed_bytes,
                max_detail_bytes=active.source_max_detail_bytes,
                request_timeout_seconds=active.source_http_timeout_seconds,
                max_attempts=active.source_http_max_attempts,
                max_redirects=active.source_http_max_redirects,
                cursor_history_limit=active.source_cursor_history_limit,
                enabled=active.enable_fed_press_source,
            ),
            SourceRegistration(
                source_id="gleif",
                catalog_version="source-catalog-v1",
                display_name="GLEIF LEI reference data",
                kind=SourceKind.REFERENCE_API,
                discovery_url=active.gleif_api_url,
                allowed_origins=(
                    "https://api.gleif.org",
                    "https://goldencopy.gleif.org",
                ),
                cadence_seconds=28_800,
                freshness_sla_seconds=43_200,
                silence_sla_seconds=86_400,
                expected_min_items=1,
                expected_max_items=active.gleif_page_size * active.gleif_max_pages,
                licence_group="open_reference",
                licence_class=LicenceClass.OPEN_REFERENCE,
                raw_retention_days=active.source_raw_retention_days,
                barrier_side=BarrierSide.PUBLIC,
                allowed_entitlement_groups=frozenset(
                    {"fi_gcc_private", "fi_gcc_public", "open_reference"}
                ),
                max_feed_bytes=active.source_max_feed_bytes,
                max_detail_bytes=active.source_max_detail_bytes,
                request_timeout_seconds=active.source_http_timeout_seconds,
                max_attempts=active.source_http_max_attempts,
                max_redirects=active.source_http_max_redirects,
                cursor_history_limit=active.source_cursor_history_limit,
            ),
        )
    )


def _normalize_origin(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.scheme.lower() != "https" or parsed.hostname is None:
        raise ValueError("source origins must be absolute HTTPS origins")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("source origins cannot contain credentials")
    if parsed.path not in {"", "/"} or parsed.query or parsed.fragment:
        raise ValueError("allowed origin cannot contain a path, query, or fragment")
    host = parsed.hostname.rstrip(".").lower()
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise ValueError("literal IP addresses cannot be registered as source origins")
    port = parsed.port
    suffix = "" if port in {None, 443} else f":{port}"
    return f"https://{host}{suffix}"


def _origin_for_url(value: str) -> str:
    parsed = urlsplit(value)
    if parsed.username is not None or parsed.password is not None or parsed.fragment:
        raise ValueError("source URL contains forbidden credentials or fragment")
    if parsed.hostname is None:
        raise ValueError("source URL must be absolute")
    host = parsed.hostname.rstrip(".").lower()
    port = parsed.port
    suffix = "" if port in {None, 443} else f":{port}"
    return f"{parsed.scheme.lower()}://{host}{suffix}"
