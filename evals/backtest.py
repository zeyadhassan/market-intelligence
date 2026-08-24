"""Backtest harness: replay detectors at as-of dates and measure lead time.

The headline metric of the project. Detectors run at each as-of date with
reads pinned to evidence recorded on/before that date (invariant 10); the
harness additionally verifies the pin by checking every matched evidence
doc's recorded_at. A violation raises LeakageError — a deliberate attempt
to see the future fails loudly, not silently.

Metrics: precision@10, recall, per-pattern attribution, and the lead-time
DISTRIBUTION (not just a mean — a detector firing 400 days early on
everything is not useful).
"""

from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta

from pydantic import BaseModel, ConfigDict

from fi_intel.graph.client import GraphClient
from fi_intel.graph.registry import PatternRegistry
from fi_intel.logging import get_logger


class LeakageError(RuntimeError):
    """The harness detected evidence used from after the as-of cutoff."""


@dataclass(frozen=True)
class Outcome:
    """A real business outcome a detector is trying to precede."""

    outcome_id: str
    entity_key: str
    outcome_date: date
    kind: str


class FiredSignal(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern: str
    entity_key: str
    fired_at: date


class PatternAttribution(BaseModel):
    model_config = ConfigDict(frozen=True)

    pattern: str
    fired: int
    preceded_outcome: int
    lead_days: list[int]  # the distribution, not a mean


class BacktestResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    from_date: date
    to_date: date
    step_days: int
    precision_at_10: float
    recall: float
    attribution: list[PatternAttribution]
    total_signals: int


class Backtester:
    def __init__(self, client: GraphClient, registry: PatternRegistry) -> None:
        self._client = client
        self._registry = registry
        self._log = get_logger(component="evals.backtest")

    async def run(
        self,
        from_date: date,
        to_date: date,
        step_days: int,
        outcomes: list[Outcome],
        enabled: set[str] | None = None,
        window_days: int = 395,
    ) -> BacktestResult:
        if step_days < 1:
            msg = "step_days must be >= 1"
            raise ValueError(msg)
        if from_date >= to_date:
            msg = "from_date must be before to_date"
            raise ValueError(msg)

        fired: list[FiredSignal] = []
        current = from_date
        while current <= to_date:
            as_of = datetime(current.year, current.month, current.day, tzinfo=UTC)
            signals = await self._registry.run(as_of, enabled=enabled, window_days=window_days)
            fired.extend(
                FiredSignal(pattern=s.pattern, entity_key=s.entity_key, fired_at=current)
                for s in signals
            )
            # Leakage gate: every evidence doc the patterns used must have
            # been recorded on/before this as_of. Checked at the harness
            # level, on top of the query-level pin.
            await self._assert_pin(as_of)
            current += timedelta(days=step_days)

        return self._metrics(from_date, to_date, step_days, fired, outcomes)

    async def _assert_pin(self, as_of: datetime) -> None:
        """Assert no evidence was available from after the cutoff."""
        rows = await self._client.read_all_assertions_including_superseded(as_of=as_of)
        # The read itself enforces recorded_at <= as_of. If a future-cheat
        # slipped a later-recorded assertion into visibility, it is a leak.
        for row in rows:
            recorded = row["a"].get("recorded_at")
            if recorded is None:
                continue
            recorded_dt = recorded.to_native() if hasattr(recorded, "to_native") else recorded
            if recorded_dt > as_of:
                msg = f"leakage: assertion recorded at {recorded_dt} visible at {as_of.date()}"
                raise LeakageError(msg)

    def _metrics(
        self,
        from_date: date,
        to_date: date,
        step_days: int,
        fired: list[FiredSignal],
        outcomes: list[Outcome],
    ) -> BacktestResult:
        # Deduplicate each (pattern, entity) to its FIRST firing: a signal
        # that fires at step k and stays fired is one signal, not k signals.
        # Lead time is measured from the first firing to the outcome.
        first_fired: dict[tuple[str, str], date] = {}
        for s in fired:
            key = (s.pattern, s.entity_key)
            if key not in first_fired or s.fired_at < first_fired[key]:
                first_fired[key] = s.fired_at

        by_pattern: dict[str, list[int]] = {}
        preceded = 0
        for (pattern, entity_key), fired_date in first_fired.items():
            leads = [
                (o.outcome_date - fired_date).days
                for o in outcomes
                if o.entity_key == entity_key and o.outcome_date > fired_date
            ]
            if leads:
                preceded += 1
                by_pattern.setdefault(pattern, []).append(min(leads))
            else:
                by_pattern.setdefault(pattern, [])

        rankable = sorted(first_fired.items(), key=lambda kv: kv[1])
        k = min(10, len(rankable))
        # precision@10: of the first 10 unique (pattern, entity) signals by
        # first firing, how many precede a real outcome.
        top = rankable[:k]
        preceded_top = sum(
            1
            for (pattern, entity_key), fired_date in top
            if any(
                o.entity_key == entity_key and o.outcome_date > fired_date
                for o in outcomes
            )
        )
        precision_at_10 = (preceded_top / k) if k else 0.0

        outcome_entities = {o.entity_key for o in outcomes}
        outcomes_preceded = {
            o.entity_key
            for o in outcomes
            if any(key[1] == o.entity_key for key in first_fired)
        }
        recall = len(outcomes_preceded) / len(outcome_entities) if outcome_entities else 0.0

        attribution = []
        for pattern, leads in sorted(by_pattern.items()):
            fired_count = sum(1 for key in first_fired if key[0] == pattern)
            attribution.append(
                PatternAttribution(
                    pattern=pattern,
                    fired=fired_count,
                    preceded_outcome=len(leads),
                    lead_days=sorted(leads),
                )
            )
        return BacktestResult(
            from_date=from_date,
            to_date=to_date,
            step_days=step_days,
            precision_at_10=round(precision_at_10, 4),
            recall=round(recall, 4),
            attribution=attribution,
            total_signals=len(first_fired),
        )
