"""Explicitly labelled local heuristics used only by the POC demo.

These components are deterministic fixture evaluators. They are not presented
as trained models and are never selected by production configuration.
"""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from decimal import Decimal

from fi_intel.agents.opportunity_research import (
    ResearchClaim,
    ResearchRequest,
    ResearchResponse,
)
from fi_intel.ingest.extract import (
    ChangeDirection,
    ClaimProperties,
    ExtractionRequest,
    ExtractionResponse,
    RawClaim,
    RawEntityMention,
)
from fi_intel.ontology.vocab import EdgeType, NodeType
from fi_intel.tools.evidence import OpportunityClaimKind, OpportunityStatus

POC_EXTRACTOR_VERSION = "poc-document-heuristic-v1"
POC_REASONER_VERSION = "poc-evidence-heuristic-v1"


def _aware(value: str) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _mention(node_type: NodeType, name: str) -> RawEntityMention:
    return RawEntityMention(node_type=node_type, name=name)


def _claim(
    *,
    predicate: EdgeType,
    subject: RawEntityMention,
    object_: RawEntityMention,
    text: str,
    valid_from: datetime,
    properties: ClaimProperties,
    confidence: float,
) -> RawClaim:
    # The whole document is a valid exact span. Production extractors should use
    # the smallest sufficient span; the POC keeps rules legible and auditable.
    return RawClaim(
        predicate=predicate,
        subject=subject,
        object=object_,
        valid_from=valid_from,
        confidence=confidence,
        snippet_offset=(0, len(text)),
        snippet_text=text,
        properties=properties,
    )


class POCHeuristicExtractor:
    """Map a small, declared phrase set into governed typed claims.

    The request's untrusted document JSON is the only input. No document ID,
    graph fixture, or expected-signal label participates in extraction.
    """

    model_version = POC_EXTRACTOR_VERSION

    async def extract(self, request: ExtractionRequest) -> ExtractionResponse:
        payload = json.loads(request.document_text)
        text = str(payload["document_text"])
        title, _, body = text.partition("\n")
        lowered = body.lower()
        published_at = _aware(str(payload["published_at"]))
        mentioned_names = tuple(str(name) for name in payload.get("mentioned_names", ()))
        if not mentioned_names:
            return ExtractionResponse(claims=[])
        organization = _mention(NodeType.ORGANIZATION, mentioned_names[0])
        claims: list[RawClaim] = []

        if "outlook" in lowered and "negative from stable" in lowered:
            claims.append(
                _claim(
                    predicate=EdgeType.RATING_ACTION_ON,
                    subject=organization,
                    object_=_mention(NodeType.RATING, "negative outlook"),
                    text=text,
                    valid_from=published_at,
                    properties=ClaimProperties(
                        direction=ChangeDirection.NEGATIVE,
                        outlook="negative",
                        rating_type="outlook",
                    ),
                    confidence=0.97,
                )
            )
        elif "outlook" in lowered and "affirmed" in lowered and "stable" in lowered:
            claims.append(
                _claim(
                    predicate=EdgeType.RATING_ACTION_ON,
                    subject=organization,
                    object_=_mention(NodeType.RATING, "stable outlook"),
                    text=text,
                    valid_from=published_at,
                    properties=ClaimProperties(
                        direction=ChangeDirection.AFFIRMED,
                        outlook="stable",
                        rating_type="outlook",
                    ),
                    confidence=0.96,
                )
            )

        if "results" in title.lower() and "cet1" in lowered:
            current_match = re.search(
                r"cet1(?: capital)? ratio (?:of |unchanged at )?(\d+(?:\.\d+)?)%",
                lowered,
            )
            prior_match = re.search(r"(?:down )?from (\d+(?:\.\d+)?)%", lowered)
            if current_match is not None:
                current = Decimal(current_match.group(1))
                prior = Decimal(prior_match.group(1)) if prior_match else current
                direction = ChangeDirection.DOWN if prior > current else ChangeDirection.FLAT
                claims.append(
                    _claim(
                        predicate=EdgeType.REPORTS_METRIC,
                        subject=organization,
                        object_=_mention(NodeType.METRIC, "CET1 ratio"),
                        text=text,
                        valid_from=published_at,
                        properties=ClaimProperties(
                            metric="cet1",
                            value=current,
                            prior=prior,
                            unit="percent",
                            direction=direction,
                        ),
                        confidence=0.98,
                    )
                )

        if "group treasurer" in lowered and ("left" in lowered or "depart" in lowered):
            claims.append(
                _claim(
                    predicate=EdgeType.LEADERSHIP_CHANGE_AT,
                    subject=_mention(NodeType.EVENT, "group treasurer"),
                    object_=organization,
                    text=text,
                    valid_from=published_at,
                    properties=ClaimProperties(role="treasurer"),
                    confidence=0.94,
                )
            )

        if "board" in lowered and "approved" in lowered and "medium term note programme" in lowered:
            limit_match = re.search(r"usd (\d+(?:\.\d+)?) billion", lowered)
            if limit_match is not None:
                claims.append(
                    _claim(
                        predicate=EdgeType.PROGRAMME_APPROVED_BY,
                        subject=_mention(NodeType.PROGRAMME, "euro medium term note programme"),
                        object_=organization,
                        text=text,
                        valid_from=published_at,
                        properties=ClaimProperties(
                            programme="EMTN",
                            limit_usd_bn=Decimal(limit_match.group(1)),
                            currency="USD",
                            status="approved",
                            marketed=False,
                        ),
                        confidence=0.98,
                    )
                )

        maturity_match = re.search(
            r"usd (\d+(?:\.\d+)?) million sukuk.*matures on 14 may 2025",
            lowered,
        )
        if maturity_match is not None and "not announced refinancing" in lowered:
            instrument = _mention(NodeType.INSTRUMENT, "USD 500 million sukuk")
            claims.extend(
                [
                    _claim(
                        predicate=EdgeType.ISSUES,
                        subject=organization,
                        object_=instrument,
                        text=text,
                        valid_from=published_at,
                        properties=ClaimProperties(
                            amount_usd_mn=Decimal(maturity_match.group(1)),
                            currency="USD",
                        ),
                        confidence=0.95,
                    ),
                    _claim(
                        predicate=EdgeType.MATURES_ON,
                        subject=instrument,
                        object_=_mention(NodeType.EVENT, "14 May 2025"),
                        text=text,
                        valid_from=datetime(2025, 5, 14, tzinfo=UTC),
                        properties=ClaimProperties(
                            maturity_date=datetime(2025, 5, 14, tzinfo=UTC).date(),
                            amount_usd_mn=Decimal(maturity_match.group(1)),
                            currency="USD",
                        ),
                        confidence=0.96,
                    ),
                ]
            )

        return ExtractionResponse(claims=claims)


class POCHeuristicReasoningModel:
    """Turn retrieved evidence into atomic, cited POC opportunity claims."""

    model_version = POC_REASONER_VERSION

    _NARRATIVES: dict[
        str,
        tuple[str, str, OpportunityClaimKind, tuple[str, ...], str],
    ] = {
        "negative_rating_action_with_capital_decline": (
            "Capital pressure creates a financing coverage window",
            "A negative outlook alongside a material CET1 decline supports "
            "proactive financing coverage.",
            OpportunityClaimKind.THESIS,
            ("negative", "cet1"),
            "The outlook returns to stable or CET1 recovers before financing activity begins.",
        ),
        "leadership_change_treasury": (
            "Treasury transition creates a timely coverage opening",
            "The treasurer departure creates a time-sensitive opportunity "
            "to engage the acting funding team.",
            OpportunityClaimKind.COMMERCIAL_ANGLE,
            ("treasurer", "left"),
            "A permanent treasurer is appointed with no change to funding plans.",
        ),
        "board_approved_issuance_programme": (
            "Approved EMTN capacity supports issuance readiness",
            "The board-approved EMTN programme provides documented capacity for future issuance.",
            OpportunityClaimKind.THESIS,
            ("approved", "programme"),
            "The programme is withdrawn or expires without any marketing or mandate.",
        ),
        "maturity_wall_no_refi": (
            "Upcoming sukuk maturity warrants refinancing coverage",
            "The USD 500 million maturity with no announced refinancing supports "
            "immediate DCM coverage.",
            OpportunityClaimKind.TIMING,
            ("matures", "refinancing"),
            "The instrument is repaid from liquidity or a refinancing is announced "
            "without a mandate.",
        ),
        "upcoming_maturity_observed": (
            "Upcoming sukuk maturity warrants funding coverage",
            "The observed USD 500 million maturity supports immediate DCM coverage.",
            OpportunityClaimKind.TIMING,
            ("matures", "500"),
            "The instrument is repaid, refinanced, or the maturity record is corrected.",
        ),
    }

    async def research(self, request: ResearchRequest) -> ResearchResponse:
        narrative = self._NARRATIVES.get(request.signal_pattern)
        if narrative is None:
            return ResearchResponse(
                title="POC heuristic abstained",
                status=OpportunityStatus.INSUFFICIENT_EVIDENCE,
                falsifier="A governed reasoning rule is added for this pattern.",
                insufficient_evidence=True,
            )
        title, statement, kind, required_terms, falsifier = narrative
        indices = [
            index
            for index, excerpt in enumerate(request.evidence_excerpts)
            if all(term in excerpt.lower() for term in required_terms)
        ]
        if not indices:
            return ResearchResponse(
                title=title,
                status=OpportunityStatus.INSUFFICIENT_EVIDENCE,
                falsifier=falsifier,
                insufficient_evidence=True,
            )
        return ResearchResponse(
            title=title,
            status=OpportunityStatus.WATCH,
            claims=[
                ResearchClaim(
                    text=statement,
                    claim_type=kind,
                    evidence_indices=[indices[0]],
                    confidence=0.85,
                    uncertainty="Deterministic POC heuristic; analyst validation is required.",
                )
            ],
            falsifier=falsifier,
        )
