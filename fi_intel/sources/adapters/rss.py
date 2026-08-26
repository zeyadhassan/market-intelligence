"""Open-web RSS/Atom adapters: SEC EDGAR 8-K filings, Fed press releases.

These freely published government feeds are registered separately from
licensed vendor content. Their source registry entries use
``licence_group='open_web_public'`` (see deploy/init.sql).

Parsing uses xml.etree and email.utils. The samples in
fi_intel/synth/data/*_sample.xml provide network-free fixtures.

SEC.gov rejects requests with no identifying User-Agent (403), per its fair
access policy: https://www.sec.gov/search-filings/edgar-search-assistance.
The Fed feed doesn't require one but is sent the same header out of
politeness. The value is a Settings field, never hardcoded here — do not
default it to a real person's contact info (see fi_intel/config.py).

Cursor positions are encoded as a JSON [published_at_isoformat, doc_id]
pair, not an array index. A live feed's window slides (old entries roll
off, new ones appear at the front) so "position 7" does not name the same
entry across two polls the way it does for a static fixture file; encoding
the last-seen (timestamp, doc_id) and resuming strictly after it is stable
under that reshuffling. Comparison is done on parsed datetimes, not on the
isoformat strings themselves — SEC's `updated` field carries a local
Eastern offset that changes across DST, and two isoformat strings with
different UTC offsets do not sort correctly as plain text.
"""

import json
import re
import xml.etree.ElementTree as ET
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from importlib import resources
from typing import Any

import httpx
from tenacity import retry, retry_if_exception, stop_after_attempt, wait_exponential

from fi_intel.config import Settings
from fi_intel.logging import get_logger
from fi_intel.sources.base import FetchCursor
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass

ParseFn = Callable[[str, str, datetime], list[CanonicalDocument]]
FetchPageFn = Callable[[], Awaitable[str]]


class MalformedFeedError(ValueError):
    """A feed entry could not be mapped to CanonicalDocument.

    Raised rather than skipped because silent data loss would corrupt
    backtests that span the gap.
    """


# ---------------------------------------------------------------------------
# Generic engine: cursor/fetch plumbing shared by every feed shape. Parsing
# differs per source (see the two _parse_* functions below) and is injected.
# ---------------------------------------------------------------------------


class FeedAdapter:
    """Serves canonical documents parsed from one fetched feed page.

    ``fetch_page`` is the only network-shaped seam: a fixture-backed
    instance injects a function that reads a packaged sample file, a real
    instance injects one that does an httpx GET. Nothing else about this
    class changes between the two (mirrors vendor_stub.py's ``_page``
    convention).
    """

    def __init__(self, source_id: str, fetch_page: FetchPageFn, parse: ParseFn) -> None:
        self._source_id = source_id
        self._fetch_page = fetch_page
        self._parse = parse
        self._entries: list[CanonicalDocument] | None = None

    @property
    def source_id(self) -> str:
        return self._source_id

    async def _load(self) -> list[CanonicalDocument]:
        if self._entries is None:
            page = await self._fetch_page()
            fetch_time = datetime.now(UTC)
            parsed = self._parse(page, self._source_id, fetch_time)
            self._entries = sorted(parsed, key=lambda d: (d.published_at, d.doc_id))
        return self._entries

    async def fetch(self, cursor: FetchCursor | None = None) -> Any:
        if cursor is not None and cursor.source_id != self._source_id:
            msg = f"cursor for {cursor.source_id!r} passed to {self._source_id!r}"
            raise ValueError(msg)
        threshold: tuple[datetime, str] | None = None
        if cursor is not None:
            raw_ts, raw_doc_id = json.loads(cursor.position)
            threshold = (datetime.fromisoformat(raw_ts), raw_doc_id)
        for entry in await self._load():
            if threshold is not None and (entry.published_at, entry.doc_id) <= threshold:
                continue
            yield entry

    def cursor_for(self, doc: CanonicalDocument) -> FetchCursor:
        if self._entries is None:
            msg = "cursor_for called before fetch() has run"
            raise RuntimeError(msg)
        match = next((d for d in self._entries if d.doc_id == doc.doc_id), None)
        if match is None:
            msg = f"doc_id {doc.doc_id!r} not served by {self._source_id!r}"
            raise ValueError(msg)
        position = json.dumps([match.published_at.isoformat(), match.doc_id])
        return FetchCursor(source_id=self._source_id, position=position, updated_at=doc.recorded_at)


# ---------------------------------------------------------------------------
# Fed press releases: RSS 2.0. Verified live shape (2026-08-25):
#   <item><title/><link/><guid/><description/><category/><pubDate/></item>
# ---------------------------------------------------------------------------


def _parse_fed_rss(xml_text: str, source_id: str, fetch_time: datetime) -> list[CanonicalDocument]:
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 (trusted federalreserve.gov feed, not user input)
    except ET.ParseError as exc:
        msg = f"feed body is not well-formed XML: {exc}"
        raise MalformedFeedError(msg) from exc

    docs: list[CanonicalDocument] = []
    for item in root.findall("channel/item"):
        title = item.findtext("title")
        link = item.findtext("link")
        pub_date_raw = item.findtext("pubDate")
        if not title or not link or not pub_date_raw:
            raw = ET.tostring(item, encoding="unicode")
            msg = f"fed press item missing title/link/pubDate: {raw}"
            raise MalformedFeedError(msg)
        try:
            published_at = parsedate_to_datetime(pub_date_raw)
        except (TypeError, ValueError) as exc:
            msg = f"fed press item has unparseable pubDate {pub_date_raw!r}"
            raise MalformedFeedError(msg) from exc
        if published_at.tzinfo is None:
            msg = f"fed press item pubDate {pub_date_raw!r} has no timezone"
            raise MalformedFeedError(msg)

        guid = item.findtext("guid") or link
        description = item.findtext("description") or title
        category = item.findtext("category")

        docs.append(
            CanonicalDocument(
                doc_id=f"{source_id}:{guid}",
                source_id=source_id,
                title=title.strip(),
                body=description.strip(),
                published_at=published_at,
                recorded_at=fetch_time,
                document_class=DocumentClass.REGULATORY,
                url=link.strip(),
                metadata={"category": category.strip()} if category else {},
            )
        )
    return docs


def fed_press_releases(settings: Settings | None = None) -> FeedAdapter:
    """Legacy summary-only adapter retained for the v1 SourceAdapter path.

    Production raw-first ingestion uses ``government.fed_press_full_content``.
    """
    active = settings or Settings()

    async def fetch_page() -> str:
        return await _http_fetch_page(active.fed_press_feed_url, active.rss_user_agent)

    return FeedAdapter(source_id="fed_press_releases", fetch_page=fetch_page, parse=_parse_fed_rss)


def fed_press_releases_fixture() -> FeedAdapter:
    """Fixture-backed variant over a real captured sample. No network;
    used by the adapter contract test and unit tests."""

    async def fetch_page() -> str:
        return _read_sample("fed_press_releases_sample.xml")

    return FeedAdapter(
        source_id="fed_press_releases_fixture", fetch_page=fetch_page, parse=_parse_fed_rss
    )


# ---------------------------------------------------------------------------
# SEC EDGAR current 8-K filings: Atom. Verified live shape (2026-08-25):
#   <entry><title/><link rel="alternate" href=.../><summary type="html"/>
#          <updated/><category term="8-K"/><id/></entry>
# Title is "<form> - <company name> (<CIK>) (<role>)"; company name/CIK are
# decorative enrichment (mentioned_names/identifiers), not required fields —
# a title that doesn't match the pattern still yields a valid document with
# no extracted name, it does not fail the whole record.
# ---------------------------------------------------------------------------

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_SEC_TITLE_RE = re.compile(r"^\S+(?:/\S+)?\s+-\s+(?P<name>.+)\s+\((?P<cik>\d+)\)\s+\([^)]+\)$")
_HTML_BR_RE = re.compile(r"<br\s*/?>", re.IGNORECASE)
_HTML_TAG_RE = re.compile(r"</?[a-zA-Z][^>]*>")


def _clean_html_summary(raw: str) -> str:
    text = _HTML_BR_RE.sub("\n", raw)
    text = _HTML_TAG_RE.sub("", text)
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return "\n".join(lines)


def _parse_sec_edgar_atom(
    xml_text: str, source_id: str, fetch_time: datetime
) -> list[CanonicalDocument]:
    try:
        root = ET.fromstring(xml_text)  # noqa: S314 (trusted sec.gov feed, not user input)
    except ET.ParseError as exc:
        msg = f"feed body is not well-formed XML: {exc}"
        raise MalformedFeedError(msg) from exc

    docs: list[CanonicalDocument] = []
    for entry in root.findall("atom:entry", _ATOM_NS):
        entry_id = entry.findtext("atom:id", namespaces=_ATOM_NS)
        title = entry.findtext("atom:title", namespaces=_ATOM_NS)
        link_el = entry.find("atom:link", _ATOM_NS)
        href = link_el.get("href") if link_el is not None else None
        updated_raw = entry.findtext("atom:updated", namespaces=_ATOM_NS)
        if not entry_id or not title or not href or not updated_raw:
            raw = ET.tostring(entry, encoding="unicode")
            msg = f"sec edgar entry missing id/title/link/updated: {raw}"
            raise MalformedFeedError(msg)
        try:
            published_at = datetime.fromisoformat(updated_raw)
        except ValueError as exc:
            msg = f"sec edgar entry has unparseable updated {updated_raw!r}"
            raise MalformedFeedError(msg) from exc
        if published_at.tzinfo is None:
            msg = f"sec edgar entry updated {updated_raw!r} has no timezone"
            raise MalformedFeedError(msg)

        summary_raw = entry.findtext("atom:summary", namespaces=_ATOM_NS) or ""
        category_el = entry.find("atom:category", _ATOM_NS)
        form_type = category_el.get("term") if category_el is not None else None

        title_match = _SEC_TITLE_RE.match(title)
        mentioned_names: tuple[str, ...] = ()
        identifiers: dict[str, str] = {}
        if title_match:
            mentioned_names = (title_match.group("name").strip(),)
            identifiers = {"cik": title_match.group("cik")}

        docs.append(
            CanonicalDocument(
                doc_id=f"{source_id}:{entry_id}",
                source_id=source_id,
                title=title.strip(),
                body=_clean_html_summary(summary_raw) or title.strip(),
                published_at=published_at,
                recorded_at=fetch_time,
                document_class=DocumentClass.FILING,
                url=href.strip(),
                mentioned_names=mentioned_names,
                identifiers=identifiers,
                metadata={"form_type": form_type} if form_type else {},
            )
        )
    return docs


def sec_edgar_8k(settings: Settings | None = None) -> FeedAdapter:
    """Legacy summary-only adapter retained for the v1 SourceAdapter path.

    Production raw-first ingestion uses ``government.sec_edgar_full_content``.
    """
    active = settings or Settings()

    async def fetch_page() -> str:
        return await _http_fetch_page(active.sec_edgar_feed_url, active.rss_user_agent)

    return FeedAdapter(source_id="sec_edgar_8k", fetch_page=fetch_page, parse=_parse_sec_edgar_atom)


def sec_edgar_8k_fixture() -> FeedAdapter:
    """Fixture-backed variant over a real captured sample. No network;
    used by the adapter contract test and unit tests."""

    async def fetch_page() -> str:
        return _read_sample("sec_edgar_8k_sample.xml")

    return FeedAdapter(
        source_id="sec_edgar_8k_fixture", fetch_page=fetch_page, parse=_parse_sec_edgar_atom
    )


# ---------------------------------------------------------------------------
# Shared fetch helpers.
# ---------------------------------------------------------------------------


def _read_sample(name: str) -> str:
    ref = resources.files("fi_intel.synth.data").joinpath(name)
    return ref.read_text(encoding="utf-8")


def _retryable(exc: BaseException) -> bool:
    if isinstance(exc, httpx.TransportError):
        return True
    if isinstance(exc, httpx.HTTPStatusError):
        return exc.response.status_code >= 500
    return False


@retry(
    reraise=True,
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=1, min=1, max=8),
    retry=retry_if_exception(_retryable),
)
async def _http_fetch_page(url: str, user_agent: str) -> str:
    log = get_logger(component="rss_adapter", url=url)
    log.info("feed.fetch.start")
    async with httpx.AsyncClient(timeout=15.0) as client:
        response = await client.get(url, headers={"User-Agent": user_agent})
        response.raise_for_status()
        text = response.text
    log.info("feed.fetch.done", status=response.status_code, bytes=len(text))
    return text
