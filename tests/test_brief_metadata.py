"""Service-free tests for brief reliability, triage, and coverage metadata."""

import asyncio
from datetime import UTC, datetime
from typing import cast

from fi_intel.agents.brief import BriefCompiler
from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.agents.render import render_html
from fi_intel.config import Settings
from fi_intel.graph.coverage import DetectorCoverageGap
from fi_intel.graph.registry import PatternRegistry, Signal
from fi_intel.tools.evidence import Opportunity

NOW = datetime(2024, 6, 1, tzinfo=UTC)


class _Registry:
    def __init__(
        self,
        signals: list[Signal],
        gaps: tuple[DetectorCoverageGap, ...] = (),
    ) -> None:
        self._signals = signals
        self.last_coverage_gaps = gaps

    async def run(self, as_of: datetime, enabled: set[str] | None = None) -> list[Signal]:
        del as_of, enabled
        return self._signals


def _signal(signal_id: str, priority: int) -> Signal:
    return Signal(
        signal_id=signal_id,
        pattern="test_pattern",
        entity_key="LEI-1",
        entity_name="Example Bank",
        priority=priority,
        fired_at=NOW,
        as_of=NOW,
        evidence={},
    )


def _compiler(registry: _Registry) -> BriefCompiler:
    return BriefCompiler(
        cast(PatternRegistry, registry),
        cast(OpportunityResearcher, object()),
    )


async def test_empty_brief_reports_structurally_dark_detectors() -> None:
    gap = DetectorCoverageGap(
        pattern_name="maturity_wall_no_refi",
        reasons=("no required source universe is configured",),
    )

    brief = await _compiler(_Registry([], (gap,))).compile(NOW, "fi_gcc")

    assert brief.coverage_complete is False
    assert brief.dark_detectors == [gap]
    page = render_html(brief)
    assert "maturity_wall_no_refi" in page
    assert "no required source universe is configured" in page
    assert "Brief incomplete" in page


async def test_brief_renders_score_distribution_against_threshold() -> None:
    brief = await _compiler(_Registry([_signal("low", 48), _signal("edge", 59)])).compile(
        NOW, "fi_gcc"
    )

    assert brief.triage_scores.threshold == 60
    assert brief.triage_scores.minimum == 48
    assert brief.triage_scores.median == 53.5
    assert brief.triage_scores.maximum == 59
    page = render_html(brief)
    assert "Signal scores ranged 48-59 (median 53.5); threshold 60" in page


class _ConcurrentResearcher:
    model_version = "fixture-concurrency-v1"
    last_trajectory = None

    def __init__(self, *, fail_signal_id: str | None = None, delay: float = 0.02) -> None:
        self.fail_signal_id = fail_signal_id
        self.delay = delay
        self.active = 0
        self.maximum_active = 0

    async def research_signal(self, signal: Signal) -> tuple[Opportunity, list]:
        self.active += 1
        self.maximum_active = max(self.maximum_active, self.active)
        try:
            await asyncio.sleep(self.delay)
            if signal.signal_id == self.fail_signal_id:
                raise RuntimeError("injected per-signal failure")
            return (
                Opportunity(
                    title=f"Supported {signal.signal_id}",
                    signal_id=signal.signal_id,
                    entity_key=signal.entity_key,
                    summary="Supported by the test harness.",
                    falsifier="Contradictory evidence appears.",
                    evidence_ids=[],
                ),
                [],
            )
        finally:
            self.active -= 1


async def test_signal_failures_are_isolated_under_bounded_concurrency() -> None:
    registry = _Registry([_signal(str(index), 90) for index in range(4)])
    researcher = _ConcurrentResearcher(fail_signal_id="1")
    compiler = BriefCompiler(
        cast(PatternRegistry, registry),
        cast(OpportunityResearcher, researcher),
        settings=Settings(daily_signal_concurrency=2, daily_signal_timeout_seconds=1.0),
    )

    brief = await compiler.compile(NOW, "fi_gcc")

    assert researcher.maximum_active == 2
    assert {item.signal.signal_id for item in brief.items} == {"0", "2", "3"}
    assert [failure.signal.signal_id for failure in brief.failed_signals] == ["1"]
    assert brief.coverage_complete is False
    assert brief.nothing_material is False


async def test_signal_deadline_becomes_visible_partial_coverage() -> None:
    registry = _Registry([_signal("slow", 90)])
    researcher = _ConcurrentResearcher(delay=0.05)
    compiler = BriefCompiler(
        cast(PatternRegistry, registry),
        cast(OpportunityResearcher, researcher),
        settings=Settings(daily_signal_concurrency=1, daily_signal_timeout_seconds=0.001),
    )

    brief = await compiler.compile(NOW, "fi_gcc")

    assert brief.items == []
    assert brief.nothing_material is False
    assert brief.coverage_complete is False
    assert brief.failed_signals[0].error_type == "TimeoutError"
