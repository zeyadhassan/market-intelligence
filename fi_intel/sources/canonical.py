"""The canonical document model — the single boundary between vendors and us.

A new vendor must be expressible as a mapping into this model. If a vendor
field does not map cleanly, the adapter raises rather than coercing it.
"""

import hashlib
from datetime import datetime
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, ValidationInfo, field_validator


class DocumentClass(StrEnum):
    """Broad content categories the pipeline treats differently."""

    NEWS_WIRE = "news_wire"
    RATING_ACTION = "rating_action"
    FILING = "filing"
    REGULATORY = "regulatory"
    REFERENCE = "reference"


class BarrierSide(StrEnum):
    """Which side of the information barrier the document may cross.

    Entitlement filtering joins on this column in the data layer.
    """

    PUBLIC = "public"
    PRIVATE = "private"


def document_text(doc: "CanonicalDocument") -> str:
    """Return the one canonical coordinate space used by hashes and spans."""
    return f"{doc.title}\n{doc.body}"


class CanonicalDocument(BaseModel):
    """One licensed document, normalized across all vendors.

    Field names here are vendor-neutral by construction. Downstream code
    must never branch on source_id to recover vendor semantics — push the
    difference into the adapter.
    """

    model_config = ConfigDict(frozen=True)

    doc_id: str = Field(min_length=1)
    source_id: str = Field(min_length=1)
    published_at: datetime
    recorded_at: datetime
    title: str = Field(min_length=1)
    body: str = Field(min_length=1)
    language: str = Field(default="en", min_length=2, max_length=2)
    document_class: DocumentClass
    barrier_side: BarrierSide = BarrierSide.PUBLIC
    # Vendor-neutral entity mentions as they appear in the text. Resolution
    # to LEIs happens in ingest/resolve.py, never in the adapter.
    mentioned_names: tuple[str, ...] = ()
    # Identifiers explicitly present in the document (LEI, BIC, ISIN, ...).
    # Keys are the identifier scheme in lower case, e.g. "lei", "isin".
    identifiers: dict[str, str] = Field(default_factory=dict)
    url: str | None = None
    # Free-form, vendor-neutral metadata (e.g. {"wire": "reuters"} is fine;
    # {"factiva_accn": "..."} is rejected below).
    metadata: dict[str, str] = Field(default_factory=dict)

    @field_validator("identifiers", "metadata")
    @classmethod
    def _no_vendor_namespaces(cls, value: dict[str, str]) -> dict[str, str]:
        for key in value:
            lowered = key.lower()
            for prefix in ("factiva", "rdp", "refinitiv", "bloomberg", "dowjones"):
                if lowered.startswith(prefix):
                    msg = f"vendor namespaced key {key!r} leaks past the adapter boundary"
                    raise ValueError(msg)
        return value

    @field_validator("recorded_at")
    @classmethod
    def _recorded_not_before_published(cls, value: datetime, info: ValidationInfo) -> datetime:
        # A document cannot be recorded before it exists. Catching this here
        # prevents a whole class of silent backtest leakage.
        published = info.data.get("published_at")
        if published is not None and value < published:
            msg = "recorded_at precedes published_at"
            raise ValueError(msg)
        return value

    def content_hash(self) -> str:
        """Stable hash over canonical content, used for exact dedupe.

        Computed from normalized title + body only: two wires carrying the
        same story with different vendor envelopes hash identically.
        """
        normalized = " ".join(document_text(self).lower().split())
        return hashlib.sha256(normalized.encode("utf-8")).hexdigest()
