"""Ingestion pipeline: fetch → dedupe → persist, per source.

Deterministic plane. Resumability comes from a single rule: the cursor is
committed in the same transaction as the batch that produced it, so the
cursor can never disagree with the data.

Failures are loud (invariant 9). A malformed document aborts the run, the
offending batch rolls back, and the error names the doc_id. Already
committed batches stay committed — that is what the cursor is for, not a
partial-commit bug.
"""

from collections.abc import AsyncIterator

from pydantic import BaseModel, ConfigDict

from fi_intel.ingest.dedupe import DedupeIndex, DuplicateVerdict
from fi_intel.ingest.store import DocumentStore
from fi_intel.logging import get_logger
from fi_intel.sources.base import SourceAdapter
from fi_intel.sources.canonical import CanonicalDocument

DEFAULT_BATCH_SIZE = 5


class IngestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    source_id: str
    fetched: int
    persisted: int
    exact_duplicates: int
    near_duplicates: int
    batches_committed: int
    final_cursor: str | None


class IngestPipeline:
    def __init__(
        self,
        store: DocumentStore,
        batch_size: int = DEFAULT_BATCH_SIZE,
        dedupe: DedupeIndex | None = None,
    ) -> None:
        if batch_size < 1:
            msg = "batch_size must be >= 1"
            raise ValueError(msg)
        self._store = store
        self._batch_size = batch_size
        self._dedupe = dedupe or DedupeIndex()
        self._log = get_logger(component="ingest.pipeline")

    async def run(self, adapter: SourceAdapter) -> IngestResult:
        source_id = adapter.source_id
        log = self._log.bind(source_id=source_id)

        cursor = await self._store.load_cursor(source_id)
        # Seed dedupe from what is already persisted so a resumed run
        # classifies re-fetched documents identically to the first run.
        self._dedupe.load(await self._store.load_documents(source_id))
        log.info("ingest.start", resumed_from=cursor.position if cursor else None)

        fetched = persisted = exact_dupes = 0
        near_dupes: list[DuplicateVerdict] = []
        batch: list[CanonicalDocument] = []
        batch_dupes: list[DuplicateVerdict] = []
        batches = 0
        last_doc: CanonicalDocument | None = None

        async def flush() -> None:
            nonlocal batches, persisted
            if last_doc is None or (not batch and not batch_dupes):
                return
            await self._store.commit_batch(batch, batch_dupes, adapter.cursor_for(last_doc))
            persisted += len(batch)
            batches += 1
            batch.clear()
            batch_dupes.clear()

        # adapter.fetch is an async generator function; mypy types the call
        # as a coroutine (see sources/base.py note), hence the ignore.
        stream: AsyncIterator[CanonicalDocument] = adapter.fetch(cursor)  # type: ignore[assignment]
        async for doc in stream:
            fetched += 1
            verdict = self._dedupe.classify(doc)
            if verdict is None:
                exact_dupes += 1
            elif isinstance(verdict, DuplicateVerdict):
                near_dupes.append(verdict)
                batch_dupes.append(verdict)
            else:
                batch.append(verdict)
            last_doc = doc
            if len(batch) + len(batch_dupes) >= self._batch_size:
                await flush()
                log.info("ingest.batch", batches=batches, fetched=fetched)

        await flush()
        final = await self._store.load_cursor(source_id)
        log.info(
            "ingest.done",
            fetched=fetched,
            persisted=persisted,
            exact_duplicates=exact_dupes,
            near_duplicates=len(near_dupes),
        )
        return IngestResult(
            source_id=source_id,
            fetched=fetched,
            persisted=persisted,
            exact_duplicates=exact_dupes,
            near_duplicates=len(near_dupes),
            batches_committed=batches,
            final_cursor=final.position if final else None,
        )
