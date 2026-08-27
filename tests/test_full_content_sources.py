"""Raw-first SEC/Fed acquisition and v2 ingestion integration."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from fi_intel.application.control import InMemoryIngestionControlStore
from fi_intel.application.ingestion import ReplayableIngestionService
from fi_intel.application.raw import InMemoryRawArchive
from fi_intel.application.source_ingestion import SourceIngestionCoordinator
from fi_intel.config import Settings
from fi_intel.ingest.resolve import EntityResolver, InMemoryResolutionStore
from fi_intel.ledger import AccessPolicy, InMemoryIntelligenceLedger
from fi_intel.ledger.models import document_identity_id
from fi_intel.sources.adapters.government import (
    GovernmentDetailCanonicalizer,
    fed_press_full_content,
    sec_edgar_full_content,
)
from fi_intel.sources.canonical import BarrierSide, CanonicalDocument, DocumentClass
from fi_intel.sources.catalog import production_source_catalog
from fi_intel.sources.operations import InMemorySourceOperationsStore, SourceHealth
from fi_intel.sources.transport import DisallowedSourceUrlError
from tests.source_support import ScriptedSourceTransport, source_response

NOW = datetime(2026, 8, 25, 12, tzinfo=UTC)
ENABLED_SETTINGS = Settings(
    enable_sec_edgar_source=True,
    enable_fed_press_source=True,
)
SEC_FEED = (
    "https://www.sec.gov/cgi-bin/browse-edgar"
    "?action=getcurrent&type=8-K&company=&dateb=&owner=include&count=100&output=atom"
)
SEC_DETAIL = "https://www.sec.gov/Archives/edgar/data/1/filing.htm"
FED_FEED = "https://www.federalreserve.gov/feeds/press_all.xml"
FED_DETAIL = "https://www.federalreserve.gov/newsevents/pressreleases/orders20260825a.htm"


class TickingClock:
    def __init__(self) -> None:
        self.value = NOW

    def __call__(self) -> datetime:
        self.value += timedelta(seconds=1)
        return self.value


def _policy() -> AccessPolicy:
    return AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_gcc_public"}),
        created_at=NOW - timedelta(days=1),
    )


def _sec_feed(detail_url: str = SEC_DETAIL) -> bytes:
    return f"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>8-K - Example Bank (0000000001) (Filer)</title>
    <link rel="alternate" href="{detail_url}"/>
    <updated>2026-08-25T11:55:00+00:00</updated>
    <id>urn:tag:sec.gov,2008:accession-number=0001</id>
  </entry>
</feed>""".encode()


def _detail(amount: str, *, closed: bool = True) -> bytes:
    closing = "</body></html>" if closed else ""
    return (
        "<html><head><title>Example Bank 8-K</title></head><body>"
        "<h1>Example Bank capital filing</h1>"
        f"<p>The issuer completed a {amount} senior funding transaction.</p>{closing}"
    ).encode()


def _fed_feed() -> bytes:
    return f"""<?xml version="1.0"?>
<rss><channel><item>
  <title>Federal Reserve Board announces approval of application by
  National Westminster Bank Plc</title>
  <link>{FED_DETAIL}</link>
  <guid>fed-order-1</guid>
  <pubDate>Tue, 25 Aug 2026 10:00:00 GMT</pubDate>
</item></channel></rss>""".encode()


async def test_sec_correction_and_conditional_restart_use_raw_v2_path() -> None:
    policy = _policy()
    clock = TickingClock()
    transport = ScriptedSourceTransport(
        [
            (
                SEC_FEED,
                source_response(
                    200,
                    _sec_feed(),
                    headers=(("content-type", "application/atom+xml"), ("etag", '"f1"')),
                ),
            ),
            (
                SEC_DETAIL,
                source_response(
                    200,
                    _detail("USD 500 million"),
                    headers=(("content-type", "text/html; charset=utf-8"), ("etag", '"d1"')),
                ),
            ),
            (
                SEC_FEED,
                source_response(
                    200,
                    _sec_feed(),
                    headers=(("content-type", "application/atom+xml"), ("etag", '"f2"')),
                ),
            ),
            (
                SEC_DETAIL,
                source_response(
                    200,
                    _detail("USD 750 million"),
                    headers=(("content-type", "text/html; charset=utf-8"), ("etag", '"d2"')),
                ),
            ),
            (SEC_FEED, source_response(304)),
        ]
    )
    adapter = sec_edgar_full_content(policy, ENABLED_SETTINGS, transport=transport, clock=clock)
    ledger = InMemoryIntelligenceLedger()
    control = InMemoryIngestionControlStore()
    archive = InMemoryRawArchive()
    ingestion = ReplayableIngestionService(
        archive,
        GovernmentDetailCanonicalizer(),
        ledger,
        control,
        clock=clock,
    )
    operations = InMemorySourceOperationsStore()
    registration = production_source_catalog(ENABLED_SETTINGS).require("sec_edgar_8k")
    coordinator = SourceIngestionCoordinator(
        registration, policy, adapter, ingestion, operations, clock=clock
    )

    first = await coordinator.run(requested_by="test")
    second = await coordinator.run(requested_by="test")
    unchanged = await coordinator.run(requested_by="test")

    assert first.observation.committed_count == 1
    assert second.observation.committed_count == 1
    assert unchanged.observation.acquired_count == 0
    assert unchanged.observation.feed_modified is False
    assert all(
        result.observation.health is SourceHealth.HEALTHY for result in (first, second, unchanged)
    )
    identity = document_identity_id(
        "sec_edgar_8k",
        "sec_edgar_8k:urn:tag:sec.gov,2008:accession-number=0001",
    )
    head = await ledger.document_head(identity)
    assert head is not None and head.version_number == 2
    assert archive.object_count() == 4  # two exact responses and two normalized versions
    assert transport.requests[-1][1]["If-None-Match"] == '"f2"'
    state = await operations.load_state("sec_edgar_8k")
    assert state is not None and state.cursor is not None
    assert state.cursor.sequence_number == 2
    transport.assert_exhausted()


async def test_full_detail_canonicalization_keeps_narrative_and_resolvable_names() -> None:
    sec_transport = ScriptedSourceTransport(
        [
            (SEC_FEED, source_response(200, _sec_feed())),
            (
                SEC_DETAIL,
                source_response(
                    200,
                    _detail("USD 500 million"),
                    headers=(("content-type", "text/html; charset=utf-8"),),
                ),
            ),
        ]
    )
    sec_poll = await sec_edgar_full_content(
        _policy(), ENABLED_SETTINGS, transport=sec_transport, clock=lambda: NOW
    ).poll()
    sec_document = await GovernmentDetailCanonicalizer().canonicalize(sec_poll.items[0].envelope)
    assert "USD 500 million senior funding transaction" in sec_document.body
    assert sec_document.mentioned_names == ("Example Bank",)
    assert sec_document.identifiers == {"cik": "0000000001"}

    fed_transport = ScriptedSourceTransport(
        [
            (FED_FEED, source_response(200, _fed_feed())),
            (
                FED_DETAIL,
                source_response(
                    200,
                    (
                        b"<html><head><title>Approval order</title></head><body>"
                        b"<h1>Approval order</h1><p>The Board approved the bank's "
                        b"application following its statutory review.</p></body></html>"
                    ),
                    headers=(("content-type", "text/html; charset=utf-8"),),
                ),
            ),
        ]
    )
    fed_poll = await fed_press_full_content(
        _policy(), ENABLED_SETTINGS, transport=fed_transport, clock=lambda: NOW
    ).poll()
    fed_document = await GovernmentDetailCanonicalizer().canonicalize(fed_poll.items[0].envelope)
    assert "statutory review" in fed_document.body
    assert fed_document.mentioned_names == ("National Westminster Bank Plc",)

    reference = CanonicalDocument(
        doc_id="natwest-reference",
        source_id="reference-test",
        published_at=NOW - timedelta(days=1),
        recorded_at=NOW - timedelta(days=1),
        title="National Westminster Bank Plc",
        body="Reference record for National Westminster Bank Plc.",
        document_class=DocumentClass.REFERENCE,
        mentioned_names=("National Westminster Bank Plc",),
        identifiers={"lei": "NATWEST-LEI"},
        metadata={
            "legal_name": "National Westminster Bank Plc",
            "jurisdiction": "GB",
            "sector": "bank",
        },
    )
    resolution_store = InMemoryResolutionStore()
    await resolution_store.load_reference([reference])
    await EntityResolver(resolution_store).resolve_document(
        fed_document, recorded_at=fed_document.recorded_at
    )
    assert {item.lei for item in await resolution_store.resolutions()} == {"NATWEST-LEI"}


async def test_malformed_full_content_is_archived_then_quarantined() -> None:
    policy = _policy()
    clock = TickingClock()
    transport = ScriptedSourceTransport(
        [
            (
                SEC_FEED,
                source_response(
                    200,
                    _sec_feed(),
                    headers=(("content-type", "application/atom+xml"),),
                ),
            ),
            (
                SEC_DETAIL,
                source_response(
                    200,
                    _detail("incomplete", closed=False),
                    headers=(("content-type", "text/html"),),
                ),
            ),
        ]
    )
    adapter = sec_edgar_full_content(policy, ENABLED_SETTINGS, transport=transport, clock=clock)
    archive = InMemoryRawArchive()
    operations = InMemorySourceOperationsStore()
    coordinator = SourceIngestionCoordinator(
        production_source_catalog(ENABLED_SETTINGS).require("sec_edgar_8k"),
        policy,
        adapter,
        ReplayableIngestionService(
            archive,
            GovernmentDetailCanonicalizer(),
            InMemoryIntelligenceLedger(),
            InMemoryIngestionControlStore(),
            clock=clock,
        ),
        operations,
        clock=clock,
    )

    result = await coordinator.run(requested_by="test")

    assert result.observation.quarantine_count == 1
    assert result.observation.complete is False
    assert result.observation.health is SourceHealth.DEGRADED
    assert archive.object_count() == 1  # malformed exact bytes remain replayable


async def test_feed_detail_link_is_checked_before_any_detail_request() -> None:
    policy = _policy()
    transport = ScriptedSourceTransport(
        [
            (
                SEC_FEED,
                source_response(
                    200,
                    _sec_feed("https://169.254.169.254/latest/meta-data"),
                    headers=(("content-type", "application/atom+xml"),),
                ),
            )
        ]
    )
    adapter = sec_edgar_full_content(
        policy, ENABLED_SETTINGS, transport=transport, clock=lambda: NOW
    )

    with pytest.raises(DisallowedSourceUrlError):
        await adapter.poll()
    assert len(transport.requests) == 1


def test_registered_source_policy_metadata_and_fed_factory() -> None:
    catalog = production_source_catalog(ENABLED_SETTINGS)
    sec = catalog.require("sec_edgar_8k")
    fed = catalog.require("fed_press_releases")

    assert sec.raw_retention_days >= 365
    assert sec.licence_group == "open_web_public"
    assert sec.allowed_origins == ("https://www.sec.gov",)
    assert fed.freshness_sla_seconds >= fed.cadence_seconds
    adapter = fed_press_full_content(
        _policy(), ENABLED_SETTINGS, transport=ScriptedSourceTransport([]), clock=lambda: NOW
    )
    assert adapter.source_id == "fed_press_releases"
