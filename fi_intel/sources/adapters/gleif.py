"""Registered GLEIF API/bulk adapters and strict entity reference records."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any, Self, cast

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from fi_intel.application.raw import RawHeader, RawSourceEnvelope
from fi_intel.config import Settings
from fi_intel.ledger.models import AccessPolicy
from fi_intel.sources.acquisition import (
    DetailValidator,
    RawAcquiredItem,
    RawSourceCursor,
    RawSourcePoll,
    cursor_position,
)
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass
from fi_intel.sources.catalog import SourceKind, SourceRegistration, production_source_catalog
from fi_intel.sources.fixture import FixtureAdapter
from fi_intel.sources.transport import (
    ConditionalRequest,
    HardenedSourceClient,
    HttpxSourceTransport,
    SourceHttpResponse,
    SourceHttpTransport,
    SourceTransportError,
)

_JURISDICTION = re.compile(r"^[A-Z]{2}(?:-[A-Z0-9]{1,3})?$")


class MalformedGleifRecordError(ValueError):
    """A GLEIF payload failed schema or identifier validation."""


class EntityReferenceRecord(BaseModel):
    """Vendor-neutral reference record admitted from GLEIF."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    lei: str
    legal_name: str = Field(min_length=1)
    jurisdiction: str
    entity_status: str = Field(min_length=1)
    registration_status: str = Field(min_length=1)
    initial_registration_at: AwareDatetime
    last_updated_at: AwareDatetime
    direct_parent_lei: str | None = None
    ultimate_parent_lei: str | None = None

    @field_validator("lei", "direct_parent_lei", "ultimate_parent_lei")
    @classmethod
    def _valid_lei(cls, value: str | None) -> str | None:
        if value is None:
            return None
        normalized = value.strip().upper()
        if not is_valid_lei(normalized):
            raise ValueError(f"invalid LEI checksum or shape: {value!r}")
        return normalized

    @field_validator("jurisdiction")
    @classmethod
    def _valid_jurisdiction(cls, value: str) -> str:
        normalized = value.strip().upper()
        if not _JURISDICTION.fullmatch(normalized):
            raise ValueError("GLEIF jurisdiction is not an ISO country/subdivision code")
        return normalized

    @classmethod
    def from_json_api(cls, raw: dict[str, Any]) -> Self:
        attributes = _mapping(raw.get("attributes"), "data.attributes")
        entity = _mapping(attributes.get("entity"), "data.attributes.entity")
        registration = _mapping(attributes.get("registration"), "data.attributes.registration")
        legal_name = _mapping(entity.get("legalName"), "entity.legalName").get("name")
        relationships = raw.get("relationships")
        relationship_map = (
            _mapping(relationships, "data.relationships") if relationships is not None else {}
        )
        return cls(
            lei=str(raw.get("id") or attributes.get("lei") or ""),
            legal_name=str(legal_name or ""),
            jurisdiction=str(entity.get("jurisdiction") or ""),
            entity_status=str(entity.get("status") or ""),
            registration_status=str(registration.get("status") or ""),
            initial_registration_at=_aware_datetime(
                registration.get("initialRegistrationDate"),
                "registration.initialRegistrationDate",
            ),
            last_updated_at=_aware_datetime(
                registration.get("lastUpdateDate"), "registration.lastUpdateDate"
            ),
            direct_parent_lei=_relationship_lei(relationship_map, "direct-parent"),
            ultimate_parent_lei=_relationship_lei(relationship_map, "ultimate-parent"),
        )

    def to_canonical(self, *, source_id: str, recorded_at: datetime) -> CanonicalDocument:
        if recorded_at.tzinfo is None or recorded_at.utcoffset() is None:
            raise ValueError("GLEIF recorded_at must be timezone-aware")
        if recorded_at < self.last_updated_at:
            raise ValueError("GLEIF record was fetched before its source update")
        metadata = {
            "legal_name": self.legal_name,
            "jurisdiction": self.jurisdiction,
            "entity_status": self.entity_status,
            "registration_status": self.registration_status,
        }
        if self.direct_parent_lei is not None:
            metadata["parent_lei"] = self.direct_parent_lei
        if self.ultimate_parent_lei is not None:
            metadata["ultimate_parent_lei"] = self.ultimate_parent_lei
        body = (
            f"GLEIF legal entity reference for {self.legal_name}. "
            f"Jurisdiction: {self.jurisdiction}. Entity status: {self.entity_status}. "
            f"Registration status: {self.registration_status}."
        )
        return CanonicalDocument(
            doc_id=f"GLEIF-{self.lei}",
            source_id=source_id,
            published_at=self.last_updated_at,
            recorded_at=recorded_at,
            title=self.legal_name,
            body=body,
            document_class=DocumentClass.REFERENCE,
            mentioned_names=(self.legal_name,),
            identifiers={"lei": self.lei},
            metadata=metadata,
        )


def is_valid_lei(value: str) -> bool:
    """Validate ISO 17442 LEI shape and ISO 7064 mod-97 check digits."""
    if len(value) != 20 or not value.isascii() or not value.isalnum():
        return False
    normalized = value.upper()
    expanded = "".join(
        character if character.isdigit() else str(ord(character) - ord("A") + 10)
        for character in normalized
    )
    remainder = 0
    for digit in expanded:
        remainder = (remainder * 10 + int(digit)) % 97
    return remainder == 1


class GleifRawAdapter:
    """Paginate the GLEIF index and archive changed per-LEI API responses."""

    def __init__(
        self,
        registration: SourceRegistration,
        access_policy: AccessPolicy,
        client: HardenedSourceClient,
        *,
        max_pages: int,
    ) -> None:
        if registration.kind is not SourceKind.REFERENCE_API:
            raise ValueError("GLEIF raw adapter requires a reference_api registration")
        if registration.barrier_side is not access_policy.barrier_side:
            raise ValueError("GLEIF registration and policy barriers differ")
        if not access_policy.allowed_entitlement_groups.issubset(
            registration.allowed_entitlement_groups
        ):
            raise ValueError("GLEIF policy contains an unregistered entitlement group")
        if max_pages < 1:
            raise ValueError("GLEIF max_pages must be positive")
        self._registration = registration
        self._policy = access_policy
        self._client = client
        self._max_pages = max_pages

    @property
    def source_id(self) -> str:
        return self._registration.source_id

    async def poll(self, cursor: RawSourceCursor | None = None) -> RawSourcePoll:
        if cursor is not None and cursor.source_id != self.source_id:
            raise ValueError("GLEIF cursor belongs to another source")
        prior = {
            item.external_id: item
            for item in (cursor.detail_validators if cursor is not None else ())
        }
        current: dict[str, DetailValidator] = {}
        sequence = cursor.sequence_number if cursor is not None else 0
        acquired: list[RawAcquiredItem] = []
        unchanged = 0
        discovered = 0
        page_count = 0
        latest = cursor.latest_source_published_at if cursor is not None else None
        page_url: str | None = self._registration.discovery_url
        seen_pages: set[str] = set()
        seen_entities: set[str] = set()
        feed_etag: str | None = None
        feed_last_modified: str | None = None
        feed_hash = hashlib.sha256()
        polled_at: datetime | None = None
        while page_url is not None:
            if page_url in seen_pages:
                raise MalformedGleifRecordError("GLEIF pagination contains a cycle")
            if page_count >= self._max_pages:
                raise MalformedGleifRecordError("GLEIF pagination exceeded max_pages")
            seen_pages.add(page_url)
            page = await self._client.fetch(
                page_url,
                max_bytes=self._registration.max_feed_bytes,
                conditional=(
                    ConditionalRequest(cursor.feed_etag, cursor.feed_last_modified)
                    if page_count == 0 and cursor is not None
                    else None
                ),
                accept="application/vnd.api+json, application/json",
            )
            page_count += 1
            if page.not_modified:
                return self._not_modified_poll(cursor, page_count, page.fetched_at)
            polled_at = page.fetched_at if polled_at is None else max(polled_at, page.fetched_at)
            if page_count == 1:
                feed_etag = page.header("etag")
                feed_last_modified = page.header("last-modified")
            feed_hash.update(page.payload)
            root = _json_mapping(page.payload)
            data = root.get("data")
            if not isinstance(data, list):
                raise MalformedGleifRecordError("GLEIF page data must be an array")
            for raw_item in data:
                summary = _mapping(raw_item, "data[]")
                discovered += 1
                (
                    sequence,
                    unchanged_delta,
                    published_at,
                    detail_fetched_at,
                ) = await self._acquire_summary(
                    summary,
                    prior,
                    current,
                    seen_entities,
                    acquired,
                    sequence,
                )
                unchanged += unchanged_delta
                latest = published_at if latest is None else max(latest, published_at)
                polled_at = (
                    detail_fetched_at if polled_at is None else max(polled_at, detail_fetched_at)
                )
            page_url = _next_link(root)

        ordered = list(current.values())
        ordered.extend(prior[key] for key in sorted(prior) if key not in current)
        last_poll_at = polled_at or datetime.now(UTC)
        next_cursor = RawSourceCursor(
            source_id=self.source_id,
            sequence_number=sequence,
            feed_etag=feed_etag,
            feed_last_modified=feed_last_modified,
            detail_validators=tuple(ordered[: self._registration.cursor_history_limit]),
            latest_source_published_at=latest,
            updated_at=last_poll_at,
        )
        return RawSourcePoll(
            source_id=self.source_id,
            polled_at=last_poll_at,
            feed_modified=True,
            feed_content_hash=feed_hash.hexdigest(),
            page_count=page_count,
            discovered_count=discovered,
            unchanged_count=unchanged,
            items=tuple(acquired),
            next_cursor=next_cursor,
        )

    async def close(self) -> None:
        await self._client.close()

    def _not_modified_poll(
        self,
        cursor: RawSourceCursor | None,
        page_count: int,
        fetched_at: datetime,
    ) -> RawSourcePoll:
        if cursor is None or page_count != 1:
            raise SourceTransportError("unexpected 304 in GLEIF pagination")
        next_cursor = cursor.model_copy(update={"updated_at": fetched_at})
        return RawSourcePoll(
            source_id=self.source_id,
            polled_at=fetched_at,
            feed_modified=False,
            page_count=1,
            discovered_count=0,
            unchanged_count=0,
            items=(),
            next_cursor=next_cursor,
        )

    async def _acquire_summary(
        self,
        summary: dict[str, Any],
        prior: dict[str, DetailValidator],
        current: dict[str, DetailValidator],
        seen_entities: set[str],
        acquired: list[RawAcquiredItem],
        sequence: int,
    ) -> tuple[int, int, datetime, datetime]:
        lei = str(summary.get("id") or "").upper()
        if not is_valid_lei(lei):
            raise MalformedGleifRecordError(f"GLEIF page contains invalid LEI {lei!r}")
        external_id = f"GLEIF-{lei}"
        if external_id in seen_entities:
            raise MalformedGleifRecordError("GLEIF pages repeat an entity")
        seen_entities.add(external_id)
        published_at = _summary_updated_at(summary)
        previous = prior.get(external_id)
        detail = await self._fetch_detail(summary, previous)
        if detail.not_modified:
            if previous is None:
                raise SourceTransportError("GLEIF detail returned 304 without prior state")
            current[external_id] = previous
            return sequence, 1, published_at, detail.fetched_at

        digest = hashlib.sha256(detail.payload).hexdigest()
        revision = f"sha256:{digest}"
        current[external_id] = DetailValidator(
            external_id=external_id,
            source_revision=revision,
            etag=detail.header("etag"),
            last_modified=detail.header("last-modified"),
        )
        if previous is not None and previous.source_revision == revision:
            return sequence, 1, published_at, detail.fetched_at
        sequence += 1
        headers = tuple(RawHeader(name=name, value=value) for name, value in detail.headers) + (
            RawHeader(name="content-location", value=detail.final_url),
        )
        envelope = RawSourceEnvelope(
            source_id=self.source_id,
            external_id=external_id,
            source_revision=revision,
            payload=detail.payload,
            media_type=detail.header("content-type") or "application/json",
            headers=headers,
            fetched_at=detail.fetched_at,
            source_published_at=published_at,
            access_policy=self._policy,
        )
        acquired.append(
            RawAcquiredItem(
                envelope=envelope,
                cursor_position=cursor_position(self.source_id, external_id, revision),
                sequence_number=sequence,
            )
        )
        return sequence, 0, published_at, detail.fetched_at

    async def _fetch_detail(
        self,
        summary: dict[str, Any],
        previous: DetailValidator | None,
    ) -> SourceHttpResponse:
        return await self._client.fetch(
            _self_link(summary),
            max_bytes=self._registration.max_detail_bytes,
            conditional=(
                ConditionalRequest(previous.etag, previous.last_modified)
                if previous is not None
                else None
            ),
            accept="application/vnd.api+json, application/json",
        )


class GleifTargetedRawAdapter:
    """Poll the exact configured LEI universe instead of an arbitrary API prefix."""

    def __init__(
        self,
        registration: SourceRegistration,
        access_policy: AccessPolicy,
        client: HardenedSourceClient,
        leis: frozenset[str],
    ) -> None:
        if registration.kind is not SourceKind.REFERENCE_API:
            raise ValueError("targeted GLEIF adapter requires a reference_api registration")
        if registration.barrier_side is not access_policy.barrier_side:
            raise ValueError("GLEIF registration and policy barriers differ")
        if not access_policy.allowed_entitlement_groups.issubset(
            registration.allowed_entitlement_groups
        ):
            raise ValueError("GLEIF policy contains an unregistered entitlement group")
        normalized = frozenset(item.strip().upper() for item in leis if item.strip())
        invalid = sorted(item for item in normalized if not is_valid_lei(item))
        if invalid:
            raise ValueError(f"target entity universe contains invalid LEIs: {invalid}")
        if not normalized:
            raise ValueError("target entity universe must contain at least one LEI")
        if len(normalized) > registration.cursor_history_limit:
            raise ValueError("target entity universe exceeds durable cursor capacity")
        self._registration = registration
        self._policy = access_policy
        self._client = client
        self._leis = normalized

    @property
    def source_id(self) -> str:
        return self._registration.source_id

    async def poll(self, cursor: RawSourceCursor | None = None) -> RawSourcePoll:
        if cursor is not None and cursor.source_id != self.source_id:
            raise ValueError("GLEIF cursor belongs to another source")
        prior = {
            item.external_id: item
            for item in (cursor.detail_validators if cursor is not None else ())
        }
        validators: list[DetailValidator] = []
        acquired: list[RawAcquiredItem] = []
        sequence = cursor.sequence_number if cursor is not None else 0
        latest = cursor.latest_source_published_at if cursor is not None else None
        unchanged = 0
        polled_at: datetime | None = None
        feed_hash = hashlib.sha256()
        base = "https://api.gleif.org/api/v1/lei-records"
        for lei in sorted(self._leis):
            external_id = f"GLEIF-{lei}"
            previous = prior.get(external_id)
            response = await self._client.fetch(
                f"{base}/{lei}",
                max_bytes=self._registration.max_detail_bytes,
                conditional=(
                    ConditionalRequest(previous.etag, previous.last_modified)
                    if previous is not None
                    else None
                ),
                accept="application/vnd.api+json, application/json",
            )
            polled_at = (
                response.fetched_at if polled_at is None else max(polled_at, response.fetched_at)
            )
            if response.not_modified:
                if previous is None:
                    raise SourceTransportError("GLEIF detail returned 304 without prior state")
                validators.append(previous)
                unchanged += 1
                continue
            root = _json_mapping(response.payload)
            record = EntityReferenceRecord.from_json_api(_mapping(root.get("data"), "data"))
            if record.lei != lei:
                raise MalformedGleifRecordError("GLEIF detail identity differs from target LEI")
            revision = hashlib.sha256(response.payload).hexdigest()
            validators.append(
                DetailValidator(
                    external_id=external_id,
                    source_revision=revision,
                    etag=response.header("etag"),
                    last_modified=response.header("last-modified"),
                )
            )
            latest = (
                record.last_updated_at if latest is None else max(latest, record.last_updated_at)
            )
            feed_hash.update(response.payload)
            if previous is not None and previous.source_revision == revision:
                unchanged += 1
                continue
            sequence += 1
            headers = tuple(
                RawHeader(name=name, value=value) for name, value in response.headers
            ) + (RawHeader(name="content-location", value=response.final_url),)
            envelope = RawSourceEnvelope(
                source_id=self.source_id,
                external_id=external_id,
                source_revision=revision,
                payload=response.payload,
                media_type=response.header("content-type") or "application/json",
                headers=headers,
                fetched_at=response.fetched_at,
                source_published_at=record.last_updated_at,
                access_policy=self._policy,
            )
            acquired.append(
                RawAcquiredItem(
                    envelope=envelope,
                    cursor_position=cursor_position(self.source_id, external_id, revision),
                    sequence_number=sequence,
                )
            )
        checked_at = polled_at or datetime.now(UTC)
        next_cursor = RawSourceCursor(
            source_id=self.source_id,
            sequence_number=sequence,
            detail_validators=tuple(validators),
            latest_source_published_at=latest,
            updated_at=checked_at,
        )
        return RawSourcePoll(
            source_id=self.source_id,
            polled_at=checked_at,
            feed_modified=bool(acquired),
            feed_content_hash=feed_hash.hexdigest(),
            page_count=len(self._leis),
            discovered_count=len(self._leis),
            unchanged_count=unchanged,
            items=tuple(acquired),
            next_cursor=next_cursor,
        )

    async def close(self) -> None:
        await self._client.close()


class GleifDetailCanonicalizer:
    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument:
        if envelope.source_id != "gleif":
            raise MalformedGleifRecordError("GLEIF canonicalizer received another source")
        root = _json_mapping(envelope.payload)
        raw = _mapping(root.get("data"), "data")
        record = EntityReferenceRecord.from_json_api(raw)
        expected = f"GLEIF-{record.lei}"
        if envelope.external_id != expected:
            raise MalformedGleifRecordError("GLEIF detail identity differs from envelope")
        if envelope.source_published_at != record.last_updated_at:
            raise MalformedGleifRecordError(
                "GLEIF page and detail disagree on the source update time"
            )
        return record.to_canonical(source_id=envelope.source_id, recorded_at=envelope.fetched_at)


class GleifBulkPage(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    records: tuple[EntityReferenceRecord, ...]
    next_token: str | None = None


GleifBulkPageFetcher = Callable[[str | None, int], Awaitable[GleifBulkPage]]


class GleifBulkAdapter:
    """Existing SourceAdapter contract over injected paginated bulk batches."""

    def __init__(
        self,
        fetch_page: GleifBulkPageFetcher,
        *,
        source_id: str = "gleif",
        page_size: int = 10_000,
        max_pages: int = 10_000,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if page_size < 1 or max_pages < 1:
            raise ValueError("GLEIF bulk page limits must be positive")
        self._fetch_page = fetch_page
        self._source_id = source_id
        self._page_size = page_size
        self._max_pages = max_pages
        self._clock = clock or (lambda: datetime.now(UTC))
        self._documents: list[CanonicalDocument] | None = None

    @property
    def source_id(self) -> str:
        return self._source_id

    async def _load(self) -> list[CanonicalDocument]:
        if self._documents is not None:
            return self._documents
        documents: list[CanonicalDocument] = []
        token: str | None = None
        seen_tokens: set[str | None] = set()
        for _ in range(self._max_pages):
            if token in seen_tokens:
                raise MalformedGleifRecordError("GLEIF bulk pagination contains a cycle")
            seen_tokens.add(token)
            page = await self._fetch_page(token, self._page_size)
            recorded_at = self._clock()
            documents.extend(
                record.to_canonical(source_id=self._source_id, recorded_at=recorded_at)
                for record in page.records
            )
            token = page.next_token
            if token is None:
                self._documents = documents
                return documents
        raise MalformedGleifRecordError("GLEIF bulk pagination exceeded max_pages")

    async def fetch(self, cursor: FetchCursor | None = None) -> Any:
        if cursor is not None and cursor.source_id != self._source_id:
            raise ValueError("GLEIF bulk cursor belongs to another source")
        start = int(cursor.position) if cursor is not None else 0
        for document in (await self._load())[start:]:
            yield document

    def cursor_for(self, doc: CanonicalDocument) -> FetchCursor:
        if self._documents is None:
            raise RuntimeError("cursor_for called before GLEIF bulk fetch")
        for index, candidate in enumerate(self._documents):
            if candidate.doc_id == doc.doc_id:
                return FetchCursor(
                    source_id=self._source_id,
                    position=str(index + 1),
                    updated_at=doc.recorded_at,
                )
        raise ValueError(f"document {doc.doc_id!r} was not served by GLEIF bulk")


def gleif_registered(
    access_policy: AccessPolicy,
    settings: Settings | None = None,
    *,
    transport: SourceHttpTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GleifRawAdapter:
    active = settings or Settings()
    registration = production_source_catalog(active).require("gleif")
    client = HardenedSourceClient(
        transport or HttpxSourceTransport(verify=active.source_tls_verify),
        allowed_origins=registration.allowed_origins,
        user_agent=active.rss_user_agent,
        timeout_seconds=registration.request_timeout_seconds,
        max_attempts=registration.max_attempts,
        max_redirects=registration.max_redirects,
        clock=clock,
        log_context={"source_id": registration.source_id},
    )
    return GleifRawAdapter(
        registration,
        access_policy,
        client,
        max_pages=active.gleif_max_pages,
    )


def gleif_targeted_registered(
    access_policy: AccessPolicy,
    leis: frozenset[str],
    settings: Settings | None = None,
    *,
    transport: SourceHttpTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> GleifTargetedRawAdapter:
    """Build the canonical adapter for a signed-off target LEI universe."""

    active = settings or Settings()
    registration = production_source_catalog(active).require("gleif")
    client = HardenedSourceClient(
        transport or HttpxSourceTransport(verify=active.source_tls_verify),
        allowed_origins=registration.allowed_origins,
        user_agent=active.rss_user_agent,
        timeout_seconds=registration.request_timeout_seconds,
        max_attempts=registration.max_attempts,
        max_redirects=registration.max_redirects,
        clock=clock,
        log_context={"source_id": registration.source_id},
    )
    return GleifTargetedRawAdapter(registration, access_policy, client, leis)


def gleif_fixture() -> FixtureAdapter:
    """The GLEIF golden-copy fixture used across the test suite."""
    return FixtureAdapter(source_id="gleif_fixture", fixture_name="gleif_golden_copy.json")


def _mapping(value: object, path: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise MalformedGleifRecordError(f"GLEIF {path} must be an object")
    return cast(dict[str, Any], value)


def _json_mapping(payload: bytes) -> dict[str, Any]:
    try:
        value = json.loads(payload)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise MalformedGleifRecordError("GLEIF response is not valid JSON") from exc
    return _mapping(value, "response")


def _aware_datetime(value: object, path: str) -> datetime:
    if not isinstance(value, str):
        raise MalformedGleifRecordError(f"GLEIF {path} must be a timestamp")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise MalformedGleifRecordError(f"GLEIF {path} is invalid") from exc
    if parsed.tzinfo is None:
        raise MalformedGleifRecordError(f"GLEIF {path} has no timezone")
    return parsed


def _relationship_lei(relationships: dict[str, Any], name: str) -> str | None:
    relation = relationships.get(name)
    if relation is None:
        return None
    relation_map = _mapping(relation, f"relationships.{name}")
    data = relation_map.get("data")
    if data is None:
        return None
    data_map = _mapping(data, f"relationships.{name}.data")
    identifier = data_map.get("id")
    return str(identifier) if identifier else None


def _summary_updated_at(summary: dict[str, Any]) -> datetime:
    attributes = _mapping(summary.get("attributes"), "summary.attributes")
    registration = _mapping(attributes.get("registration"), "summary.attributes.registration")
    return _aware_datetime(registration.get("lastUpdateDate"), "lastUpdateDate")


def _self_link(summary: dict[str, Any]) -> str:
    links = _mapping(summary.get("links"), "summary.links")
    value = links.get("self")
    if not isinstance(value, str) or not value:
        raise MalformedGleifRecordError("GLEIF summary is missing links.self")
    return value


def _next_link(root: dict[str, Any]) -> str | None:
    links = _mapping(root.get("links"), "links")
    value = links.get("next")
    if value is None:
        return None
    if isinstance(value, str):
        return value or None
    if isinstance(value, dict):
        href = value.get("href")
        if isinstance(href, str) and href:
            return href
    raise MalformedGleifRecordError("GLEIF links.next has an unsupported shape")
