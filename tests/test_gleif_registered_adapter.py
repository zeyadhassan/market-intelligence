"""Registered, paginated GLEIF reference-data contracts."""

import json
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from fi_intel.ledger import AccessPolicy
from fi_intel.sources.adapters.gleif import (
    EntityReferenceRecord,
    GleifBulkAdapter,
    GleifBulkPage,
    GleifDetailCanonicalizer,
    GleifRawAdapter,
    is_valid_lei,
)
from fi_intel.sources.canonical import BarrierSide, DocumentClass
from fi_intel.sources.catalog import production_source_catalog
from fi_intel.sources.transport import HardenedSourceClient
from tests.source_support import ScriptedSourceTransport, source_response

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
LEI_ONE = "529900T8BM49AURSDO55"
LEI_TWO = "506700LOLO7M6V0E4247"
PAGE_ONE = "https://api.gleif.org/api/v1/lei-records?page[size]=100"
PAGE_TWO = "https://api.gleif.org/api/v1/lei-records?page[number]=2"
BULK_PAGE_TWO = "page-2"


def _policy() -> AccessPolicy:
    return AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_gcc_public"}),
        created_at=NOW - timedelta(days=1),
    )


def _record(lei: str, legal_name: str) -> dict[str, object]:
    return {
        "type": "lei-records",
        "id": lei,
        "attributes": {
            "lei": lei,
            "entity": {
                "legalName": {"name": legal_name},
                "jurisdiction": "DE",
                "status": "ACTIVE",
            },
            "registration": {
                "initialRegistrationDate": "2020-01-01T00:00:00Z",
                "lastUpdateDate": "2026-08-24T10:00:00Z",
                "status": "ISSUED",
            },
        },
        "relationships": {},
        "links": {"self": f"https://api.gleif.org/api/v1/lei-records/{lei}"},
    }


def _payload(value: object) -> bytes:
    return json.dumps(value, separators=(",", ":"), sort_keys=True).encode()


def _entity_record(lei: str, legal_name: str) -> EntityReferenceRecord:
    return EntityReferenceRecord.from_json_api(_record(lei, legal_name))


def test_lei_scheme_validation_rejects_shape_and_checksum_errors() -> None:
    assert is_valid_lei(LEI_ONE)
    assert is_valid_lei(LEI_TWO)
    assert not is_valid_lei("529900T8BM49AURSDO54")
    assert not is_valid_lei("short")

    with pytest.raises(ValidationError, match="invalid LEI"):
        EntityReferenceRecord(
            lei="529900T8BM49AURSDO54",
            legal_name="Invalid Entity",
            jurisdiction="DE",
            entity_status="ACTIVE",
            registration_status="ISSUED",
            initial_registration_at=NOW - timedelta(days=10),
            last_updated_at=NOW - timedelta(days=1),
        )


async def test_gleif_raw_adapter_paginates_and_canonicalizes_exact_details() -> None:
    summary_one = _record(LEI_ONE, "Example Bank AG")
    summary_two = _record(LEI_TWO, "Example Holding SE")
    detail_one = {"data": summary_one, "links": {"self": summary_one["links"]}}
    detail_two = {"data": summary_two, "links": {"self": summary_two["links"]}}
    transport = ScriptedSourceTransport(
        [
            (
                PAGE_ONE,
                source_response(
                    200,
                    _payload(
                        {
                            "data": [summary_one],
                            "links": {"next": PAGE_TWO},
                        }
                    ),
                    headers=(("content-type", "application/vnd.api+json"),),
                ),
            ),
            (
                f"https://api.gleif.org/api/v1/lei-records/{LEI_ONE}",
                source_response(
                    200,
                    _payload(detail_one),
                    headers=(("content-type", "application/vnd.api+json"),),
                ),
            ),
            (
                PAGE_TWO,
                source_response(
                    200,
                    _payload({"data": [summary_two], "links": {"next": None}}),
                    headers=(("content-type", "application/vnd.api+json"),),
                ),
            ),
            (
                f"https://api.gleif.org/api/v1/lei-records/{LEI_TWO}",
                source_response(
                    200,
                    _payload(detail_two),
                    headers=(("content-type", "application/vnd.api+json"),),
                ),
            ),
        ]
    )
    registration = production_source_catalog().require("gleif")
    policy = _policy()
    client = HardenedSourceClient(
        transport,
        allowed_origins=registration.allowed_origins,
        user_agent="fi-intel-test test@example.invalid",
        timeout_seconds=1,
        max_attempts=1,
        max_redirects=1,
        clock=lambda: NOW,
    )
    adapter = GleifRawAdapter(registration, policy, client, max_pages=2)

    poll = await adapter.poll()

    assert poll.page_count == 2
    assert poll.discovered_count == 2
    assert [item.sequence_number for item in poll.items] == [1, 2]
    canonicalizer = GleifDetailCanonicalizer()
    documents = [
        await canonicalizer.canonicalize(item.envelope) for item in poll.items
    ]
    assert [document.identifiers["lei"] for document in documents] == [
        LEI_ONE,
        LEI_TWO,
    ]
    assert all(document.document_class is DocumentClass.REFERENCE for document in documents)
    assert all(item.envelope.access_policy.policy_id == policy.policy_id for item in poll.items)
    transport.assert_exhausted()


async def test_injected_gleif_bulk_pages_preserve_source_adapter_restart() -> None:
    pages = {
        None: GleifBulkPage(
            records=(_entity_record(LEI_ONE, "Example Bank AG"),),
            next_token=BULK_PAGE_TWO,
        ),
        BULK_PAGE_TWO: GleifBulkPage(
            records=(_entity_record(LEI_TWO, "Example Holding SE"),),
            next_token=None,
        ),
    }
    calls: list[tuple[str | None, int]] = []

    async def fetch_page(token: str | None, page_size: int) -> GleifBulkPage:
        calls.append((token, page_size))
        return pages[token]

    adapter = GleifBulkAdapter(fetch_page, page_size=1, clock=lambda: NOW)
    documents = [document async for document in adapter.fetch()]
    cursor = adapter.cursor_for(documents[0])
    resumed = [document async for document in adapter.fetch(cursor)]

    assert [document.identifiers["lei"] for document in documents] == [
        LEI_ONE,
        LEI_TWO,
    ]
    assert [document.doc_id for document in resumed] == [documents[1].doc_id]
    assert calls == [(None, 1), (BULK_PAGE_TWO, 1)]
