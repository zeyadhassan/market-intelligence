"""Reliability curves for deciding whether self-confidence may gate extraction."""

from __future__ import annotations

from dataclasses import dataclass

from evals.statistics import BinaryCounts, RateEstimate, wilson_interval


@dataclass(frozen=True)
class CalibrationObservation:
    confidence: float
    correct: bool

    def __post_init__(self) -> None:
        if not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True)
class ReliabilityBucket:
    lower: float
    upper: float
    mean_confidence: float
    precision: RateEstimate


@dataclass(frozen=True)
class CalibrationDecision:
    action: str
    threshold: float | None
    curve: tuple[ReliabilityBucket, ...]
    brier_score: float
    rationale: str


def reliability_curve(
    observations: list[CalibrationObservation],
    *,
    bucket_width: float = 0.1,
    confidence: float = 0.95,
) -> tuple[ReliabilityBucket, ...]:
    """Bucket labelled claims and report precision with Wilson intervals."""
    if not 0.0 < bucket_width <= 1.0:
        raise ValueError("bucket_width must be in (0, 1]")
    bucket_count = round(1.0 / bucket_width)
    if abs(bucket_count * bucket_width - 1.0) > 1e-9:
        raise ValueError("bucket_width must divide one exactly")
    grouped: list[list[CalibrationObservation]] = [[] for _ in range(bucket_count)]
    for observation in observations:
        index = min(int(observation.confidence / bucket_width), bucket_count - 1)
        grouped[index].append(observation)

    curve: list[ReliabilityBucket] = []
    for index, items in enumerate(grouped):
        if not items:
            continue
        counts = BinaryCounts(sum(item.correct for item in items), len(items))
        curve.append(
            ReliabilityBucket(
                lower=index * bucket_width,
                upper=(index + 1) * bucket_width,
                mean_confidence=sum(item.confidence for item in items) / len(items),
                precision=wilson_interval(counts, confidence),
            )
        )
    return tuple(curve)


def calibration_decision(
    observations: list[CalibrationObservation],
    *,
    minimum_samples: int = 100,
    minimum_precision_lower_bound: float = 0.80,
    bucket_width: float = 0.1,
) -> CalibrationDecision:
    """Recommend a threshold only when labelled confidence buckets separate."""
    curve = reliability_curve(observations, bucket_width=bucket_width)
    brier = (
        sum((item.confidence - float(item.correct)) ** 2 for item in observations)
        / len(observations)
        if observations
        else 1.0
    )
    eligible = [bucket for bucket in curve if bucket.precision.total >= minimum_samples]
    separates = any(
        high.lower > low.lower and high.precision.lower > low.precision.upper
        for low in eligible
        for high in eligible
    )
    if not separates:
        return CalibrationDecision(
            action="disable",
            threshold=None,
            curve=curve,
            brier_score=brier,
            rationale="labelled confidence buckets do not separate at the sample floor",
        )

    for bucket in sorted(eligible, key=lambda item: item.lower):
        admitted = [item for item in observations if item.confidence >= bucket.lower]
        estimate = wilson_interval(
            BinaryCounts(sum(item.correct for item in admitted), len(admitted))
        )
        if (
            estimate.total >= minimum_samples
            and estimate.lower >= minimum_precision_lower_bound
        ):
            return CalibrationDecision(
                action="threshold",
                threshold=bucket.lower,
                curve=curve,
                brier_score=brier,
                rationale="bucket separation and admitted precision clear governed gates",
            )
    return CalibrationDecision(
        action="disable",
        threshold=None,
        curve=curve,
        brier_score=brier,
        rationale="no separated threshold clears the admitted-precision lower bound",
    )
