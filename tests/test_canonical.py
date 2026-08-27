"""Tests for the canonical document boundary."""

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from fi_intel.sources.canonical import (
    BarrierSide,
    CanonicalDocument,
    DocumentClass,
)


def _doc(**overrides: object) -> CanonicalDocument:
    base: dict[str, object] = {
        "doc_id": "T-1",
        "source_id": "test",
        "published_at": datetime(2024, 1, 1, 8, tzinfo=UTC),
        "recorded_at": datetime(2024, 1, 1, 9, tzinfo=UTC),
        "title": "t",
        "body": "b",
        "document_class": DocumentClass.NEWS_WIRE,
        "barrier_side": BarrierSide.PUBLIC,
    }
    base.update(overrides)
    return CanonicalDocument.model_validate(base)


def test_vendor_namespaced_metadata_key_rejected() -> None:
    with pytest.raises(ValidationError, match="vendor namespaced key"):
        _doc(metadata={"factiva_accn": "123"})


def test_vendor_namespaced_identifier_key_rejected() -> None:
    with pytest.raises(ValidationError, match="vendor namespaced key"):
        _doc(identifiers={"rdp_perm_id": "42"})


def test_recorded_before_published_rejected() -> None:
    with pytest.raises(ValidationError, match="recorded_at precedes published_at"):
        _doc(
            published_at=datetime(2024, 1, 2, 8, tzinfo=UTC),
            recorded_at=datetime(2024, 1, 1, 8, tzinfo=UTC),
        )


def test_content_hash_ignores_envelope_and_case() -> None:
    a = _doc(doc_id="A", title="Same Story", body="Body text here.")
    b = _doc(doc_id="B", title="same story", body="  body   text here. ")
    assert a.content_hash() == b.content_hash()


def test_content_hash_distinguishes_different_stories() -> None:
    a = _doc(doc_id="A", body="Story one.")
    b = _doc(doc_id="B", body="Story two.")
    assert a.content_hash() != b.content_hash()
