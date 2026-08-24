"""Assertion model — the atom of the knowledge graph.

Every graph write is an Assertion (invariant 4): a predicate between two
entity keys carrying provenance and both time axes. Assertions are
append-only; corrections create a new assertion that supersedes the old.
Nothing is mutated or deleted.

`assertion_id` is a deterministic content hash, which is what makes writes
idempotent: asserting the same fact from the same evidence twice is a
no-op, not a duplicate.
"""

import hashlib
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from fi_intel.ontology.vocab import EdgeType, NodeType


class EntityRef(BaseModel):
    """A node endpoint. `key` is the stable entity key: an LEI for
    organizations (from M3 resolution), or a deterministic key for
    instruments/events (e.g. ISIN, or `event:<slug>`)."""

    model_config = ConfigDict(frozen=True)

    node_type: NodeType
    key: str = Field(min_length=1)
    display_name: str = Field(min_length=1)


class Assertion(BaseModel):
    """One bi-temporal, fully-provenanced claim."""

    model_config = ConfigDict(frozen=True)

    predicate: EdgeType
    subject: EntityRef
    object: EntityRef

    # Provenance (invariant 4 — minimum set, all required).
    source_doc_id: str = Field(min_length=1)
    snippet_offset: tuple[int, int]
    extractor_version: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    # Both time axes: when the fact is true in the world, and when the
    # system learned it. Backtests pin on recorded_at (invariant 10).
    valid_from: datetime
    valid_to: datetime | None = None
    recorded_at: datetime

    properties: dict[str, str] = Field(default_factory=dict)

    def assertion_id(self) -> str:
        """Deterministic identity of the claim+evidence, for idempotency."""
        payload = "|".join(
            [
                str(self.predicate),
                self.subject.node_type + ":" + self.subject.key,
                self.object.node_type + ":" + self.object.key,
                self.source_doc_id,
                f"{self.snippet_offset[0]}:{self.snippet_offset[1]}",
                self.valid_from.isoformat(),
            ]
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()
