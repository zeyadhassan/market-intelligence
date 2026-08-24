"""The retrieval service: search + mandatory audit, one entry point.

Agents and the CLI call this, never CorpusSearch directly, so that
"every retrieval writes an access_log row" is structural rather than
conventional. Audit failure fails the retrieval (fail-closed).
"""

from datetime import UTC, datetime

from fi_intel.governance.audit import AccessEvent, AuditLog
from fi_intel.retrieval.corpus import CorpusSearch, ScoredChunk
from fi_intel.retrieval.entitlement import Principal


class RetrievalService:
    def __init__(self, search: CorpusSearch, audit: AuditLog, run_id: str) -> None:
        self._search = search
        self._audit = audit
        self._run_id = run_id

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
                    accessed_at=datetime.now(tz=UTC),
                )
            )
        await self._audit.record(events)
        return results
