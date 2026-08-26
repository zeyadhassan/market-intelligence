"""Service-free tests for brief triage and detector-coverage metadata."""

from datetime import UTC, datetime
from typing import cast

from fi_intel.agents.brief import BriefCompiler
from fi_intel.agents.opportunity_research import OpportunityResearcher
from fi_intel.agents.render import render_html
from fi_intel.graph.coverage import DetectorCoverageGap
from fi_intel.graph.registry import PatternRegistry, Signal

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
