"""Raw-first, two-stage acquisition for SEC and Federal Reserve sources."""

from __future__ import annotations

import hashlib
import re
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from urllib.parse import urlsplit

from fi_intel.application.raw import RawHeader, RawSourceEnvelope
from fi_intel.config import Settings
from fi_intel.ledger.models import AccessPolicy
from fi_intel.sources.acquisition import (
    DetailValidator,
    RawAcquiredItem,
    RawSourceCursor,
    RawSourcePoll,
    cursor_position,
)
from fi_intel.sources.canonical import CanonicalDocument, DocumentClass
from fi_intel.sources.catalog import SourceKind, SourceRegistration, production_source_catalog
from fi_intel.sources.transport import (
    ConditionalRequest,
    HardenedSourceClient,
    HttpxSourceTransport,
    SourceHttpTransport,
    SourceTransportError,
)

_ATOM_NS = {"atom": "http://www.w3.org/2005/Atom"}
_FORBIDDEN_XML = re.compile(rb"<!\s*(?:DOCTYPE|ENTITY)\b", re.IGNORECASE)
_SEC_TITLE_RE = re.compile(r"^\S+(?:/\S+)?\s+-\s+(?P<name>.+)\s+\((?P<cik>\d+)\)\s+\([^)]+\)$")
_FED_ENTITY_MARKER_RE = re.compile(
    r"\b(?:application by|actions? with)\s+(?:the\s+)?(?P<names>.+)$",
    re.IGNORECASE,
)


class MalformedDiscoveryError(ValueError):
    """A feed or required discovery field violated its source contract."""


class MalformedDetailError(ValueError):
    """A complete detail response could not become a canonical document."""


class PartialDetailError(MalformedDetailError):
    """A detail payload is empty or structurally truncated."""


@dataclass(frozen=True, slots=True)
class DiscoveredDetail:
    external_id: str
    url: str
    title: str
    published_at: datetime


DiscoveryParser = Callable[[bytes, str], tuple[DiscoveredDetail, ...]]


class FeedDetailRawAdapter:
    """Fetch a discovery feed, then exact full content for changed entries."""

    def __init__(
        self,
        registration: SourceRegistration,
        access_policy: AccessPolicy,
        client: HardenedSourceClient,
        parser: DiscoveryParser,
    ) -> None:
        if registration.kind is not SourceKind.FEED_DETAIL:
            raise ValueError("feed-detail adapter requires a feed_detail registration")
        if not registration.enabled:
            raise ValueError(f"source {registration.source_id!r} is disabled by coverage policy")
        if registration.barrier_side is not access_policy.barrier_side:
            raise ValueError("source registration and access policy barriers differ")
        if not access_policy.allowed_entitlement_groups.issubset(
            registration.allowed_entitlement_groups
        ):
            raise ValueError("access policy contains an unregistered entitlement group")
        self._registration = registration
        self._policy = access_policy
        self._client = client
        self._parser = parser

    @property
    def source_id(self) -> str:
        return self._registration.source_id

    async def poll(self, cursor: RawSourceCursor | None = None) -> RawSourcePoll:
        self._validate_cursor(cursor)
        base_sequence = cursor.sequence_number if cursor is not None else 0
        feed = await self._client.fetch(
            self._registration.discovery_url,
            max_bytes=self._registration.max_feed_bytes,
            conditional=(
                ConditionalRequest(cursor.feed_etag, cursor.feed_last_modified)
                if cursor is not None
                else None
            ),
            accept="application/atom+xml, application/rss+xml, application/xml, text/xml",
        )
        if feed.not_modified:
            if cursor is None:
                raise SourceTransportError("feed returned 304 without prior validator state")
            next_cursor = cursor.model_copy(update={"updated_at": feed.fetched_at})
            return RawSourcePoll(
                source_id=self.source_id,
                partition_key=cursor.partition_key,
                polled_at=feed.fetched_at,
                feed_modified=False,
                page_count=1,
                discovered_count=0,
                unchanged_count=0,
                items=(),
                next_cursor=next_cursor,
            )

        discovered = self._parser(feed.payload, self.source_id)
        prior = {
            item.external_id: item
            for item in (cursor.detail_validators if cursor is not None else ())
        }
        current: dict[str, DetailValidator] = {}
        acquired: list[RawAcquiredItem] = []
        unchanged = 0
        sequence = base_sequence
        for entry in discovered:
            previous = prior.get(entry.external_id)
            detail = await self._client.fetch(
                entry.url,
                max_bytes=self._registration.max_detail_bytes,
                conditional=(
                    ConditionalRequest(previous.etag, previous.last_modified)
                    if previous is not None
                    else None
                ),
                accept="text/html, text/plain, application/xhtml+xml",
            )
            if detail.not_modified:
                if previous is None:
                    raise SourceTransportError("detail returned 304 without prior validator state")
                current[entry.external_id] = previous
                unchanged += 1
                continue
            if not detail.payload:
                raise PartialDetailError(f"empty detail payload for {entry.external_id}")
            digest = hashlib.sha256(detail.payload).hexdigest()
            revision = f"sha256:{digest}"
            validator = DetailValidator(
                external_id=entry.external_id,
                source_revision=revision,
                etag=detail.header("etag"),
                last_modified=detail.header("last-modified"),
            )
            current[entry.external_id] = validator
            if previous is not None and previous.source_revision == revision:
                unchanged += 1
                continue
            sequence += 1
            headers = tuple(RawHeader(name=name, value=value) for name, value in detail.headers) + (
                RawHeader(name="content-location", value=detail.final_url),
                RawHeader(name="x-fi-intel-discovery-title", value=entry.title),
            )
            envelope = RawSourceEnvelope(
                source_id=self.source_id,
                external_id=entry.external_id,
                source_revision=revision,
                payload=detail.payload,
                media_type=detail.header("content-type") or "application/octet-stream",
                headers=headers,
                fetched_at=detail.fetched_at,
                source_published_at=entry.published_at,
                access_policy=self._policy,
            )
            acquired.append(
                RawAcquiredItem(
                    envelope=envelope,
                    cursor_position=cursor_position(self.source_id, entry.external_id, revision),
                    sequence_number=sequence,
                )
            )

        history = self._bounded_history(current, prior)
        previous_latest = cursor.latest_source_published_at if cursor is not None else None
        publication_times = [item.published_at for item in discovered]
        if previous_latest is not None:
            publication_times.append(previous_latest)
        current_latest = max(publication_times, default=None)
        next_cursor = RawSourceCursor(
            source_id=self.source_id,
            partition_key=cursor.partition_key if cursor is not None else "default",
            sequence_number=sequence,
            feed_etag=feed.header("etag"),
            feed_last_modified=feed.header("last-modified"),
            detail_validators=history,
            latest_source_published_at=current_latest,
            updated_at=feed.fetched_at,
        )
        return RawSourcePoll(
            source_id=self.source_id,
            partition_key=next_cursor.partition_key,
            polled_at=feed.fetched_at,
            feed_modified=True,
            feed_content_hash=hashlib.sha256(feed.payload).hexdigest(),
            page_count=1,
            discovered_count=len(discovered),
            unchanged_count=unchanged,
            items=tuple(acquired),
            next_cursor=next_cursor,
        )

    async def close(self) -> None:
        await self._client.close()

    def _validate_cursor(self, cursor: RawSourceCursor | None) -> None:
        if cursor is not None and cursor.source_id != self.source_id:
            raise ValueError(f"cursor for {cursor.source_id!r} passed to {self.source_id!r}")

    def _bounded_history(
        self,
        current: dict[str, DetailValidator],
        prior: dict[str, DetailValidator],
    ) -> tuple[DetailValidator, ...]:
        ordered = list(current.values())
        ordered.extend(prior[key] for key in sorted(prior) if key not in current)
        return tuple(ordered[: self._registration.cursor_history_limit])


def parse_fed_discovery(payload: bytes, source_id: str) -> tuple[DiscoveredDetail, ...]:
    root = _parse_xml(payload)
    entries: list[DiscoveredDetail] = []
    for item in root.findall("channel/item"):
        title = item.findtext("title")
        link = item.findtext("link")
        published_raw = item.findtext("pubDate")
        if not title or not link or not published_raw:
            raise MalformedDiscoveryError("Fed feed item is missing title, link, or pubDate")
        try:
            published_at = parsedate_to_datetime(published_raw)
        except (TypeError, ValueError) as exc:
            raise MalformedDiscoveryError(
                f"Fed feed pubDate is invalid: {published_raw!r}"
            ) from exc
        if published_at.tzinfo is None:
            raise MalformedDiscoveryError("Fed feed pubDate has no timezone")
        guid = item.findtext("guid") or link
        entries.append(
            DiscoveredDetail(
                external_id=f"{source_id}:{guid.strip()}",
                url=link.strip(),
                title=title.strip(),
                published_at=published_at,
            )
        )
    return _unique_entries(entries)


def parse_sec_discovery(payload: bytes, source_id: str) -> tuple[DiscoveredDetail, ...]:
    root = _parse_xml(payload)
    entries: list[DiscoveredDetail] = []
    for item in root.findall("atom:entry", _ATOM_NS):
        entry_id = item.findtext("atom:id", namespaces=_ATOM_NS)
        title = item.findtext("atom:title", namespaces=_ATOM_NS)
        published_raw = item.findtext("atom:updated", namespaces=_ATOM_NS)
        links = item.findall("atom:link", _ATOM_NS)
        alternate = next(
            (link for link in links if link.get("rel", "alternate") == "alternate"),
            None,
        )
        href = alternate.get("href") if alternate is not None else None
        if not entry_id or not title or not href or not published_raw:
            raise MalformedDiscoveryError(
                "SEC feed entry is missing id, title, alternate link, or updated"
            )
        try:
            published_at = datetime.fromisoformat(published_raw)
        except ValueError as exc:
            raise MalformedDiscoveryError(
                f"SEC feed updated timestamp is invalid: {published_raw!r}"
            ) from exc
        if published_at.tzinfo is None:
            raise MalformedDiscoveryError("SEC feed updated timestamp has no timezone")
        entries.append(
            DiscoveredDetail(
                external_id=f"{source_id}:{entry_id.strip()}",
                url=href.strip(),
                title=title.strip(),
                published_at=published_at,
            )
        )
    return _unique_entries(entries)


class GovernmentDetailCanonicalizer:
    """Strictly canonicalize full SEC/Fed detail payloads, never summaries."""

    _CLASSES = {
        "sec_edgar_8k": DocumentClass.FILING,
        "fed_press_releases": DocumentClass.REGULATORY,
    }

    async def canonicalize(self, envelope: RawSourceEnvelope) -> CanonicalDocument:
        try:
            document_class = self._CLASSES[envelope.source_id]
        except KeyError as exc:
            raise MalformedDetailError(
                f"unsupported government detail source {envelope.source_id!r}"
            ) from exc
        if envelope.source_published_at is None:
            raise MalformedDetailError("detail envelope is missing source publication time")
        if envelope.fetched_at < envelope.source_published_at:
            raise MalformedDetailError("detail was fetched before its publication time")
        media_type = envelope.media_type.lower()
        if not (
            media_type.startswith("text/html")
            or media_type.startswith("application/xhtml+xml")
            or media_type.startswith("text/plain")
        ):
            raise MalformedDetailError(f"unsupported detail media type {envelope.media_type}")
        text = _decode_text(envelope.payload, envelope.media_type)
        if media_type.startswith(("text/html", "application/xhtml+xml")):
            title, body = _extract_html(text)
        else:
            lines = [line.strip() for line in text.splitlines() if line.strip()]
            if not lines:
                raise PartialDetailError("plain-text detail has no content")
            title, body = lines[0], "\n".join(lines)
        if len(body) < 20:
            raise PartialDetailError("detail body is too short to be complete")
        location = next(
            (header.value for header in envelope.headers if header.name == "content-location"),
            None,
        )
        discovery_title = next(
            (
                header.value.strip()
                for header in envelope.headers
                if header.name == "x-fi-intel-discovery-title"
            ),
            "",
        )
        mentioned_names: tuple[str, ...] = ()
        identifiers: dict[str, str] = {}
        if envelope.source_id == "sec_edgar_8k":
            match = _SEC_TITLE_RE.match(discovery_title)
            if match is not None:
                mentioned_names = (match.group("name").strip(),)
                identifiers = {"cik": match.group("cik")}
        else:
            mentioned_names = _fed_mentioned_names(discovery_title)
        return CanonicalDocument(
            doc_id=envelope.external_id,
            source_id=envelope.source_id,
            published_at=envelope.source_published_at,
            recorded_at=envelope.fetched_at,
            title=title,
            body=body,
            document_class=document_class,
            barrier_side=envelope.access_policy.barrier_side,
            mentioned_names=mentioned_names,
            identifiers=identifiers,
            url=location,
            metadata={
                "acquisition_mode": "full_content",
                "source_revision": envelope.source_revision,
                "discovery_title": discovery_title,
            },
        )


def sec_edgar_full_content(
    access_policy: AccessPolicy,
    settings: Settings | None = None,
    *,
    transport: SourceHttpTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FeedDetailRawAdapter:
    active = settings or Settings()
    registration = production_source_catalog(active).require("sec_edgar_8k")
    return _government_adapter(
        registration,
        access_policy,
        active,
        parse_sec_discovery,
        transport,
        clock,
    )


def fed_press_full_content(
    access_policy: AccessPolicy,
    settings: Settings | None = None,
    *,
    transport: SourceHttpTransport | None = None,
    clock: Callable[[], datetime] | None = None,
) -> FeedDetailRawAdapter:
    active = settings or Settings()
    registration = production_source_catalog(active).require("fed_press_releases")
    return _government_adapter(
        registration,
        access_policy,
        active,
        parse_fed_discovery,
        transport,
        clock,
    )


def _government_adapter(
    registration: SourceRegistration,
    policy: AccessPolicy,
    settings: Settings,
    parser: DiscoveryParser,
    transport: SourceHttpTransport | None,
    clock: Callable[[], datetime] | None,
) -> FeedDetailRawAdapter:
    client = HardenedSourceClient(
        transport or HttpxSourceTransport(),
        allowed_origins=registration.allowed_origins,
        user_agent=settings.rss_user_agent,
        timeout_seconds=registration.request_timeout_seconds,
        max_attempts=registration.max_attempts,
        max_redirects=registration.max_redirects,
        clock=clock,
    )
    return FeedDetailRawAdapter(registration, policy, client, parser)


def _parse_xml(payload: bytes) -> ET.Element:
    if _FORBIDDEN_XML.search(payload):
        raise MalformedDiscoveryError("feed XML declarations cannot define entities")
    try:
        return ET.fromstring(payload)  # noqa: S314 - entities are rejected above
    except ET.ParseError as exc:
        raise MalformedDiscoveryError(f"feed is not well-formed XML: {exc}") from exc


def _unique_entries(entries: list[DiscoveredDetail]) -> tuple[DiscoveredDetail, ...]:
    identities = [item.external_id for item in entries]
    if len(set(identities)) != len(identities):
        raise MalformedDiscoveryError("feed contains duplicate external identities")
    return tuple(sorted(entries, key=lambda item: (item.published_at, item.external_id)))


def _decode_text(payload: bytes, media_type: str) -> str:
    match = re.search(r"charset=([^;\s]+)", media_type, flags=re.IGNORECASE)
    charset = match.group(1).strip("\"'") if match else "utf-8"
    try:
        return payload.decode(charset, errors="strict")
    except (LookupError, UnicodeDecodeError) as exc:
        raise MalformedDetailError(f"detail is not valid {charset} text") from exc


class _VisibleTextParser(HTMLParser):
    _HIDDEN = frozenset({"script", "style", "svg", "noscript"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title_parts: list[str] = []
        self.heading_parts: list[str] = []
        self.body_parts: list[str] = []
        self._hidden_depth = 0
        self._in_title = False
        self._in_heading = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        del attrs
        lowered = tag.lower()
        if lowered in self._HIDDEN:
            self._hidden_depth += 1
        self._in_title = self._in_title or lowered == "title"
        self._in_heading = self._in_heading or lowered == "h1"

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if lowered in self._HIDDEN and self._hidden_depth:
            self._hidden_depth -= 1
        if lowered == "title":
            self._in_title = False
        if lowered == "h1":
            self._in_heading = False

    def handle_data(self, data: str) -> None:
        if self._hidden_depth:
            return
        clean = " ".join(data.split())
        if not clean:
            return
        self.body_parts.append(clean)
        if self._in_title:
            self.title_parts.append(clean)
        if self._in_heading:
            self.heading_parts.append(clean)


def _extract_html(value: str) -> tuple[str, str]:
    lowered = value.lower()
    if "<html" in lowered and "</html>" not in lowered:
        raise PartialDetailError("HTML detail is missing its closing document tag")
    parser = _VisibleTextParser()
    try:
        parser.feed(value)
        parser.close()
    except Exception as exc:
        raise MalformedDetailError("detail HTML could not be parsed") from exc
    title = " ".join(parser.heading_parts or parser.title_parts).strip()
    body = "\n".join(parser.body_parts).strip()
    if not title:
        raise MalformedDetailError("detail HTML is missing a title or h1")
    if not body:
        raise PartialDetailError("detail HTML contains no visible text")
    return title, body


def _fed_mentioned_names(title: str) -> tuple[str, ...]:
    """Extract institution names explicitly named by common Fed title forms."""
    normalized = " ".join(title.split())
    match = _FED_ENTITY_MARKER_RE.search(normalized)
    if match is None:
        return ()
    payload = re.split(
        r"\s+and\s+(?:announces|former\s+employee)\b",
        match.group("names"),
        maxsplit=1,
        flags=re.IGNORECASE,
    )[0]
    names = [
        part.strip(" ,.")
        for part in re.split(r",\s+and\s+|\s+and\s+", payload)
        if part.strip(" ,.")
    ]
    return tuple(dict.fromkeys(names))


def detail_origin(value: str) -> str:
    """Expose normalized origin only for diagnostics and focused tests."""
    parsed = urlsplit(value)
    host = parsed.hostname or ""
    port = "" if parsed.port in {None, 443} else f":{parsed.port}"
    return f"{parsed.scheme.lower()}://{host.lower()}{port}"
