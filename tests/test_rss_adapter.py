"""Unit tests for the open-web RSS/Atom adapter (fi_intel/sources/adapters/rss.py).

Golden-path assertions run against fi_intel/synth/data/*_sample.xml, which
are real captures (SEC EDGAR, Federal Reserve), not synthetic data — see
that module's docstring. The malformed-entry and DST tests use small
inline XML snippets so the failure modes under test are exact and legible.
"""

from datetime import UTC, datetime

import pytest

from fi_intel.sources.adapters.rss import (
    MalformedFeedError,
    _parse_fed_rss,
    _parse_sec_edgar_atom,
    fed_press_releases_fixture,
    sec_edgar_8k_fixture,
)
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import BarrierSide, DocumentClass

FIXED_FETCH_TIME = datetime(2026, 8, 25, 0, 0, 0, tzinfo=UTC)


# ---------------------------------------------------------------------------
# Fed press releases (RSS 2.0), parsed from the real captured sample.
# ---------------------------------------------------------------------------


async def test_fed_fixture_adapter_serves_real_sample() -> None:
    adapter = fed_press_releases_fixture()
    docs = [d async for d in adapter.fetch()]
    assert len(docs) == 20
    assert all(d.source_id == "fed_press_releases_fixture" for d in docs)
    assert all(d.document_class == DocumentClass.REGULATORY for d in docs)
    assert all(d.barrier_side == BarrierSide.PUBLIC for d in docs)
    assert all(d.recorded_at >= d.published_at for d in docs)
    fed_url = "https://www.federalreserve.gov"
    assert all(d.url is not None and d.url.startswith(fed_url) for d in docs)
    assert any(d.metadata.get("category") == "Enforcement Actions" for d in docs)


def test_fed_rss_missing_link_raises() -> None:
    xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
<title>Missing link</title>
<guid>urn:test:1</guid>
<pubDate>Thu, 20 Aug 2026 20:00:00 GMT</pubDate>
</item>
</channel></rss>"""
    with pytest.raises(MalformedFeedError, match="missing title/link/pubDate"):
        _parse_fed_rss(xml, "fed_test", FIXED_FETCH_TIME)


def test_fed_rss_missing_guid_falls_back_to_link() -> None:
    xml = """<?xml version="1.0"?>
<rss version="2.0"><channel>
<item>
<title>No guid, has link</title>
<link>https://www.federalreserve.gov/x.htm</link>
<pubDate>Thu, 20 Aug 2026 20:00:00 GMT</pubDate>
</item>
</channel></rss>"""
    docs = _parse_fed_rss(xml, "fed_test", FIXED_FETCH_TIME)
    assert len(docs) == 1
    assert docs[0].doc_id == "fed_test:https://www.federalreserve.gov/x.htm"
    # description absent too -> falls back to title, still satisfies min_length=1
    assert docs[0].body == "No guid, has link"


# ---------------------------------------------------------------------------
# SEC EDGAR 8-K filings (Atom), parsed from the real captured sample.
# ---------------------------------------------------------------------------


async def test_sec_fixture_adapter_serves_real_sample() -> None:
    adapter = sec_edgar_8k_fixture()
    docs = [d async for d in adapter.fetch()]
    assert len(docs) == 10
    assert all(d.source_id == "sec_edgar_8k_fixture" for d in docs)
    assert all(d.document_class == DocumentClass.FILING for d in docs)
    assert all(d.metadata.get("form_type") == "8-K" for d in docs)
    assert all("accession-number=" in d.doc_id for d in docs)
    # Company name / CIK extracted from title for every real sample entry.
    assert all(d.mentioned_names for d in docs)
    assert all("cik" in d.identifiers for d in docs)
    # HTML markup from <summary type="html"> must be stripped, not leaked.
    assert all("<b>" not in d.body and "<br" not in d.body for d in docs)
    assert any("Filed:" in d.body for d in docs)


def test_sec_atom_missing_id_raises() -> None:
    xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>8-K - Missing Id Corp (0000000001) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/x.htm"/>
<summary type="html">Filed: 2026-08-24</summary>
<updated>2026-08-24T17:30:48-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
</entry>
</feed>"""
    with pytest.raises(MalformedFeedError, match="missing id/title/link/updated"):
        _parse_sec_edgar_atom(xml, "sec_test", FIXED_FETCH_TIME)


def test_sec_atom_title_not_matching_pattern_yields_no_mention_not_an_error() -> None:
    xml = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>Unexpected title shape</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/x.htm"/>
<summary type="html">Filed: 2026-08-24</summary>
<updated>2026-08-24T17:30:48-04:00</updated>
<id>urn:tag:sec.gov,2008:accession-number=ZZZ</id>
</entry>
</feed>"""
    docs = _parse_sec_edgar_atom(xml, "sec_test", FIXED_FETCH_TIME)
    assert len(docs) == 1
    assert docs[0].mentioned_names == ()
    assert docs[0].identifiers == {}


# ---------------------------------------------------------------------------
# Cursor correctness across a DST offset change: two SEC `updated` timestamps
# with different UTC offsets, where comparing the isoformat *strings*
# lexicographically gives the wrong chronological order. See rss.py's
# module docstring for why this matters.
# ---------------------------------------------------------------------------

_DST_ATOM = """<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry>
<title>8-K - Alpha Corp (0000000001) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/data/1/a.htm"/>
<summary type="html">Filed: 2025-11-02</summary>
<updated>2025-11-02T01:15:00-05:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=AAA</id>
</entry>
<entry>
<title>8-K - Beta Corp (0000000002) (Filer)</title>
<link rel="alternate" type="text/html" href="https://www.sec.gov/data/2/b.htm"/>
<summary type="html">Filed: 2025-11-02</summary>
<updated>2025-11-02T01:30:00-04:00</updated>
<category scheme="https://www.sec.gov/" label="form type" term="8-K"/>
<id>urn:tag:sec.gov,2008:accession-number=BBB</id>
</entry>
</feed>"""
# 2025-11-02 is the real US "fall back" DST transition (clocks go from
# 2am EDT to 1am EST), so both -04:00 and -05:00 are valid local offsets
# for a 01:xx timestamp that day. Both instants are safely in the past
# relative to any plausible test-run clock, satisfying recorded_at >=
# published_at.
# Alpha (AAA) is 2025-11-02T01:15:00-05:00 = 06:15 UTC.
# Beta  (BBB) is 2025-11-02T01:30:00-04:00 = 05:30 UTC -> chronologically
# earlier than Alpha, even though "15" < "30" makes Alpha's isoformat string
# sort first under plain text comparison. Correct order is [Beta, Alpha].


async def test_fetch_orders_by_actual_instant_not_isoformat_text() -> None:
    async def fetch_page() -> str:
        return _DST_ATOM

    from fi_intel.sources.adapters.rss import FeedAdapter

    adapter = FeedAdapter(source_id="dst_test", fetch_page=fetch_page, parse=_parse_sec_edgar_atom)
    docs = [d async for d in adapter.fetch()]
    assert [d.doc_id.rsplit("=", 1)[-1] for d in docs] == ["BBB", "AAA"]


async def test_cursor_resumption_is_dst_safe() -> None:
    async def fetch_page() -> str:
        return _DST_ATOM

    from fi_intel.sources.adapters.rss import FeedAdapter

    adapter = FeedAdapter(source_id="dst_test", fetch_page=fetch_page, parse=_parse_sec_edgar_atom)
    docs = [d async for d in adapter.fetch()]
    beta = next(d for d in docs if d.doc_id.endswith("BBB"))

    cursor = adapter.cursor_for(beta)
    resumed = [d async for d in adapter.fetch(cursor)]
    assert [d.doc_id.rsplit("=", 1)[-1] for d in resumed] == ["AAA"]


async def test_cursor_round_trips_and_foreign_cursor_rejected() -> None:
    async def fetch_page() -> str:
        return _DST_ATOM

    from fi_intel.sources.adapters.rss import FeedAdapter

    adapter = FeedAdapter(source_id="dst_test", fetch_page=fetch_page, parse=_parse_sec_edgar_atom)
    docs = [d async for d in adapter.fetch()]
    cursor = adapter.cursor_for(docs[0])
    restored = FetchCursor.model_validate_json(cursor.model_dump_json())
    resumed = [d async for d in adapter.fetch(restored)]
    assert [d.doc_id for d in resumed] == [d.doc_id for d in docs[1:]]

    foreign = FetchCursor(
        source_id="someone_else", position=cursor.position, updated_at=cursor.updated_at
    )
    with pytest.raises(ValueError, match="cursor"):
        [d async for d in adapter.fetch(foreign)]
