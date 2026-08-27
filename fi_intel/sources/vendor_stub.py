"""A stub showing the shape of a real vendor adapter.

This exists so the adapter pattern is reviewable before any vendor
credentials are in place, and so the contract test has a second
implementation to run against. It serves canned records; a real adapter
replaces ``_page`` with an httpx call wrapped in tenacity, and nothing
else changes downstream.
"""

from datetime import UTC, datetime
from typing import Any

from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import (
    BarrierSide,
    CanonicalDocument,
    DocumentClass,
)


class VendorStubAdapter:
    """Minimal vendor-shaped adapter over an in-memory page of records."""

    def __init__(self, source_id: str = "vendor_stub") -> None:
        self._source_id = source_id
        # Vendor-shaped raw records: deliberately *not* canonical field
        # names, to prove the mapping happens here and only here.
        self._raw_records: list[dict[str, Any]] = [
            {
                "article_ref": "VS-0001",
                "headline_txt": "Stub vendor sample article",
                "story_body": "This record exists to exercise the adapter contract.",
                "pub_ts": "2024-01-05T08:00:00+00:00",
                "ingest_ts": "2024-01-05T08:05:00+00:00",
                "lang_cd": "en",
                "entitlements": {"side": "public"},
            }
        ]

    @property
    def source_id(self) -> str:
        return self._source_id

    def _map(self, raw: dict[str, Any]) -> CanonicalDocument:
        # The only place vendor field names may appear. If a field cannot
        # be mapped cleanly, this raises instead of coercing.
        return CanonicalDocument(
            doc_id=str(raw["article_ref"]),
            source_id=self._source_id,
            title=str(raw["headline_txt"]),
            body=str(raw["story_body"]),
            published_at=datetime.fromisoformat(str(raw["pub_ts"])),
            recorded_at=datetime.fromisoformat(str(raw["ingest_ts"])),
            language=str(raw["lang_cd"]),
            document_class=DocumentClass.NEWS_WIRE,
            barrier_side=BarrierSide(str(raw["entitlements"]["side"])),
        )

    async def fetch(self, cursor: FetchCursor | None = None) -> Any:
        start = 0
        if cursor is not None:
            if cursor.source_id != self._source_id:
                msg = f"cursor for {cursor.source_id!r} passed to {self._source_id!r}"
                raise ValueError(msg)
            start = int(cursor.position)
        for raw in self._raw_records[start:]:
            yield self._map(raw)

    def cursor_for(self, doc: CanonicalDocument) -> FetchCursor:
        for index, raw in enumerate(self._raw_records):
            if str(raw["article_ref"]) == doc.doc_id:
                return FetchCursor(
                    source_id=self._source_id,
                    position=str(index + 1),
                    updated_at=datetime.now(tz=UTC),
                )
        msg = f"doc_id {doc.doc_id!r} not served by {self._source_id!r}"
        raise ValueError(msg)
