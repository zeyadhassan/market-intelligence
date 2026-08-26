"""Deterministic detector coverage checks derived from corpus state."""

from __future__ import annotations

from datetime import timedelta
from typing import Protocol, runtime_checkable

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.graph.queries import CoverageScope
from fi_intel.sources.operations import SourceHealth, SourceOperationsStore


class CoverageRequest(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern_name: str
    entity_key: str
    as_of: AwareDatetime
    freshness_days: int = Field(gt=0)
    allowed_source_ids: frozenset[str]
    scopes: frozenset[CoverageScope]


class CoverageDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    complete: bool
    reasons: tuple[str, ...] = ()
    checked_source_ids: tuple[str, ...] = ()


@runtime_checkable
class CoverageProvider(Protocol):
    async def assess(self, request: CoverageRequest) -> CoverageDecision: ...


class FailClosedCoverageProvider:
    """Default used when computed coverage has not been configured."""

    async def assess(self, request: CoverageRequest) -> CoverageDecision:
        return CoverageDecision(
            complete=False,
            reasons=(f"no coverage provider configured for {request.pattern_name}",),
        )


class StaticCoverageProvider:
    """Explicit deterministic provider for fixtures and controlled replays."""

    def __init__(self, *, complete: bool, reason: str = "static fixture coverage") -> None:
        self._decision = CoverageDecision(complete=complete, reasons=(reason,))

    async def assess(self, request: CoverageRequest) -> CoverageDecision:
        del request
        return self._decision


class SourceOperationsCoverageProvider:
    """Evaluate freshness and account coverage from durable operational state."""

    def __init__(
        self,
        operations: SourceOperationsStore,
        *,
        required_source_ids: dict[str, frozenset[str]],
        covered_entity_keys: frozenset[str],
    ) -> None:
        self._operations = operations
        self._required_source_ids = required_source_ids
        self._covered_entity_keys = covered_entity_keys

    async def assess(self, request: CoverageRequest) -> CoverageDecision:
        reasons: list[str] = []
        checked: list[str] = []
        if (
            CoverageScope.DESK_ACCOUNT in request.scopes
            and request.entity_key not in self._covered_entity_keys
        ):
            reasons.append(f"entity {request.entity_key!r} is absent from desk account coverage")

        if CoverageScope.SOURCE_OPERATIONS in request.scopes:
            required = self._required_source_ids.get(request.pattern_name, frozenset())
            if not required:
                reasons.append("no required source universe is configured")
            unauthorized = required - request.allowed_source_ids
            if unauthorized:
                reasons.append(f"required sources are not authorized: {sorted(unauthorized)}")
            for source_id in sorted(required & request.allowed_source_ids):
                checked.append(source_id)
                observations = await self._operations.list_observations(source_id)
                visible = [item for item in observations if item.finished_at <= request.as_of]
                if not visible:
                    reasons.append(f"source {source_id!r} has no as-of observation")
                    continue
                latest = max(visible, key=lambda item: item.finished_at)
                earliest_fresh = request.as_of - timedelta(days=request.freshness_days)
                if latest.finished_at < earliest_fresh:
                    reasons.append(f"source {source_id!r} observation is outside freshness window")
                if (
                    latest.health is not SourceHealth.HEALTHY
                    or not latest.complete
                    or not latest.fresh
                    or latest.silent
                    or not latest.within_expected_volume
                ):
                    reasons.append(f"source {source_id!r} latest observation is incomplete")

        return CoverageDecision(
            complete=not reasons,
            reasons=tuple(reasons),
            checked_source_ids=tuple(checked),
        )


def source_coverage_policy(source_ids: frozenset[str]) -> dict[str, frozenset[str]]:
    """Map the configured refinancing universe to the affected detectors."""
    return {
        "maturity_wall_no_refi": source_ids,
        "at1_call_approaching_no_refi": source_ids,
    }
