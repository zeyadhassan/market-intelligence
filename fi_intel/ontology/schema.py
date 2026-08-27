"""Assertion model — the atom of the knowledge graph.

Every graph write is an Assertion: a predicate between two entity keys
carrying provenance and both time axes. Assertions are
append-only; corrections create a new assertion that supersedes the old.
Nothing is mutated or deleted.

`assertion_id` is a deterministic content hash, which is what makes writes
idempotent: asserting the same fact from the same evidence twice is a
no-op, not a duplicate.
"""

import hashlib
import json

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.ledger.models import knowledge_assertion_id
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.canonical import BarrierSide


class EntityRef(BaseModel):
    """A node endpoint. `key` is the stable entity key: an LEI for
    organizations, or a deterministic key for
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

    # Minimum provenance fields required for every assertion.
    source_id: str = Field(min_length=1)
    source_doc_id: str = Field(min_length=1)
    barrier_side: BarrierSide
    policy_version: str = Field(min_length=1)
    snippet_offset: tuple[int, int]
    extractor_version: str = Field(min_length=1)
    confidence: float = Field(ge=0.0, le=1.0)

    # Both time axes: when the fact is true in the world, and when the
    # system learned it. Backtests pin on recorded_at.
    valid_from: AwareDatetime
    valid_to: AwareDatetime | None = None
    recorded_at: AwareDatetime

    properties: dict[str, str] = Field(default_factory=dict)

    def state_key(self) -> str | None:
        """Stable key for mutable real-world state, or ``None`` for events.

        The dimensions are deterministic and predicate-specific.  They keep,
        for example, a rating outlook separate from a rating grade and one
        leadership role separate from another.
        """

        dimensions: dict[EdgeType, tuple[str, ...]] = {
            EdgeType.RATING_ACTION_ON: ("rating_type",),
            EdgeType.LEADERSHIP_CHANGE_AT: ("role",),
            EdgeType.PROGRAMME_APPROVED_BY: ("programme",),
            EdgeType.MATURES_ON: (),
            EdgeType.CALLABLE_ON: (),
            EdgeType.REPORTS_METRIC: ("metric",),
            EdgeType.REFINANCES: ("status",),
        }
        keys = dimensions.get(self.predicate)
        if keys is None:
            return None
        owner = (
            self.object.key
            if self.predicate in {EdgeType.RATING_ACTION_ON, EdgeType.LEADERSHIP_CHANGE_AT}
            else self.subject.key
        )
        values = [self.properties.get(key, "").strip().casefold() for key in keys]
        return "|".join([str(self.predicate), owner, *values])

    def assertion_id(self) -> str:
        """Deterministic identity of the claim+evidence, for idempotency."""
        payload = "|".join(
            [
                str(self.predicate),
                self.subject.node_type + ":" + self.subject.key,
                self.object.node_type + ":" + self.object.key,
                self.source_id,
                self.source_doc_id,
                str(self.barrier_side),
                self.policy_version,
                self.extractor_version,
                json.dumps(self.properties, sort_keys=True, separators=(",", ":")),
                f"{self.snippet_offset[0]}:{self.snippet_offset[1]}",
                self.valid_from.isoformat(),
            ]
        )
        content_identity = hashlib.sha256(payload.encode("utf-8")).hexdigest()
        return str(knowledge_assertion_id(content_identity))
