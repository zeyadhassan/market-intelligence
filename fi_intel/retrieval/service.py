"""The retrieval service: search + mandatory audit, one entry point.

Agents and the CLI call this, never CorpusSearch directly, so that
"every retrieval writes an access_log row" is structural rather than
conventional. Audit failure fails the retrieval (fail-closed).
"""

from datetime import UTC, datetime

from fi_intel.governance.audit import AccessEvent, AuditLog
from fi_intel.retrieval.corpus import CorpusSearch, ScoredChunk
from fi_intel.retrieval.entitlement import Principal
from fi_intel.sources.canonical import CanonicalDocument


class RetrievalService:
    def __init__(self, search: CorpusSearch, audit: AuditLog, run_id: str) -> None:
        self._search = search
        self._audit = audit
        self._run_id = run_id

    @property
    def model_version(self) -> str:
        return self._search.model_version

    async def search(
        self,
        query: str,
        principal: Principal,
        **kwargs: object,
    ) -> list[ScoredChunk]:
        results = await self._search.search(query, principal, **kwargs)  # type: ignore[arg-type]
        # Dedupe to one row per document; the audit trail records what the
        # caller could see, not how many chunks of it matched.
        seen: set[tuple[str, str]] = set()
        events: list[AccessEvent] = []
        for result in results:
            key = (result.doc.source_id, result.doc.doc_id)
            if key in seen:
                continue
            seen.add(key)
            events.append(
                AccessEvent(
                    run_id=self._run_id,
                    principal=principal.principal_id,
                    entitlement_group=principal.entitlement_group,
                    source_id=result.doc.source_id,
                    doc_id=result.doc.doc_id,
                    result_count=len(results),
                    accessed_at=datetime.now(tz=UTC),
                )
            )
        if not events:
            events.append(
                AccessEvent(
                    run_id=self._run_id,
                    principal=principal.principal_id,
                    entitlement_group=principal.entitlement_group,
                    operation="retrieval",
                    result_count=0,
                    accessed_at=datetime.now(tz=UTC),
                )
            )
        await self._audit.record(events)
        return results

    async def resolve_span(
        self,
        principal: Principal,
        source_id: str,
        doc_id: str,
        start: int,
        end: int,
        *,
        as_of: datetime | None,
    ) -> tuple[CanonicalDocument, str] | None:
        """Resolve and audit one graph-backed evidence span."""
        resolved = await self._search.resolve_span(principal, source_id, doc_id, start, end, as_of)
        await self._audit.record(
            [
                AccessEvent(
                    run_id=self._run_id,
                    principal=principal.principal_id,
                    entitlement_group=principal.entitlement_group,
                    source_id=source_id if resolved is not None else None,
                    doc_id=doc_id if resolved is not None else None,
                    operation="evidence_resolve",
                    result_count=int(resolved is not None),
                    accessed_at=datetime.now(tz=UTC),
                )
            ]
        )
        return resolved

    async def resolve_spans(
        self,
        principal: Principal,
        spans: tuple[tuple[str, str, int, int], ...],
        *,
        as_of: datetime | None,
    ) -> dict[tuple[str, str, int, int], tuple[CanonicalDocument, str]]:
        """Resolve and audit a bounded evidence batch through one store read."""

        resolved = await self._search.resolve_spans(principal, spans, as_of)
        await self._audit.record(
            [
                AccessEvent(
                    run_id=self._run_id,
                    principal=principal.principal_id,
                    entitlement_group=principal.entitlement_group,
                    source_id=span[0] if span in resolved else None,
                    doc_id=span[1] if span in resolved else None,
                    operation="evidence_resolve_batch",
                    result_count=int(span in resolved),
                    accessed_at=datetime.now(tz=UTC),
                )
                for span in spans
            ]
        )
        return resolved
