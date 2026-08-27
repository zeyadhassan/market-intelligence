from evals.confidence_calibration import (
    CalibrationObservation,
    calibration_decision,
    reliability_curve,
)
from fi_intel.config import Settings


def test_uncalibrated_confidence_gate_is_disabled_by_default() -> None:
    assert Settings().min_extraction_confidence == 0.0


def test_flat_reliability_curve_disables_threshold() -> None:
    observations = [
        CalibrationObservation(confidence=(index % 10) / 10, correct=index % 2 == 0)
        for index in range(200)
    ]
    decision = calibration_decision(observations, minimum_samples=20)

    assert decision.action == "disable"
    assert decision.threshold is None
    assert decision.curve


def test_separated_buckets_can_earn_a_threshold() -> None:
    observations = [
        *[CalibrationObservation(confidence=0.2, correct=index < 10) for index in range(100)],
        *[CalibrationObservation(confidence=0.9, correct=index < 99) for index in range(100)],
    ]
    curve = reliability_curve(observations)
    decision = calibration_decision(
        observations,
        minimum_samples=100,
        minimum_precision_lower_bound=0.90,
    )

    assert len(curve) == 2
    assert curve[1].precision.lower > curve[0].precision.upper
    assert decision.action == "threshold"
    assert decision.threshold == 0.9
