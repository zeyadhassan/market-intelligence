"""Authorized pattern evaluation and durable signal lifecycle management."""

import hashlib
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from fi_intel.governance.policy import GraphAccessContext
from fi_intel.graph.client import GraphClient
from fi_intel.graph.coverage import (
    CoverageProvider,
    CoverageRequest,
    DetectorCoverageGap,
    FailClosedCoverageProvider,
    StaticCoverageProvider,
)
from fi_intel.graph.precision import (
    PatternPrecisionEstimate,
    PatternPrecisionProvider,
    UnavailablePatternPrecisionProvider,
)
from fi_intel.graph.queries import ALL_PATTERNS, Pattern
from fi_intel.graph.signals import (
    LifecycleDecision,
    ScoreContribution,
    Signal,
    SignalLifecycleSnapshot,
    SignalLifecycleState,
    classify_lifecycle,
    rescore_for_lifecycle,
    score_signal,
    signal_authorization_scope,
    stable_signal_id,
)
from fi_intel.logging import get_logger
from fi_intel.sources.canonical import BarrierSide

DEFAULT_WINDOW_DAYS = 395
_ACTIVE_STATES = (
    SignalLifecycleState.NEW,
    SignalLifecycleState.UNCHANGED,
    SignalLifecycleState.STRENGTHENED,
    SignalLifecycleState.WEAKENED,
)


@dataclass(frozen=True)
class _PatternCandidate:
    signal_id: str
    entity_key: str
    entity_name: str
    evidence: dict[str, str]
    material_arguments: dict[str, str]
    assertion_ids: tuple[str, ...]
    source_ids: tuple[str, ...]
    source_doc_ids: tuple[str, ...]
    barrier_side: BarrierSide
    latest_recorded_at: datetime
    materiality_score: float
    evidence_confidence: float


def _combine_candidates(candidates: list[_PatternCandidate]) -> _PatternCandidate:
    """Combine independent evidence rows for one stable signal episode."""
    if not candidates:
        raise ValueError("at least one pattern candidate is required")
    first = candidates[0]
    if any(
        candidate.signal_id != first.signal_id
        or candidate.entity_key != first.entity_key
        or candidate.material_arguments != first.material_arguments
        for candidate in candidates[1:]
    ):
        raise RuntimeError("cannot combine candidates from different signal episodes")

    representative = max(
        candidates,
        key=lambda candidate: (
            candidate.materiality_score,
            candidate.evidence_confidence,
            candidate.latest_recorded_at,
            candidate.assertion_ids,
        ),
    )
    refs = sorted(
        {
            ref
            for candidate in candidates
            for ref in zip(candidate.source_ids, candidate.source_doc_ids, strict=True)
        }
    )
    return _PatternCandidate(
        signal_id=first.signal_id,
        entity_key=first.entity_key,
        entity_name=representative.entity_name,
        evidence=representative.evidence,
        material_arguments=first.material_arguments,
        assertion_ids=tuple(
            sorted({item for candidate in candidates for item in candidate.assertion_ids})
        ),
        source_ids=tuple(source_id for source_id, _ in refs),
        source_doc_ids=tuple(doc_id for _, doc_id in refs),
        barrier_side=(
            BarrierSide.PRIVATE
            if any(candidate.barrier_side is BarrierSide.PRIVATE for candidate in candidates)
            else BarrierSide.PUBLIC
        ),
        latest_recorded_at=max(candidate.latest_recorded_at for candidate in candidates),
        materiality_score=max(candidate.materiality_score for candidate in candidates),
        evidence_confidence=sum(candidate.evidence_confidence for candidate in candidates)
        / len(candidates),
    )


class PatternRegistry:
    def __init__(
        self,
        client: GraphClient,
        patterns: tuple[Pattern, ...] = ALL_PATTERNS,
        *,
        access: GraphAccessContext,
        coverage: CoverageProvider | None = None,
        precision: PatternPrecisionProvider | None = None,
    ) -> None:
        self._client = client
        self._patterns = {pattern.name: pattern for pattern in patterns}
        if len(self._patterns) != len(patterns):
            raise ValueError("pattern names must be unique")
        self._access = access
        if coverage is not None:
            self._coverage = coverage
        elif access.policy_version == "trusted-test-v1":
            self._coverage = StaticCoverageProvider(
                complete=True,
                reason="explicit trusted-test coverage",
            )
        else:
            self._coverage = FailClosedCoverageProvider()
        self._precision = precision or UnavailablePatternPrecisionProvider()
        self._last_coverage_gaps: list[DetectorCoverageGap] = []
        self._authorization_scope = signal_authorization_scope(
            access.principal.entitlement_group,
            access.principal.side.value,
            access.allowed_source_ids,
        )
        self._log = get_logger(component="graph.patterns")

    @property
    def access(self) -> GraphAccessContext:
        return self._access

    def pattern_names(self) -> list[str]:
        return sorted(self._patterns)

    def registered_patterns(self) -> tuple[Pattern, ...]:
        """Public read-only pattern metadata for evaluation and tooling."""
        return tuple(self._patterns[name] for name in sorted(self._patterns))

    @property
    def last_coverage_gaps(self) -> tuple[DetectorCoverageGap, ...]:
        """Coverage gates observed by the most recent evaluate/run call."""

        return tuple(self._last_coverage_gaps)

    async def evaluate(
        self,
        as_of: datetime,
        enabled: set[str] | None = None,
        window_days: int = DEFAULT_WINDOW_DAYS,
    ) -> list[Signal]:
        """Evaluate authorized patterns without mutating signal lifecycle state."""
        return await self._execute(
            as_of,
            enabled=enabled,
            window_days=window_days,
            persist=False,
            include_unchanged=True,
        )

    async def run(
        self,
        as_of: datetime,
        enabled: set[str] | None = None,
        *,
        include_unchanged: bool = False,
    ) -> list[Signal]:
        """Persist lifecycle observations and return actionable detections.

        Repeated unchanged conditions are confirmed and stored but omitted by
        default, preventing daily briefs from resurfacing identical evidence.
        """
        return await self._execute(
            as_of,
            enabled=enabled,
            window_days=None,
            persist=True,
            include_unchanged=include_unchanged,
        )

    async def _execute(
        self,
        as_of: datetime,
        *,
        enabled: set[str] | None,
        window_days: int | None,
        persist: bool,
        include_unchanged: bool,
    ) -> list[Signal]:
        if window_days is not None and window_days < 1:
            raise ValueError("window_days must be >= 1")
        active = self._active_patterns(enabled)
        self._last_coverage_gaps = []
        signals: list[Signal] = []
        async with self._client._driver.session() as session:  # noqa: SLF001
            for pattern in active:
                preflight_gap = await self._coverage_preflight(pattern, as_of)
                if preflight_gap is not None:
                    self._record_coverage_gap(preflight_gap)
                    continue
                precision = await self._precision.estimate(
                    pattern.name,
                    pattern.precision_lineage,
                    as_of,
                    self._access,
                )
                result = await session.run(
                    pattern.cypher,
                    {
                        "as_of": as_of.isoformat(),
                        "window_days": (
                            pattern.prediction_horizon_days
                            if window_days is None
                            else min(window_days, pattern.prediction_horizon_days)
                        ),
                        "freshness_days": pattern.freshness_days,
                        "allowed_source_ids": sorted(self._access.allowed_source_ids),
                        "side": self._access.principal.side.value,
                        **pattern.query_parameters,
                    },
                )
                rows = [record.data() async for record in result]
                grouped: dict[str, list[_PatternCandidate]] = {}
                for row in rows:
                    candidate = await self._candidate(pattern, row)
                    coverage_gap = await self._candidate_coverage_gap(pattern, candidate, as_of)
                    if coverage_gap is not None:
                        self._record_coverage_gap(coverage_gap)
                        continue
                    grouped.setdefault(candidate.signal_id, []).append(candidate)
                seen = set(grouped)
                for candidates in grouped.values():
                    candidate = _combine_candidates(candidates)
                    previous = (
                        await self._load_previous(session, candidate.signal_id) if persist else None
                    )
                    signal, decision = self._build_signal(
                        pattern,
                        candidate,
                        as_of,
                        previous,
                        precision,
                    )
                    if persist:
                        await self._persist_signal(session, signal, decision.score_anchor)
                    if signal.actionable or (
                        include_unchanged
                        and signal.lifecycle_state is SignalLifecycleState.UNCHANGED
                    ):
                        signals.append(signal)
                resolved = (
                    await self._resolve_missing(session, pattern, seen, as_of) if persist else 0
                )
                self._log.info(
                    "pattern.ran",
                    pattern=pattern.name,
                    pattern_version=pattern.version,
                    matched=len(seen),
                    surfaced=sum(1 for signal in signals if signal.pattern == pattern.name),
                    resolved=resolved,
                    as_of=str(as_of.date()),
                )
        signals.sort(key=lambda signal: signal.opportunity_score, reverse=True)
        return signals

    async def _coverage_preflight(
        self, pattern: Pattern, as_of: datetime
    ) -> DetectorCoverageGap | None:
        if not pattern.computed_coverage_scopes:
            return None
        decision = await self._coverage.preflight(
            CoverageRequest(
                pattern_name=pattern.name,
                entity_key="",
                as_of=as_of,
                freshness_days=pattern.freshness_days,
                allowed_source_ids=self._access.allowed_source_ids,
                scopes=pattern.computed_coverage_scopes,
            )
        )
        if decision.complete:
            return None
        return DetectorCoverageGap(
            pattern_name=pattern.name,
            reasons=decision.reasons,
            checked_source_ids=decision.checked_source_ids,
        )

    async def _candidate_coverage_gap(
        self,
        pattern: Pattern,
        candidate: _PatternCandidate,
        as_of: datetime,
    ) -> DetectorCoverageGap | None:
        if not pattern.computed_coverage_scopes:
            return None
        decision = await self._coverage.assess(
            CoverageRequest(
                pattern_name=pattern.name,
                entity_key=candidate.entity_key,
                as_of=as_of,
                freshness_days=pattern.freshness_days,
                allowed_source_ids=self._access.allowed_source_ids,
                scopes=pattern.computed_coverage_scopes,
            )
        )
        if decision.complete:
            return None
        return DetectorCoverageGap(
            pattern_name=pattern.name,
            entity_key=candidate.entity_key,
            reasons=decision.reasons,
            checked_source_ids=decision.checked_source_ids,
        )

    def _record_coverage_gap(self, gap: DetectorCoverageGap) -> None:
        self._last_coverage_gaps.append(gap)
        self._log.info(
            "pattern.coverage_incomplete",
            pattern=gap.pattern_name,
            entity_key=gap.entity_key,
            reasons=gap.reasons,
            checked_source_ids=gap.checked_source_ids,
        )

    def _active_patterns(self, enabled: set[str] | None) -> tuple[Pattern, ...]:
        if enabled is not None:
            unknown = enabled - self._patterns.keys()
            if unknown:
                raise ValueError(f"unknown patterns: {sorted(unknown)}")
        names = self._patterns if enabled is None else enabled
        return tuple(
            pattern for name in sorted(names) if (pattern := self._patterns[name]).deployable
        )

    async def _candidate(
        self,
        pattern: Pattern,
        row: dict[str, Any],
    ) -> _PatternCandidate:
        raw_assertion_ids = tuple(str(value) for value in row.pop("_assertion_ids"))
        raw_source_ids = tuple(str(value) for value in row.pop("_source_ids"))
        raw_source_doc_ids = tuple(str(value) for value in row.pop("_source_doc_ids"))
        raw_barrier_sides = tuple(str(value) for value in row.pop("_barrier_sides"))
        if not raw_assertion_ids:
            raise RuntimeError(f"pattern {pattern.name!r} returned no assertion provenance")
        provenance_lengths = {
            len(raw_assertion_ids),
            len(raw_source_ids),
            len(raw_source_doc_ids),
            len(raw_barrier_sides),
        }
        if len(provenance_lengths) != 1:
            raise RuntimeError(f"pattern {pattern.name!r} returned misaligned provenance")

        assertion_ids = tuple(sorted(set(raw_assertion_ids)))
        refs = tuple(
            dict.fromkeys(
                zip(
                    raw_source_ids,
                    raw_source_doc_ids,
                    strict=True,
                )
            )
        )
        source_ids = tuple(source_id for source_id, _ in refs)
        source_doc_ids = tuple(doc_id for _, doc_id in refs)
        barrier_sides = tuple(BarrierSide(value) for value in raw_barrier_sides)
        unauthorized_sources = set(source_ids) - self._access.allowed_source_ids
        if unauthorized_sources:
            raise PermissionError(
                f"pattern {pattern.name!r} returned unauthorized sources: "
                f"{sorted(unauthorized_sources)}"
            )
        if (
            self._access.principal.side.value == BarrierSide.PUBLIC.value
            and BarrierSide.PRIVATE in barrier_sides
        ):
            raise PermissionError(f"pattern {pattern.name!r} crossed the public barrier")
        latest_recorded_at = _native_datetime(row.pop("_latest_recorded_at"))
        materiality_score = float(row.pop("_materiality_score"))
        evidence_confidence = float(row.pop("_evidence_confidence"))
        material_arguments = {
            name: str(row[name]) for name in pattern.material_arguments if row.get(name) is not None
        }
        if material_arguments.keys() != set(pattern.material_arguments):
            missing = sorted(set(pattern.material_arguments) - material_arguments.keys())
            raise RuntimeError(f"pattern {pattern.name!r} omitted material arguments {missing}")
        await self._client.audit_access(self._access, list(refs))
        signal_id = stable_signal_id(
            pattern,
            str(row["entity_key"]),
            material_arguments,
            self._authorization_scope,
        )
        return _PatternCandidate(
            signal_id=signal_id,
            entity_key=str(row["entity_key"]),
            entity_name=str(row["entity_name"]),
            evidence={
                key: str(value)
                for key, value in row.items()
                if key not in {"entity_key", "entity_name"} and not key.startswith("_")
            },
            material_arguments=material_arguments,
            assertion_ids=assertion_ids,
            source_ids=source_ids,
            source_doc_ids=source_doc_ids,
            barrier_side=(
                BarrierSide.PRIVATE if BarrierSide.PRIVATE in barrier_sides else BarrierSide.PUBLIC
            ),
            latest_recorded_at=latest_recorded_at,
            materiality_score=materiality_score,
            evidence_confidence=evidence_confidence,
        )

    def _build_signal(
        self,
        pattern: Pattern,
        candidate: _PatternCandidate,
        as_of: datetime,
        previous: SignalLifecycleSnapshot | None,
        precision: PatternPrecisionEstimate | None,
    ) -> tuple[Signal, LifecycleDecision]:
        preliminary_base, _, _ = score_signal(
            pattern,
            as_of=as_of,
            latest_recorded_at=candidate.latest_recorded_at,
            materiality_score=candidate.materiality_score,
            evidence_confidence=candidate.evidence_confidence,
            assertion_ids=candidate.assertion_ids,
            source_ids=candidate.source_ids,
            lifecycle_state=SignalLifecycleState.NEW,
            historical_precision=precision.rate if precision is not None else None,
            precision_samples=precision.samples if precision is not None else 0,
            precision_weight_scale=precision.weight_scale if precision is not None else None,
        )
        decision = classify_lifecycle(previous, preliminary_base, as_of)
        base_score, opportunity_score, contributions = score_signal(
            pattern,
            as_of=as_of,
            latest_recorded_at=candidate.latest_recorded_at,
            materiality_score=candidate.materiality_score,
            evidence_confidence=candidate.evidence_confidence,
            assertion_ids=candidate.assertion_ids,
            source_ids=candidate.source_ids,
            lifecycle_state=decision.state,
            historical_precision=precision.rate if precision is not None else None,
            precision_samples=precision.samples if precision is not None else 0,
            precision_weight_scale=precision.weight_scale if precision is not None else None,
        )
        return (
            Signal(
                signal_id=candidate.signal_id,
                pattern=pattern.name,
                pattern_version=pattern.version,
                hypothesis=pattern.hypothesis,
                eligible_outcome_kinds=tuple(sorted(pattern.eligible_outcome_kinds)),
                entity_key=candidate.entity_key,
                entity_name=candidate.entity_name,
                priority=round(opportunity_score * 100),
                opportunity_score=opportunity_score,
                ranking_base_score=base_score,
                score_contributions=contributions,
                lifecycle_state=decision.state,
                opened_at=decision.opened_at,
                updated_at=decision.updated_at,
                last_confirmed_at=decision.last_confirmed_at,
                resolved_at=decision.resolved_at,
                fired_at=decision.opened_at,
                as_of=as_of,
                evidence=candidate.evidence,
                material_arguments=candidate.material_arguments,
                matched_assertion_ids=candidate.assertion_ids,
                source_ids=candidate.source_ids,
                source_doc_ids=candidate.source_doc_ids,
                barrier_side=candidate.barrier_side,
                policy_version=self._access.policy_version,
                authorization_scope=self._authorization_scope,
                analyst_disposition=(previous.analyst_disposition if previous else None),
                analyst_reason=(previous.analyst_reason if previous else None),
            ),
            decision,
        )

    async def _load_previous(
        self,
        session: Any,
        signal_id: str,
    ) -> SignalLifecycleSnapshot | None:
        result = await session.run(
            """
            MATCH (s:Signal {signal_id: $signal_id, authorization_scope: $scope})
            RETURN s
            """,
            signal_id=signal_id,
            scope=self._authorization_scope,
        )
        record = await result.single()
        if record is None:
            return None
        node = record["s"]
        fallback = _native_datetime(node["as_of"])
        return SignalLifecycleSnapshot(
            state=SignalLifecycleState(node.get("lifecycle_state", "new")),
            opened_at=_optional_datetime(node.get("opened_at")) or fallback,
            updated_at=_optional_datetime(node.get("updated_at")) or fallback,
            last_confirmed_at=_optional_datetime(node.get("last_confirmed_at")) or fallback,
            resolved_at=_optional_datetime(node.get("resolved_at")),
            score_anchor=float(node.get("score_anchor", node.get("ranking_base_score", 0.0))),
            policy_version=str(node.get("policy_version", self._access.policy_version)),
            analyst_disposition=node.get("analyst_disposition"),
            analyst_reason=node.get("analyst_reason"),
        )

    async def _persist_signal(
        self,
        session: Any,
        signal: Signal,
        score_anchor: float,
    ) -> None:
        observation_id = _observation_id(
            signal.signal_id,
            signal.as_of,
            signal.lifecycle_state,
            signal.matched_assertion_ids,
        )
        await session.run(
            """
            MERGE (s:Signal {signal_id: $signal_id})
            ON CREATE SET
                s.pattern = $pattern,
                s.pattern_version = $pattern_version,
                s.authorization_scope = $authorization_scope,
                s.entity_key = $entity_key
            SET s.entity_name = $entity_name,
                s.opened_at = datetime($opened_at),
                s.policy_version = $policy_version,
                s.hypothesis = $hypothesis,
                s.eligible_outcome_kinds = $eligible_outcome_kinds,
                s.priority = $priority,
                s.opportunity_score = $opportunity_score,
                s.ranking_base_score = $ranking_base_score,
                s.score_anchor = $score_anchor,
                s.score_contributions_json = $score_contributions_json,
                s.lifecycle_state = $lifecycle_state,
                s.updated_at = datetime($updated_at),
                s.last_confirmed_at = datetime($last_confirmed_at),
                s.resolved_at = CASE WHEN $resolved_at IS NULL
                                     THEN null ELSE datetime($resolved_at) END,
                s.as_of = datetime($as_of),
                s.evidence_json = $evidence_json,
                s.material_arguments_json = $material_arguments_json,
                s.assertion_ids = $assertion_ids,
                s.source_ids = $source_ids,
                s.source_doc_ids = $source_doc_ids,
                s.barrier_side = $barrier_side,
                s.analyst_disposition = $analyst_disposition,
                s.analyst_reason = $analyst_reason,
                s.downstream_opportunity_ids = $downstream_opportunity_ids,
                s.outcome_ids = $outcome_ids
            MERGE (o:SignalObservation {observation_id: $observation_id})
            ON CREATE SET
                o.as_of = datetime($as_of),
                o.lifecycle_state = $lifecycle_state,
                o.entity_name = $entity_name,
                o.opened_at = datetime($opened_at),
                o.priority = $priority,
                o.opportunity_score = $opportunity_score,
                o.ranking_base_score = $ranking_base_score,
                o.score_anchor = $score_anchor,
                o.score_contributions_json = $score_contributions_json,
                o.updated_at = datetime($updated_at),
                o.last_confirmed_at = datetime($last_confirmed_at),
                o.resolved_at = CASE WHEN $resolved_at IS NULL
                                     THEN null ELSE datetime($resolved_at) END,
                o.evidence_json = $evidence_json,
                o.material_arguments_json = $material_arguments_json,
                o.assertion_ids = $assertion_ids,
                o.source_ids = $source_ids,
                o.source_doc_ids = $source_doc_ids,
                o.barrier_side = $barrier_side,
                o.policy_version = $policy_version,
                o.analyst_disposition = coalesce($analyst_disposition, ''),
                o.analyst_reason = coalesce($analyst_reason, ''),
                o.downstream_opportunity_ids = $downstream_opportunity_ids,
                o.outcome_ids = $outcome_ids
            MERGE (s)-[:HAS_OBSERVATION]->(o)
            WITH s, o
            OPTIONAL MATCH (s)-[old:SUPPORTED_BY]->(:Assertion)
            DELETE old
            WITH s, o
            MATCH (a:Assertion) WHERE a.assertion_id IN $assertion_ids
            MERGE (s)-[:SUPPORTED_BY]->(a)
            MERGE (o)-[:SUPPORTED_BY]->(a)
            """,
            signal_id=signal.signal_id,
            observation_id=observation_id,
            pattern=signal.pattern,
            pattern_version=signal.pattern_version,
            authorization_scope=signal.authorization_scope,
            entity_key=signal.entity_key,
            entity_name=signal.entity_name,
            hypothesis=signal.hypothesis,
            eligible_outcome_kinds=list(signal.eligible_outcome_kinds),
            priority=signal.priority,
            opportunity_score=signal.opportunity_score,
            ranking_base_score=signal.ranking_base_score,
            score_anchor=score_anchor,
            score_contributions_json=json.dumps(
                [item.model_dump(mode="json") for item in signal.score_contributions]
            ),
            lifecycle_state=signal.lifecycle_state.value,
            opened_at=_required_iso(signal.opened_at),
            updated_at=_required_iso(signal.updated_at),
            last_confirmed_at=_required_iso(signal.last_confirmed_at),
            resolved_at=signal.resolved_at.isoformat() if signal.resolved_at else None,
            as_of=signal.as_of.isoformat(),
            evidence_json=json.dumps(signal.evidence, sort_keys=True),
            material_arguments_json=json.dumps(signal.material_arguments, sort_keys=True),
            assertion_ids=list(signal.matched_assertion_ids),
            source_ids=list(signal.source_ids),
            source_doc_ids=list(signal.source_doc_ids),
            barrier_side=signal.barrier_side.value,
            policy_version=signal.policy_version,
            analyst_disposition=signal.analyst_disposition,
            analyst_reason=signal.analyst_reason,
            downstream_opportunity_ids=list(signal.downstream_opportunity_ids),
            outcome_ids=list(signal.outcome_ids),
        )

    async def _resolve_missing(
        self,
        session: Any,
        pattern: Pattern,
        seen: set[str],
        as_of: datetime,
    ) -> int:
        result = await session.run(
            """
            MATCH (s:Signal {pattern: $pattern, pattern_version: $pattern_version,
                             authorization_scope: $scope})
            WHERE s.lifecycle_state IN $active_states
              AND s.last_confirmed_at < datetime($as_of)
              AND NOT (s.signal_id IN $seen)
              AND size(coalesce(s.source_ids, [])) > 0
              AND all(source_id IN s.source_ids WHERE source_id IN $allowed_source_ids)
              AND (s.barrier_side = 'public' OR $side = 'private')
            RETURN s
            """,
            pattern=pattern.name,
            pattern_version=pattern.version,
            scope=self._authorization_scope,
            active_states=[state.value for state in _ACTIVE_STATES],
            as_of=as_of.isoformat(),
            seen=sorted(seen),
            allowed_source_ids=sorted(self._access.allowed_source_ids),
            side=self._access.principal.side.value,
        )
        nodes = [record["s"] async for record in result]
        for node in nodes:
            current = _signal_from_nodes(node, None)
            await self._client.audit_access(
                self._access,
                list(zip(current.source_ids, current.source_doc_ids, strict=True)),
            )
            opportunity_score, contributions = rescore_for_lifecycle(
                current.ranking_base_score,
                current.score_contributions,
                SignalLifecycleState.RESOLVED,
            )
            resolved = current.model_copy(
                update={
                    "lifecycle_state": SignalLifecycleState.RESOLVED,
                    "opportunity_score": opportunity_score,
                    "priority": round(opportunity_score * 100),
                    "score_contributions": contributions,
                    "updated_at": as_of,
                    "resolved_at": as_of,
                    "as_of": as_of,
                    "policy_version": self._access.policy_version,
                }
            )
            await self._persist_signal(
                session,
                resolved,
                score_anchor=float(node.get("score_anchor", node.get("ranking_base_score", 0.0))),
            )
        return len(nodes)

    async def explain(self, signal_id: str, *, as_of: datetime | None = None) -> Signal | None:
        """Return an authorized signal at the latest lifecycle observation."""
        async with self._client._driver.session() as session:  # noqa: SLF001
            result = await session.run(
                """
                MATCH (s:Signal {signal_id: $id, authorization_scope: $scope})
                OPTIONAL MATCH (s)-[:HAS_OBSERVATION]->(o:SignalObservation)
                WITH s, o
                WHERE $as_of IS NULL OR o.as_of <= datetime($as_of)
                ORDER BY o.as_of DESC
                WITH s, collect(o)[0] AS observation
                WHERE $as_of IS NULL OR observation IS NOT NULL
                WITH s, observation,
                     coalesce(observation.source_ids, s.source_ids) AS source_ids,
                     coalesce(observation.barrier_side, s.barrier_side) AS barrier_side
                WHERE size(coalesce(source_ids, [])) > 0
                  AND all(source_id IN source_ids WHERE source_id IN $allowed_source_ids)
                  AND (barrier_side = 'public' OR $side = 'private')
                RETURN s, observation
                """,
                id=signal_id,
                scope=self._authorization_scope,
                allowed_source_ids=sorted(self._access.allowed_source_ids),
                side=self._access.principal.side.value,
                as_of=as_of.isoformat() if as_of is not None else None,
            )
            record = await result.single()
            if record is None:
                return None
            signal = _signal_from_nodes(record["s"], record["observation"])
            await self._client.audit_access(
                self._access,
                list(zip(signal.source_ids, signal.source_doc_ids, strict=True)),
            )
            return signal

    async def suppress(
        self,
        signal_id: str,
        *,
        reason: str,
        disposition: str = "analyst_suppressed",
        at: datetime | None = None,
    ) -> Signal | None:
        """Suppress an authorized signal and retain the analyst rationale."""
        if not reason.strip():
            raise ValueError("suppression reason is required")
        existing = await self.explain(signal_id)
        if existing is None:
            return None
        suppressed_at = at or datetime.now(UTC)
        if existing.last_confirmed_at and suppressed_at < existing.last_confirmed_at:
            raise ValueError("suppression cannot precede the latest signal confirmation")
        opportunity_score, contributions = rescore_for_lifecycle(
            existing.ranking_base_score,
            existing.score_contributions,
            SignalLifecycleState.SUPPRESSED,
        )
        suppressed = existing.model_copy(
            update={
                "lifecycle_state": SignalLifecycleState.SUPPRESSED,
                "opportunity_score": opportunity_score,
                "priority": round(opportunity_score * 100),
                "score_contributions": contributions,
                "updated_at": suppressed_at,
                "as_of": suppressed_at,
                "policy_version": self._access.policy_version,
                "analyst_disposition": disposition,
                "analyst_reason": reason.strip(),
            }
        )
        async with self._client._driver.session() as session:  # noqa: SLF001
            await self._persist_signal(
                session,
                suppressed,
                score_anchor=existing.ranking_base_score,
            )
        return suppressed


def _signal_from_nodes(node: Any, observation: Any | None) -> Signal:
    snapshot = observation if observation is not None else node

    def snapshot_value(key: str, default: Any = None) -> Any:
        value = snapshot.get(key)
        if value is None and observation is not None:
            return node.get(key, default)
        return default if value is None else value

    current_as_of = _native_datetime(snapshot_value("as_of"))
    opened_at = _optional_datetime(snapshot_value("opened_at")) or current_as_of
    score_json = snapshot_value("score_contributions_json", "[]")
    contributions = tuple(
        ScoreContribution.model_validate(item) for item in json.loads(score_json or "[]")
    )
    lifecycle_state = SignalLifecycleState(snapshot_value("lifecycle_state", "new"))
    analyst_disposition = (
        (observation.get("analyst_disposition") or None)
        if observation is not None
        else node.get("analyst_disposition")
    )
    analyst_reason = (
        (observation.get("analyst_reason") or None)
        if observation is not None
        else node.get("analyst_reason")
    )
    downstream_opportunity_ids = (
        tuple(observation.get("downstream_opportunity_ids", []))
        if observation is not None
        else tuple(node.get("downstream_opportunity_ids", []))
    )
    outcome_ids = (
        tuple(observation.get("outcome_ids", []))
        if observation is not None
        else tuple(node.get("outcome_ids", []))
    )
    return Signal(
        signal_id=str(node["signal_id"]),
        pattern=str(node["pattern"]),
        pattern_version=str(node.get("pattern_version", "unversioned")),
        hypothesis=str(node.get("hypothesis", "")),
        eligible_outcome_kinds=tuple(node.get("eligible_outcome_kinds", [])),
        entity_key=str(node["entity_key"]),
        entity_name=str(snapshot_value("entity_name", "")),
        priority=int(snapshot_value("priority", 0)),
        opportunity_score=float(snapshot_value("opportunity_score", 0.0)),
        ranking_base_score=float(snapshot_value("ranking_base_score", 0.0)),
        score_contributions=contributions,
        lifecycle_state=lifecycle_state,
        opened_at=opened_at,
        updated_at=_optional_datetime(snapshot_value("updated_at")),
        last_confirmed_at=_optional_datetime(snapshot_value("last_confirmed_at")),
        resolved_at=(
            _optional_datetime(observation.get("resolved_at"))
            if observation is not None
            else _optional_datetime(node.get("resolved_at"))
        ),
        fired_at=opened_at,
        as_of=current_as_of,
        evidence=json.loads(snapshot_value("evidence_json", "{}")),
        material_arguments=json.loads(snapshot_value("material_arguments_json", "{}")),
        matched_assertion_ids=tuple(snapshot_value("assertion_ids", [])),
        source_ids=tuple(snapshot_value("source_ids", [])),
        source_doc_ids=tuple(snapshot_value("source_doc_ids", [])),
        barrier_side=BarrierSide(snapshot_value("barrier_side", "public")),
        policy_version=str(snapshot_value("policy_version", "unpersisted")),
        authorization_scope=str(node.get("authorization_scope", "unscoped")),
        analyst_disposition=analyst_disposition,
        analyst_reason=analyst_reason,
        downstream_opportunity_ids=downstream_opportunity_ids,
        outcome_ids=outcome_ids,
    )


def _native_datetime(value: Any) -> datetime:
    native = value.to_native() if hasattr(value, "to_native") else value
    if not isinstance(native, datetime):
        raise TypeError(f"expected datetime, got {type(native).__name__}")
    if native.tzinfo is None:
        return native.replace(tzinfo=UTC)
    return native


def _optional_datetime(value: Any | None) -> datetime | None:
    return None if value is None else _native_datetime(value)


def _required_iso(value: datetime | None) -> str:
    if value is None:
        raise ValueError("persisted signal lifecycle timestamp is required")
    return value.isoformat()


def _observation_id(
    signal_id: str,
    as_of: datetime,
    state: SignalLifecycleState,
    assertion_ids: tuple[str, ...],
) -> str:
    payload = json.dumps(
        {
            "signal_id": signal_id,
            "as_of": as_of.isoformat(),
            "state": state.value,
            "assertion_ids": sorted(set(assertion_ids)),
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


__all__ = [
    "DEFAULT_WINDOW_DAYS",
    "PatternRegistry",
    "ScoreContribution",
    "Signal",
    "SignalLifecycleState",
]
