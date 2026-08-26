"""Replayable application services built on the v2 evidence ledger.

The original ``fi_intel.ingest.pipeline`` remains the prototype compatibility
path. New source integrations should enter through :mod:`fi_intel.application`
so original bytes, revisions, job state, and watermarks are never inferred from
already-normalized documents.
"""

from fi_intel.application.ingestion import (
    Canonicalizer,
    IngestionDisposition,
    IngestionResult,
    ReplayableIngestionService,
)
from fi_intel.application.raw import (
    ArchiveConflictError,
    ArchivedObject,
    InMemoryRawArchive,
    RawArchive,
    RawHeader,
    RawSourceEnvelope,
)

__all__ = [
    "ArchiveConflictError",
    "ArchivedObject",
    "Canonicalizer",
    "InMemoryRawArchive",
    "IngestionDisposition",
    "IngestionResult",
    "RawArchive",
    "RawHeader",
    "RawSourceEnvelope",
    "ReplayableIngestionService",
]
