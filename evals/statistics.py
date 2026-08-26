"""Statistical release gates that account for sample size and critical slices."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import NormalDist


@dataclass(frozen=True)
class BinaryCounts:
    successes: int
    total: int

    def __post_init__(self) -> None:
        if self.total < 0 or self.successes < 0 or self.successes > self.total:
            raise ValueError("binary counts require 0 <= successes <= total")

    @property
    def rate(self) -> float:
        return self.successes / self.total if self.total else 0.0


@dataclass(frozen=True)
class RateEstimate:
    rate: float
    lower: float
    upper: float
    total: int
    confidence: float


@dataclass(frozen=True)
class RateGate:
    name: str
    minimum_lower_bound: float
    minimum_samples: int
    confidence: float = 0.95
    required_slices: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if not self.name:
            raise ValueError("gate name cannot be empty")
        if not 0.0 <= self.minimum_lower_bound <= 1.0:
            raise ValueError("minimum lower bound must be between zero and one")
        if self.minimum_samples < 1:
            raise ValueError("minimum_samples must be positive")
        if not 0.0 < self.confidence < 1.0:
            raise ValueError("confidence must be between zero and one")


@dataclass(frozen=True)
class RateGateResult:
    name: str
    overall: RateEstimate
    slices: dict[str, RateEstimate]
    failures: tuple[str, ...]

    @property
    def passed(self) -> bool:
        return not self.failures


def wilson_interval(counts: BinaryCounts, confidence: float = 0.95) -> RateEstimate:
    """Return a two-sided Wilson score interval for one binary rate."""

    if counts.total == 0:
        return RateEstimate(rate=0.0, lower=0.0, upper=1.0, total=0, confidence=confidence)
    if not 0.0 < confidence < 1.0:
        raise ValueError("confidence must be between zero and one")
    z = NormalDist().inv_cdf(0.5 + confidence / 2.0)
    z_squared = z * z
    n = counts.total
    rate = counts.rate
    denominator = 1.0 + z_squared / n
    centre = (rate + z_squared / (2.0 * n)) / denominator
    half_width = (
        z * ((rate * (1.0 - rate) / n + z_squared / (4.0 * n * n)) ** 0.5) / denominator
    )
    return RateEstimate(
        rate=rate,
        lower=max(0.0, centre - half_width),
        upper=min(1.0, centre + half_width),
        total=n,
        confidence=confidence,
    )


def evaluate_rate_gate(
    overall: BinaryCounts,
    slices: dict[str, BinaryCounts],
    gate: RateGate,
) -> RateGateResult:
    """Fail when the overall or any declared critical slice is weak or absent."""

    overall_estimate = wilson_interval(overall, gate.confidence)
    slice_estimates = {
        name: wilson_interval(counts, gate.confidence) for name, counts in sorted(slices.items())
    }
    failures: list[str] = []
    _assess_estimate("overall", overall_estimate, gate, failures)
    for required in sorted(gate.required_slices):
        estimate = slice_estimates.get(required)
        if estimate is None:
            failures.append(f"required slice {required!r} is missing")
            continue
        _assess_estimate(f"slice {required!r}", estimate, gate, failures)
    return RateGateResult(
        name=gate.name,
        overall=overall_estimate,
        slices=slice_estimates,
        failures=tuple(failures),
    )


def _assess_estimate(
    label: str,
    estimate: RateEstimate,
    gate: RateGate,
    failures: list[str],
) -> None:
    if estimate.total < gate.minimum_samples:
        failures.append(
            f"{label} sample count {estimate.total} below minimum {gate.minimum_samples}"
        )
    if estimate.lower < gate.minimum_lower_bound:
        failures.append(
            f"{label} lower confidence bound {estimate.lower:.4f} below "
            f"minimum {gate.minimum_lower_bound:.4f}"
        )
