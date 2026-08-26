"""Immutable raw-source boundary and content-addressed archive contract."""

from __future__ import annotations

import hashlib
from typing import Protocol, runtime_checkable
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator

from fi_intel.ledger.models import AccessPolicy, raw_asset_id


class RawHeader(BaseModel):
    """One source header; tuple storage preserves repeated header names."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(min_length=1)
    value: str

    @field_validator("name")
    @classmethod
    def _canonical_name(cls, value: str) -> str:
        stripped = value.strip().lower()
        if not stripped:
            raise ValueError("header name cannot be blank")
        return stripped


class RawSourceEnvelope(BaseModel):
    """Exactly one immutable source revision before any parsing or mapping."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_id: str = Field(min_length=1)
    external_id: str = Field(min_length=1)
    source_revision: str = Field(min_length=1)
    payload: bytes = Field(min_length=1)
    media_type: str = Field(min_length=1)
    headers: tuple[RawHeader, ...] = ()
    fetched_at: AwareDatetime
    source_published_at: AwareDatetime | None = None
    access_policy: AccessPolicy

    @property
    def content_hash(self) -> str:
        return hashlib.sha256(self.payload).hexdigest()

    @property
    def raw_asset_id(self) -> UUID:
        return raw_asset_id(self.source_id, self.external_id, self.source_revision)

    @property
    def archive_key(self) -> str:
        return f"raw/{self.raw_asset_id}/{self.content_hash}"


class ArchivedObject(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    key: str = Field(min_length=1)
    uri: str = Field(min_length=1)
    content_hash: str = Field(pattern=r"^[0-9a-f]{64}$")
    size_bytes: int = Field(ge=1)
    media_type: str = Field(min_length=1)
    archived_at: AwareDatetime


class ArchiveConflictError(RuntimeError):
    """An archive key was reused for different bytes or metadata."""


@runtime_checkable
class RawArchive(Protocol):
    async def put_if_absent(
        self,
        *,
        key: str,
        content: bytes,
        content_hash: str,
        media_type: str,
        archived_at: AwareDatetime,
    ) -> ArchivedObject: ...

    async def get(self, uri: str) -> bytes: ...

    async def close(self) -> None: ...


class InMemoryRawArchive:
    """Content-verifying archive used by application contract tests."""

    def __init__(self) -> None:
        self._objects: dict[str, tuple[ArchivedObject, bytes]] = {}

    async def put_if_absent(
        self,
        *,
        key: str,
        content: bytes,
        content_hash: str,
        media_type: str,
        archived_at: AwareDatetime,
    ) -> ArchivedObject:
        if not content:
            raise ValueError("archive content cannot be empty")
        actual_hash = hashlib.sha256(content).hexdigest()
        if actual_hash != content_hash:
            raise ArchiveConflictError("declared content hash does not match bytes")
        uri = f"memory://{key}"
        archived = ArchivedObject(
            key=key,
            uri=uri,
            content_hash=content_hash,
            size_bytes=len(content),
            media_type=media_type,
            archived_at=archived_at,
        )
        previous = self._objects.get(key)
        if previous is not None:
            previous_object, previous_content = previous
            if previous_content != content or previous_object.media_type != media_type:
                raise ArchiveConflictError("archive key has conflicting immutable content")
            return previous_object
        self._objects[key] = (archived, bytes(content))
        return archived

    async def get(self, uri: str) -> bytes:
        for archived, content in self._objects.values():
            if archived.uri == uri:
                return bytes(content)
        raise KeyError(uri)

    async def close(self) -> None:
        return None

    def object_count(self) -> int:
        return len(self._objects)
