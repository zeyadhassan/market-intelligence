"""Raw source acquisition contracts used before canonicalization."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol, Self, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from fi_intel.application.raw import RawSourceEnvelope


class AcquisitionModel(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")


class DetailValidator(AcquisitionModel):
    external_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    etag: str | None = None
    last_modified: str | None = None
    detail_url: str | None = None


class RawSourceCursor(AcquisitionModel):
    """Bounded restart state for one source partition."""

    source_id: str = Field(min_length=1)
    partition_key: str = "default"
    sequence_number: int = Field(ge=0)
    feed_etag: str | None = None
    feed_last_modified: str | None = None
    detail_validators: tuple[DetailValidator, ...] = ()
    latest_source_published_at: AwareDatetime | None = None
    updated_at: AwareDatetime

    @model_validator(mode="after")
    def _unique_items(self) -> Self:
        identities = [item.external_id for item in self.detail_validators]
        if len(set(identities)) != len(identities):
            raise ValueError("raw cursor contains duplicate detail identities")
        return self


class RawAcquiredItem(AcquisitionModel):
    envelope: RawSourceEnvelope
    cursor_position: str = Field(min_length=1)
    sequence_number: int = Field(ge=1)


class RawSourcePoll(AcquisitionModel):
    source_id: str = Field(min_length=1)
    partition_key: str = "default"
    polled_at: AwareDatetime
    feed_modified: bool
    feed_content_hash: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    page_count: int = Field(ge=1)
    discovered_count: int = Field(ge=0)
    unchanged_count: int = Field(ge=0)
    failed_count: int = Field(default=0, ge=0)
    items: tuple[RawAcquiredItem, ...]
    next_cursor: RawSourceCursor

    @model_validator(mode="after")
    def _counts_and_cursor_match(self) -> Self:
        if self.next_cursor.source_id != self.source_id:
            raise ValueError("poll cursor belongs to another source")
        if self.next_cursor.partition_key != self.partition_key:
            raise ValueError("poll cursor belongs to another partition")
        if self.discovered_count != self.unchanged_count + self.failed_count + len(self.items):
            raise ValueError("poll discovery counts are incomplete")
        sequences = [item.sequence_number for item in self.items]
        if sequences and sequences != list(range(sequences[0], sequences[-1] + 1)):
            raise ValueError("poll item sequence numbers must be contiguous")
        if sequences and self.next_cursor.sequence_number != sequences[-1]:
            raise ValueError("poll cursor does not follow the final item")
        return self


@runtime_checkable
class RawSourceAdapter(Protocol):
    @property
    def source_id(self) -> str: ...

    async def poll(self, cursor: RawSourceCursor | None = None) -> RawSourcePoll: ...

    async def close(self) -> None: ...


def cursor_position(source_id: str, external_id: str, revision: str) -> str:
    """Return the opaque item checkpoint stored by the ingestion watermark."""
    return f"{source_id}\x1f{external_id}\x1f{revision}"


def require_aware(value: datetime, name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{name} must be timezone-aware")
    return value
