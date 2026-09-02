"""Deterministic detector coverage checks derived from corpus state."""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum
from typing import Protocol, Self, runtime_checkable

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

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
    factual_subject_key: str | None = None


class FactualCoverageState(StrEnum):
    COMPLETE = "complete"
    PARTIAL = "partial"
    DELAYED = "delayed"
    DARK = "dark"
    UNAUTHORIZED = "unauthorized"
    UNKNOWN = "unknown"


class FactualCoverageContract(BaseModel):
    """Predeclared closed-world contract for one negative inference target."""

    model_config = ConfigDict(frozen=True)

    pattern_name: str
    entity_key: str
    subject_key: str
    required_source_ids: frozenset[str] = Field(min_length=1)
    source_classes: frozenset[str] = Field(min_length=1)
    window_start: AwareDatetime
    window_end: AwareDatetime
    state: FactualCoverageState
    reconciled_at: AwareDatetime
    policy_version: str

    @model_validator(mode="after")
    def _valid_interval(self) -> Self:
        if self.window_end <= self.window_start:
            raise ValueError("factual coverage window must have positive duration")
        return self


@runtime_checkable
class FactualCoverageStore(Protocol):
    async def list_for_pattern(
        self, pattern_name: str, as_of: AwareDatetime
    ) -> tuple[FactualCoverageContract, ...]: ...

    async def close(self) -> None: ...


class PostgresFactualCoverageStore:
    """Append-only factual coverage contracts from migration 0015."""

    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def list_for_pattern(
        self, pattern_name: str, as_of: AwareDatetime
    ) -> tuple[FactualCoverageContract, ...]:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT pattern_name, entity_key, subject_key, required_source_ids,
                   source_classes, window_start, window_end, state,
                   reconciled_at, policy_version
            FROM factual_coverage_contract_v3
            WHERE pattern_name = $1
              AND window_start <= $2
              AND window_end >= $2
              AND reconciled_at <= $2
            ORDER BY reconciled_at, contract_id
            """,
            pattern_name,
            as_of,
        )
        return tuple(
            FactualCoverageContract(
                pattern_name=row["pattern_name"],
                entity_key=row["entity_key"],
                subject_key=row["subject_key"],
                required_source_ids=frozenset(row["required_source_ids"]),
                source_classes=frozenset(row["source_classes"]),
                window_start=row["window_start"],
                window_end=row["window_end"],
                state=row["state"],
                reconciled_at=row["reconciled_at"],
                policy_version=row["policy_version"],
            )
            for row in rows
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


class CoverageDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    complete: bool
    reasons: tuple[str, ...] = ()
    checked_source_ids: tuple[str, ...] = ()


class DetectorCoverageGap(BaseModel):
    """One detector suppressed by incomplete operational coverage."""

    model_config = ConfigDict(frozen=True)

    pattern_name: str
    entity_key: str | None = None
    reasons: tuple[str, ...]
    checked_source_ids: tuple[str, ...] = ()


@runtime_checkable
class CoverageProvider(Protocol):
    async def preflight(self, request: CoverageRequest) -> CoverageDecision: ...

    async def assess(self, request: CoverageRequest) -> CoverageDecision: ...


class FailClosedCoverageProvider:
    """Default used when computed coverage has not been configured."""

    async def preflight(self, request: CoverageRequest) -> CoverageDecision:
        return await self.assess(request)

    async def assess(self, request: CoverageRequest) -> CoverageDecision:  # noqa: C901
        return CoverageDecision(
            complete=False,
            reasons=(f"no coverage provider configured for {request.pattern_name}",),
        )


class StaticCoverageProvider:
    """Explicit deterministic provider for fixtures and controlled replays."""

    def __init__(self, *, complete: bool, reason: str = "static fixture coverage") -> None:
        self._decision = CoverageDecision(complete=complete, reasons=(reason,))

    async def preflight(self, request: CoverageRequest) -> CoverageDecision:
        del request
        return self._decision

    async def assess(  # noqa: C901
        self, request: CoverageRequest
    ) -> CoverageDecision:
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
        factual_contracts: tuple[FactualCoverageContract, ...] = (),
        factual_store: FactualCoverageStore | None = None,
    ) -> None:
        self._operations = operations
        self._required_source_ids = required_source_ids
        self._covered_entity_keys = covered_entity_keys
        self._factual_contracts = factual_contracts
        self._factual_store = factual_store

    async def _contracts(self, request: CoverageRequest) -> tuple[FactualCoverageContract, ...]:
        durable = (
            await self._factual_store.list_for_pattern(request.pattern_name, request.as_of)
            if self._factual_store is not None
            else ()
        )
        return (*self._factual_contracts, *durable)

    async def preflight(self, request: CoverageRequest) -> CoverageDecision:
        """Report structurally dark detectors before any query returns rows."""

        reasons: list[str] = []
        checked: list[str] = []
        if CoverageScope.DESK_ACCOUNT in request.scopes and not self._covered_entity_keys:
            reasons.append("no desk account coverage universe is configured")
        if CoverageScope.SOURCE_OPERATIONS in request.scopes:
            required = self._required_source_ids.get(request.pattern_name, frozenset())
            if not required:
                reasons.append("no required source universe is configured")
            unauthorized = required - request.allowed_source_ids
            if unauthorized:
                reasons.append(f"required sources are not authorized: {sorted(unauthorized)}")
            checked.extend(sorted(required & request.allowed_source_ids))
        if CoverageScope.FACTUAL_ENTITY in request.scopes:
            contracts = await self._contracts(request)
            if not any(contract.pattern_name == request.pattern_name for contract in contracts):
                reasons.append("no factual completeness contract is configured")
        return CoverageDecision(
            complete=not reasons,
            reasons=tuple(reasons),
            checked_source_ids=tuple(checked),
        )

    async def assess(self, request: CoverageRequest) -> CoverageDecision:  # noqa: C901
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

        if CoverageScope.FACTUAL_ENTITY in request.scopes:
            contracts = await self._contracts(request)
            subject_key = request.factual_subject_key or request.entity_key
            eligible = [
                contract
                for contract in contracts
                if contract.pattern_name == request.pattern_name
                and contract.entity_key == request.entity_key
                and contract.subject_key == subject_key
                and contract.window_start <= request.as_of <= contract.window_end
                and contract.reconciled_at <= request.as_of
            ]
            if not eligible:
                reasons.append(
                    f"no as-of factual completeness contract for subject {subject_key!r}"
                )
            else:
                contract = max(eligible, key=lambda item: item.reconciled_at)
                if contract.state is not FactualCoverageState.COMPLETE:
                    reasons.append(
                        f"factual coverage for subject {subject_key!r} is {contract.state.value}"
                    )
                unauthorized = contract.required_source_ids - request.allowed_source_ids
                if unauthorized:
                    reasons.append(
                        f"factual coverage sources are not authorized: {sorted(unauthorized)}"
                    )
                checked.extend(sorted(contract.required_source_ids & request.allowed_source_ids))

        return CoverageDecision(
            complete=not reasons,
            reasons=tuple(reasons),
            checked_source_ids=tuple(checked),
        )


def source_coverage_policy(source_ids: frozenset[str]) -> dict[str, frozenset[str]]:
    """Map the configured source universe to every Stage One detector."""
    return {
        "maturity_wall_no_refi": source_ids,
        "at1_call_approaching_no_refi": source_ids,
        "negative_rating_action_with_capital_decline": source_ids,
    }
