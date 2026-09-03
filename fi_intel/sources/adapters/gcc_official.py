"""Registered official GCC pages mapped into the canonical source boundary."""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urldefrag, urljoin, urlparse
from uuid import uuid4

from fi_intel.application.raw import RawHeader, RawSourceEnvelope
from fi_intel.config import Settings
from fi_intel.ledger.models import AccessPolicy
from fi_intel.logging import get_logger, safe_error_summary
from fi_intel.sources.acquisition import (
    DetailValidator,
    RawAcquiredItem,
    RawSourceCursor,
    RawSourcePoll,
    cursor_position,
)
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass
from fi_intel.sources.transport import (
    ConditionalRequest,
    HardenedSourceClient,
    HttpxSourceTransport,
    SourceHttpResponse,
    SourceHttpTransport,
    SourceTransportError,
)


@dataclass(frozen=True, slots=True)
class GccOfficialSource:
    source_id: str
    country: str
    display_name: str
    source_type: str
    url: str
    allowed_origins: tuple[str, ...]


GCC_OFFICIAL_SOURCES = (
    GccOfficialSource(
        "sa_sama_news",
        "Saudi Arabia",
        "Saudi Central Bank news",
        "central_bank",
        "https://sama.gov.sa/en-US/MediaCenter/News/pages/allnews.aspx",
        ("https://sama.gov.sa", "https://www.sama.gov.sa"),
    ),
    GccOfficialSource(
        "sa_cma_announcements",
        "Saudi Arabia",
        "Saudi Capital Market Authority announcements",
        "capital_markets_regulator",
        "https://cma.org.sa/en/market/news/Pages/default.aspx",
        (
            "https://cma.org.sa",
            "https://www.cma.org.sa",
            "https://cma.gov.sa",
            "https://www.cma.gov.sa",
        ),
    ),
    GccOfficialSource(
        "ae_cbuae_news",
        "United Arab Emirates",
        "Central Bank of the UAE news",
        "central_bank",
        "https://www.centralbank.ae/en/news-and-publications/news-and-insights/",
        ("https://www.centralbank.ae",),
    ),
    GccOfficialSource(
        "ae_cma_updates",
        "United Arab Emirates",
        "UAE Capital Market Authority updates",
        "capital_markets_regulator",
        "https://www.uaecma.gov.ae/en/",
        ("https://www.uaecma.gov.ae",),
    ),
    GccOfficialSource(
        "qa_qcb_news",
        "Qatar",
        "Qatar Central Bank news",
        "central_bank",
        "https://www.qcb.gov.qa/en/News/Pages/default.aspx",
        ("https://www.qcb.gov.qa",),
    ),
    GccOfficialSource(
        "qa_qfma_news",
        "Qatar",
        "Qatar Financial Markets Authority news",
        "capital_markets_regulator",
        "https://www.qfma.org.qa/English/MediaCenter/News/Pages/default.aspx",
        ("https://www.qfma.org.qa",),
    ),
    GccOfficialSource(
        "kw_cbk_press",
        "Kuwait",
        "Central Bank of Kuwait press releases",
        "central_bank",
        "https://www.cbk.gov.kw/en/cbk-news/announcements-and-press-releases/press-releases",
        ("https://www.cbk.gov.kw",),
    ),
    GccOfficialSource(
        "kw_cbk_announcements",
        "Kuwait",
        "Central Bank of Kuwait announcements",
        "official_announcements",
        "https://www.cbk.gov.kw/en/cbk-news/announcements-and-press-releases/announcements",
        ("https://www.cbk.gov.kw",),
    ),
    GccOfficialSource(
        "bh_cbb_media",
        "Bahrain",
        "Central Bank of Bahrain media centre",
        "central_bank_and_market_regulator",
        "https://www.cbb.gov.bh/media-center/",
        ("https://www.cbb.gov.bh",),
    ),
    GccOfficialSource(
        "bh_bourse_announcements",
        "Bahrain",
        "Bahrain Bourse company announcements",
        "exchange_announcements",
        "https://bahrainbourse.com/en/news%20and%20events/CompanyAnnouncements",
        ("https://bahrainbourse.com", "https://www.bahrainbourse.com"),
    ),
    GccOfficialSource(
        "om_cbo_news",
        "Oman",
        "Central Bank of Oman news",
        "central_bank",
        "https://cbo.gov.om/Pages/home.aspx",
        ("https://cbo.gov.om", "https://www.cbo.gov.om"),
    ),
    GccOfficialSource(
        "om_fsa_news",
        "Oman",
        "Oman Financial Services Authority news",
        "capital_markets_regulator",
        "https://fsa.gov.om/Home/News/",
        ("https://fsa.gov.om", "https://www.fsa.gov.om"),
    ),
)


class _VisiblePageParser(HTMLParser):
    _HIDDEN = frozenset({"script", "style", "svg", "noscript", "template"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.visible_parts: list[str] = []
        self.links: list[str] = []
        self.published_candidates: list[str] = []
        self._hidden_depth = 0
        self._in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lowered = tag.casefold()
        attributes = {name.casefold(): value for name, value in attrs if value is not None}
        if lowered == "a" and attributes.get("href"):
            self.links.append(attributes["href"])
        if lowered == "time" and attributes.get("datetime"):
            self.published_candidates.append(attributes["datetime"])
        if lowered == "meta":
            marker = (attributes.get("property") or attributes.get("name") or "").casefold()
            if marker in {
                "article:published_time",
                "date",
                "datepublished",
                "dc.date",
                "publishdate",
            } and attributes.get("content"):
                self.published_candidates.append(attributes["content"])
        if lowered in self._HIDDEN:
            self._hidden_depth += 1
        if lowered == "title":
            self._in_title = True

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.casefold()
        if lowered in self._HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1
        if lowered == "title":
            self._in_title = False

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        clean = " ".join(data.split())
        if clean:
            self.visible_parts.append(clean)
            if self._in_title:
                self.title_parts.append(clean)


def _decode(payload: bytes, media_type: str) -> str:
    charset = "utf-8"
    for item in media_type.split(";")[1:]:
        key, _, value = item.strip().partition("=")
        if key.casefold() == "charset" and value:
            charset = value.strip("\"'")
    return payload.decode(charset, errors="strict")


def _canonicalize_payload(
    source: GccOfficialSource,
    payload: bytes,
    media_type: str,
    fetched_at: datetime,
    published_at: datetime,
    char_limit: int,
    *,
    doc_id: str,
    url: str,
) -> CanonicalDocument:
    if not media_type.casefold().startswith(("text/html", "application/xhtml+xml", "text/plain")):
        raise ValueError(f"unsupported official source media type: {media_type or 'missing'}")
    decoded = _decode(payload, media_type)
    parser = _VisiblePageParser()
    if media_type.casefold().startswith("text/plain"):
        title = source.display_name
        visible = " ".join(decoded.split())
    else:
        parser.feed(decoded)
        title = " ".join(parser.title_parts) or source.display_name
        visible = " ".join(parser.visible_parts)
    if len(visible) < 200:
        raise ValueError("official source returned too little visible text")
    raw_hash = hashlib.sha256(payload).hexdigest()
    bounded = visible[:char_limit]
    return CanonicalDocument(
        doc_id=doc_id,
        source_id=source.source_id,
        published_at=published_at,
        recorded_at=fetched_at,
        title=title,
        body=bounded,
        language="ar" if any("\u0600" <= char <= "\u06ff" for char in bounded) else "en",
        document_class=DocumentClass.REGULATORY,
        url=url,
        metadata={
            "country": source.country,
            "source_type": source.source_type,
            "raw_content_hash": raw_hash,
        },
    )


def _content_location(response: SourceHttpResponse, fallback: str) -> str:
    return response.final_url or fallback


def _published_at(response: SourceHttpResponse, parser: _VisiblePageParser | None) -> datetime:
    candidates = list(parser.published_candidates if parser is not None else ())
    last_modified = response.header("last-modified")
    if last_modified:
        candidates.append(last_modified)
    for value in candidates:
        try:
            parsed = (
                parsedate_to_datetime(value)
                if "," in value
                else datetime.fromisoformat(value.replace("Z", "+00:00"))
            )
        except (TypeError, ValueError, OverflowError):
            continue
        if parsed.tzinfo is not None and parsed.utcoffset() is not None:
            normalized = parsed.astimezone(UTC)
            if normalized <= response.fetched_at:
                return normalized
    return response.fetched_at


def _detail_urls(
    source: GccOfficialSource,
    response: SourceHttpResponse,
    parser: _VisiblePageParser,
    limit: int,
) -> tuple[str, ...]:
    origins = {
        (urlparse(origin).scheme.casefold(), urlparse(origin).netloc.casefold())
        for origin in source.allowed_origins
    }
    excluded_suffixes = (
        ".css",
        ".js",
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
        ".svg",
        ".ico",
        ".zip",
        ".mp4",
        ".mp3",
        ".pdf",
    )
    keywords = (
        "news",
        "announcement",
        "press",
        "media",
        "release",
        "article",
        "detail",
        "publication",
        "decision",
        "circular",
        "companyannouncement",
    )
    landing = urldefrag(_content_location(response, source.url)).url
    admitted: list[str] = []
    for href in parser.links:
        candidate = urldefrag(urljoin(landing, href.strip())).url
        parsed = urlparse(candidate)
        if (
            parsed.scheme.casefold(),
            parsed.netloc.casefold(),
        ) not in origins or candidate == landing:
            continue
        lowered = candidate.casefold()
        if lowered.endswith(excluded_suffixes):
            continue
        if not any(keyword in lowered for keyword in keywords) and not parsed.query:
            continue
        if candidate not in admitted:
            admitted.append(candidate)
        if len(admitted) >= limit:
            break
    return tuple(admitted)


class OfficialGccRawAdapter:
    """Acquire a landing page and bounded same-origin announcement details."""

    def __init__(
        self,
        source: GccOfficialSource,
        settings: Settings,
        policy: AccessPolicy,
        *,
        transport: SourceHttpTransport | None = None,
        clock: Callable[[], datetime] | None = None,
        run_id: str | None = None,
    ) -> None:
        self._source = source
        self._settings = settings
        self._policy = policy
        self._log = get_logger(component="gcc-official-source-adapter")
        self._run_id = run_id or f"source-poll:{self.source_id}:{uuid4()}"
        self._client = HardenedSourceClient(
            transport
            or HttpxSourceTransport(verify=self._settings.source_tls_verify),
            allowed_origins=self._source.allowed_origins,
            user_agent=self._settings.rss_user_agent,
            timeout_seconds=self._settings.source_http_timeout_seconds,
            max_attempts=self._settings.source_http_max_attempts,
            max_redirects=self._settings.source_http_max_redirects,
            clock=clock,
            log_context={"run_id": self._run_id, "source_id": self.source_id},
        )

    @property
    def source_id(self) -> str:
        return self._source.source_id

    async def poll(  # noqa: C901
        self, cursor: RawSourceCursor | None = None
    ) -> RawSourcePoll:
        # Individual HTTP attempts are bounded, but a landing page can lead to
        # many sequential detail requests. Bound the complete poll as well so
        # degraded connectivity becomes a durable failed observation instead
        # of leaving the source stage working for many minutes.
        async with asyncio.timeout(self._settings.source_poll_timeout_seconds):
            return await self._poll_within_deadline(cursor)

    async def _poll_within_deadline(  # noqa: C901
        self, cursor: RawSourceCursor | None = None
    ) -> RawSourcePoll:
        if cursor is not None and cursor.source_id != self.source_id:
            raise ValueError("official GCC cursor belongs to another source")
        prior = {
            item.external_id: item
            for item in (cursor.detail_validators if cursor is not None else ())
        }
        response = await self._client.fetch(
            self._source.url,
            max_bytes=self._settings.source_max_detail_bytes,
            accept="text/html, application/xhtml+xml, text/plain",
            conditional=(
                ConditionalRequest(cursor.feed_etag, cursor.feed_last_modified)
                if cursor is not None
                else None
            ),
        )
        prior_sequence = cursor.sequence_number if cursor is not None else 0
        sequence = prior_sequence
        items: list[RawAcquiredItem] = []
        validators: list[DetailValidator] = []
        unchanged = 0
        failed = 0
        feed_hash = hashlib.sha256()
        latest = cursor.latest_source_published_at if cursor is not None else None
        landing_previous = prior.get("landing-page")
        if response.not_modified:
            if landing_previous is None:
                raise SourceTransportError("official landing page returned 304 without prior state")
            validators.append(landing_previous)
            unchanged += 1
            detail_urls = tuple(
                item.detail_url
                for item in prior.values()
                if item.external_id != "landing-page" and item.detail_url
            )[: self._settings.gcc_source_max_detail_pages]
        else:
            landing_revision = hashlib.sha256(response.payload).hexdigest()
            landing_parser = _VisiblePageParser()
            landing_parser.feed(_decode(response.payload, response.header("content-type") or ""))
            landing_url = _content_location(response, self._source.url)
            validators.append(
                DetailValidator(
                    external_id="landing-page",
                    source_revision=landing_revision,
                    etag=response.header("etag"),
                    last_modified=response.header("last-modified"),
                    detail_url=landing_url,
                )
            )
            feed_hash.update(response.payload)
            landing_published_at = _published_at(response, landing_parser)
            latest = landing_published_at if latest is None else max(latest, landing_published_at)
            if (
                landing_previous is not None
                and landing_previous.source_revision == landing_revision
            ):
                unchanged += 1
            else:
                sequence += 1
                items.append(
                    self._acquired_item(
                        response,
                        external_id="landing-page",
                        revision=landing_revision,
                        sequence=sequence,
                        published_at=landing_published_at,
                    )
                )
            detail_urls = _detail_urls(
                self._source,
                response,
                landing_parser,
                self._settings.gcc_source_max_detail_pages,
            )
        for detail_url in detail_urls:
            external_id = f"detail-{hashlib.sha256(detail_url.encode()).hexdigest()[:24]}"
            previous = prior.get(external_id)
            try:
                detail = await self._client.fetch(
                    detail_url,
                    max_bytes=self._settings.source_max_detail_bytes,
                    accept="text/html, application/xhtml+xml, text/plain",
                    conditional=(
                        ConditionalRequest(previous.etag, previous.last_modified)
                        if previous is not None
                        else None
                    ),
                )
            except SourceTransportError as exc:
                if previous is not None:
                    validators.append(previous)
                failed += 1
                self._log.warning(
                    "source.detail.fetch_failed",
                    run_id=self._run_id,
                    source_id=self.source_id,
                    source_url=self._source.url,
                    detail_url=detail_url,
                    error_type=type(exc).__name__,
                    safe_error_summary=safe_error_summary(exc),
                    error_message=str(exc),
                )
                continue
            if detail.not_modified:
                if previous is None:
                    continue
                validators.append(previous)
                unchanged += 1
                continue
            revision = hashlib.sha256(detail.payload).hexdigest()
            validators.append(
                DetailValidator(
                    external_id=external_id,
                    source_revision=revision,
                    etag=detail.header("etag"),
                    last_modified=detail.header("last-modified"),
                    detail_url=detail_url,
                )
            )
            feed_hash.update(detail.payload)
            parser = _VisiblePageParser()
            parser.feed(_decode(detail.payload, detail.header("content-type") or ""))
            published_at = _published_at(detail, parser)
            latest = published_at if latest is None else max(latest, published_at)
            if previous is not None and previous.source_revision == revision:
                unchanged += 1
                continue
            sequence += 1
            items.append(
                self._acquired_item(
                    detail,
                    external_id=external_id,
                    revision=revision,
                    sequence=sequence,
                    published_at=published_at,
                    fallback_url=detail_url,
                )
            )
        next_cursor = RawSourceCursor(
            source_id=self.source_id,
            sequence_number=sequence,
            feed_etag=(
                response.header("etag")
                if not response.not_modified
                else (landing_previous.etag if landing_previous is not None else None)
            ),
            feed_last_modified=(
                response.header("last-modified")
                if not response.not_modified
                else (landing_previous.last_modified if landing_previous is not None else None)
            ),
            detail_validators=tuple(validators),
            latest_source_published_at=latest,
            updated_at=response.fetched_at,
        )
        return RawSourcePoll(
            source_id=self.source_id,
            polled_at=response.fetched_at,
            feed_modified=bool(items),
            feed_content_hash=feed_hash.hexdigest(),
            page_count=1 + len(detail_urls),
            discovered_count=1 + len(detail_urls),
            unchanged_count=unchanged,
            failed_count=failed,
            items=tuple(items),
            next_cursor=next_cursor,
        )

    def _acquired_item(
        self,
        response: SourceHttpResponse,
        *,
        external_id: str,
        revision: str,
        sequence: int,
        published_at: datetime,
        fallback_url: str | None = None,
    ) -> RawAcquiredItem:
        location = _content_location(response, fallback_url or self._source.url)
        envelope = RawSourceEnvelope(
            source_id=self.source_id,
            external_id=external_id,
            source_revision=revision,
            payload=response.payload,
            media_type=response.header("content-type") or "application/octet-stream",
            headers=tuple(RawHeader(name=key, value=value) for key, value in response.headers)
            + (RawHeader(name="content-location", value=location),),
            fetched_at=response.fetched_at,
            source_published_at=published_at,
            access_policy=self._policy,
        )
        return RawAcquiredItem(
            envelope=envelope,
            cursor_position=cursor_position(self.source_id, external_id, revision),
            sequence_number=sequence,
        )

    async def close(self) -> None:
        await self._client.close()


class GccOfficialCanonicalizer:
    def __init__(self, source: GccOfficialSource, settings: Settings) -> None:
        self._source = source
        self._settings = settings

    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument:
        if envelope.source_id != self._source.source_id:
            raise ValueError("canonicalizer received another source")
        return _canonicalize_payload(
            self._source,
            envelope.payload,
            envelope.media_type,
            envelope.fetched_at,
            envelope.source_published_at or envelope.fetched_at,
            self._settings.gcc_source_char_limit,
            doc_id=envelope.external_id,
            url=next(
                (
                    header.value
                    for header in envelope.headers
                    if header.name.casefold() == "content-location"
                ),
                self._source.url,
            ),
        )


__all__ = [
    "GCC_OFFICIAL_SOURCES",
    "GccOfficialCanonicalizer",
    "GccOfficialSource",
    "OfficialGccRawAdapter",
]
