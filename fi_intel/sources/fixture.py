"""Fixture-backed adapter serving the synthetic corpus.

This is the source used by every golden-path, negative, and leakage test.
It behaves like a real wire adapter — ordered, cursor-resumable, loud on
malformed records — so tests exercise the same code path as production
ingestion. No network, no clock dependence: the corpus is data.
"""

import json
from importlib import resources
from typing import Any

from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import CanonicalDocument


class MalformedFixtureError(ValueError):
    """A fixture record failed to map to CanonicalDocument.

    Raised rather than skipped because silent data loss would corrupt
    backtests that span the gap.
    """


class FixtureAdapter:
    """Serves canonical documents from a packaged JSON fixture file."""

    def __init__(self, source_id: str, fixture_name: str) -> None:
        self._source_id = source_id
        self._fixture_name = fixture_name
        self._records: list[dict[str, Any]] | None = None

    @property
    def source_id(self) -> str:
        return self._source_id

    def _load(self) -> list[dict[str, Any]]:
        if self._records is None:
            ref = resources.files("fi_intel.synth.data").joinpath(self._fixture_name)
            raw = json.loads(ref.read_text(encoding="utf-8"))
            if not isinstance(raw, list):
                msg = f"fixture {self._fixture_name} must be a JSON array of records"
                raise MalformedFixtureError(msg)
            self._records = raw
        return self._records

    async def fetch(self, cursor: FetchCursor | None = None) -> Any:
        start = 0
        if cursor is not None:
            if cursor.source_id != self._source_id:
                msg = f"cursor for {cursor.source_id!r} passed to {self._source_id!r}"
                raise ValueError(msg)
            start = int(cursor.position)
        for index, record in enumerate(self._load()[start:], start=start):
            try:
                yield CanonicalDocument.model_validate(record)
            except Exception as exc:
                doc_id = record.get("doc_id", f"<index {index}>")
                msg = f"malformed fixture record doc_id={doc_id!r}: {exc}"
                raise MalformedFixtureError(msg) from exc

    def cursor_for(self, doc: CanonicalDocument) -> FetchCursor:
        records = self._load()
        for index, record in enumerate(records):
            if record.get("doc_id") == doc.doc_id:
                return FetchCursor(
                    source_id=self._source_id,
                    position=str(index + 1),
                    updated_at=doc.recorded_at,
                )
        msg = f"doc_id {doc.doc_id!r} not served by fixture {self._fixture_name!r}"
        raise ValueError(msg)


def synthetic_wire() -> FixtureAdapter:
    """The primary synthetic wire used across the test suite."""
    return FixtureAdapter(source_id="synthetic_wire", fixture_name="synthetic_wire.json")


def synthetic_wire_private() -> FixtureAdapter:
    """Private-side synthetic source. Exists so entitlement tests have a
    document that must never leak to public-side principals."""
    return FixtureAdapter(
        source_id="synthetic_wire_private", fixture_name="synthetic_wire_private.json"
    )
