"""Immutable raw-source boundary and content-addressed archive contract."""

from __future__ import annotations

import asyncio
import hashlib
import os
from pathlib import Path
from typing import Protocol, runtime_checkable
from urllib.parse import unquote, urlparse
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


class FileRawArchive:
    """Content-verifying durable archive rooted at an operator-owned mount."""

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).resolve()
        self._root.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        candidate = (self._root / key).resolve()
        if self._root != candidate and self._root not in candidate.parents:
            raise ValueError("archive key escapes the configured root")
        return candidate

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
        if hashlib.sha256(content).hexdigest() != content_hash:
            raise ArchiveConflictError("declared content hash does not match bytes")
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists():
            previous = path.read_bytes()
            if previous != content:
                raise ArchiveConflictError("archive key has conflicting immutable content")
        else:
            temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
            temporary.write_bytes(content)
            try:
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        return ArchivedObject(
            key=key,
            uri=path.as_uri(),
            content_hash=content_hash,
            size_bytes=len(content),
            media_type=media_type,
            archived_at=archived_at,
        )

    async def get(self, uri: str) -> bytes:
        path = _archive_uri_path(uri)
        if self._root != path and self._root not in path.parents:
            raise ValueError("archive URI escapes the configured root")
        return await asyncio.to_thread(path.read_bytes)

    async def close(self) -> None:
        return None


def _archive_uri_path(uri: str) -> Path:
    parsed = urlparse(uri)
    if parsed.scheme != "file":
        raise ValueError("archive URI must use the file scheme")
    raw_path = unquote(parsed.path)
    if os.name == "nt" and raw_path.startswith("/"):
        raw_path = raw_path[1:]
    return Path(raw_path).resolve()
