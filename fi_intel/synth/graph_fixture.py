"""Hand-written graph fixture: the two episodes as bi-temporal assertions.

The fixture gives detectors graph structure to query without a live model.
Every assertion carries source-span provenance and both time axes.

Gulf Meridian facts are sourced from specific corpus documents (the
source_doc_id and offsets are real). Northern Harbour's decoy facts are
deliberately non-triggering: stable rating, flat capital, no programme, no
maturity wall.
"""

from datetime import UTC, datetime

from fi_intel.ontology.schema import Assertion, EntityRef
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.sources.canonical import BarrierSide
from fi_intel.synth.episodes import GULF_MERIDIAN_LEI, NORTHERN_HARBOUR_LEI

EXTRACTOR = "fixture-1.0"
_DOCUMENT_TITLES = {
    "SW-2024-0001": "Gulf Meridian Bank Q.P.S.C. outlook revised to negative",
    "SW-2024-0004": "Gulf Meridian Bank FY2023 results: CET1 falls to 12.1%",
    "SW-2024-0005": "Gulf Meridian group treasurer departs",
    "SW-2024-0006": "Gulf Meridian board approves USD 1.5bn EMTN programme update",
    "SW-2024-0007": "Gulf Meridian USD 500m sukuk matures in May 2025",
    "SW-2024-0009": "Northern Harbour Bank reports steady FY2023 results",
    "SW-2024-0011": "Northern Harbour Bank outlook affirmed at stable",
}


def org(lei: str, name: str) -> EntityRef:
    return EntityRef(node_type=NodeType.ORGANIZATION, key=lei, display_name=name)


def event(key: str, name: str) -> EntityRef:
    return EntityRef(node_type=NodeType.EVENT, key=key, display_name=name)


def instrument(key: str, name: str) -> EntityRef:
    return EntityRef(node_type=NodeType.INSTRUMENT, key=key, display_name=name)


def _mk(
    predicate: EdgeType,
    subject: EntityRef,
    obj: EntityRef,
    doc_id: str,
    offset: tuple[int, int],
    valid_from: datetime,
    recorded_at: datetime,
    properties: dict[str, str] | None = None,
    confidence: float = 0.95,
) -> Assertion:
    prefix = len(_DOCUMENT_TITLES[doc_id]) + 1
    return Assertion(
        predicate=predicate,
        subject=subject,
        object=obj,
        source_id="synthetic_wire",
        source_doc_id=doc_id,
        barrier_side=BarrierSide.PUBLIC,
        policy_version="fixture-policy-v1",
        snippet_offset=(offset[0] + prefix, offset[1] + prefix),
        extractor_version=EXTRACTOR,
        confidence=confidence,
        valid_from=valid_from,
        recorded_at=recorded_at,
        properties=properties or {},
    )


def gulf_meridian_assertions() -> list[Assertion]:
    gm = org(GULF_MERIDIAN_LEI, "Gulf Meridian Bank Q.P.S.C.")
    sukuk = instrument("XS0000000001", "USD 500m sukuk")
    at1 = instrument("XS0000000002", "AT1 notes")
    out = [
        # Rating action: outlook to negative (doc 0001), with the metric decline.
        _mk(
            EdgeType.RATING_ACTION_ON,
            gm,
            EntityRef(node_type=NodeType.RATING, key="rating:gm-neg", display_name="A-/negative"),
            "SW-2024-0001",
            (54, 93),
            datetime(2024, 1, 15, tzinfo=UTC),
            datetime(2024, 1, 15, 9, tzinfo=UTC),
            properties={
                "direction": "negative",
                "outlook": "negative",
                "rating_type": "outlook",
            },
        ),
        # Capital metric: CET1 12.1% vs 13.4% prior year (docs 0001/0004).
        _mk(
            EdgeType.REPORTS_METRIC,
            gm,
            EntityRef(
                node_type=NodeType.METRIC, key="metric:gm-cet1-2023", display_name="CET1 2023"
            ),
            "SW-2024-0004",
            (0, 40),
            datetime(2023, 12, 31, tzinfo=UTC),
            datetime(2024, 2, 10, 9, tzinfo=UTC),
            properties={
                "metric": "cet1",
                "value": "12.1",
                "prior": "13.4",
                "unit": "percent",
                "direction": "down",
            },
        ),
        # Leadership: group treasurer departed (doc 0005).
        _mk(
            EdgeType.LEADERSHIP_CHANGE_AT,
            EntityRef(
                node_type=NodeType.PERSON, key="person:gm-treasurer", display_name="Group Treasurer"
            ),
            gm,
            "SW-2024-0005",
            (33, 48),
            datetime(2024, 3, 20, tzinfo=UTC),
            datetime(2024, 3, 20, 9, tzinfo=UTC),
            properties={"role": "treasurer"},
        ),
        # Board-approved EMTN programme (doc 0006).
        _mk(
            EdgeType.PROGRAMME_APPROVED_BY,
            EntityRef(
                node_type=NodeType.PROGRAMME, key="prog:gm-emtn", display_name="EMTN programme"
            ),
            gm,
            "SW-2024-0006",
            (67, 98),
            datetime(2024, 4, 25, tzinfo=UTC),
            datetime(2024, 4, 25, 9, tzinfo=UTC),
            properties={
                "programme": "EMTN",
                "limit_usd_bn": "1.5",
                "currency": "USD",
                "status": "approved",
                "marketed": "false",
            },
        ),
        # Maturity wall: sukuk matures 2025-05-14, NO refinancing (doc 0007).
        _mk(
            EdgeType.ISSUES,
            gm,
            sukuk,
            "SW-2024-0007",
            (16, 37),
            datetime(2019, 5, 14, tzinfo=UTC),
            datetime(2024, 5, 15, 9, tzinfo=UTC),
        ),
        _mk(
            EdgeType.MATURES_ON,
            sukuk,
            event("event:sukuk-mat", "Sukuk maturity"),
            "SW-2024-0007",
            (16, 37),
            datetime(2025, 5, 14, tzinfo=UTC),
            datetime(2024, 5, 15, 9, tzinfo=UTC),
            properties={
                "maturity_date": "2025-05-14",
                "amount_usd_mn": "500",
                "currency": "USD",
            },
        ),
        # AT1 first call 2025-09 (doc 0007), no refinancing.
        _mk(
            EdgeType.ISSUES,
            gm,
            at1,
            "SW-2024-0007",
            (16, 37),
            datetime(2020, 9, 1, tzinfo=UTC),
            datetime(2024, 5, 15, 9, tzinfo=UTC),
            properties={"class": "AT1"},
        ),
        _mk(
            EdgeType.CALLABLE_ON,
            at1,
            event("event:at1-call", "AT1 first call"),
            "SW-2024-0007",
            (16, 37),
            datetime(2025, 9, 1, tzinfo=UTC),
            datetime(2024, 5, 15, 9, tzinfo=UTC),
            properties={
                "first_call_date": "2025-09-01",
                "class": "AT1",
                "amount_usd_mn": "500",
                "currency": "USD",
            },
        ),
    ]
    return out


def northern_harbour_assertions() -> list[Assertion]:
    """Decoy: deliberately non-triggering facts."""
    nh = org(NORTHERN_HARBOUR_LEI, "Northern Harbour Bank")
    return [
        # Stable outlook (NOT negative).
        _mk(
            EdgeType.RATING_ACTION_ON,
            nh,
            EntityRef(node_type=NodeType.RATING, key="rating:nh-stable", display_name="A/stable"),
            "SW-2024-0011",
            (0, 40),
            datetime(2024, 4, 2, tzinfo=UTC),
            datetime(2024, 4, 2, 9, tzinfo=UTC),
            properties={"direction": "affirmed", "outlook": "stable"},
        ),
        # Flat capital (direction "flat", not "down").
        _mk(
            EdgeType.REPORTS_METRIC,
            nh,
            EntityRef(
                node_type=NodeType.METRIC, key="metric:nh-cet1-2023", display_name="CET1 2023"
            ),
            "SW-2024-0009",
            (0, 40),
            datetime(2023, 12, 31, tzinfo=UTC),
            datetime(2024, 2, 1, 9, tzinfo=UTC),
            properties={
                "metric": "cet1",
                "value": "14.9",
                "prior": "14.9",
                "unit": "percent",
                "direction": "flat",
            },
        ),
    ]
