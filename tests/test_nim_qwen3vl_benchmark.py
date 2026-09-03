from __future__ import annotations

import base64
import json

import pytest

from evals.nim_qwen3vl_benchmark import (
    RequestResult,
    _exact,
    _json_financial_check,
    _percentile,
    _solid_red_png_data_url,
    _summarize_load,
    _tool_call_check,
)


def _result(**changes: object) -> RequestResult:
    values: dict[str, object] = {
        "request_id": "test",
        "ok": True,
        "latency_seconds": 1.0,
    }
    values.update(changes)
    return RequestResult(**values)  # type: ignore[arg-type]


def test_percentile_interpolates_small_samples() -> None:
    assert _percentile([], 0.95) is None
    assert _percentile([1.0], 0.95) == 1.0
    assert _percentile([1.0, 3.0], 0.50) == 2.0


def test_exact_check_removes_reasoning_block() -> None:
    passed, _ = _exact("20.00%")(_result(content="<think>math</think>\n20.00%"))
    assert passed


def test_financial_json_check_requires_exact_typed_payload() -> None:
    valid = json.dumps({"currency": "QAR", "amount_millions": 125.5, "period": "H1 2026"})
    assert _json_financial_check(_result(content=valid))[0]
    assert not _json_financial_check(_result(content=valid[:-1] + ', "extra": true}'))[0]


def test_tool_check_accepts_structured_doha_call() -> None:
    tool_calls = (
        {
            "type": "function",
            "function": {"name": "get_weather", "arguments": '{"city":"Doha"}'},
        },
    )
    assert _tool_call_check(_result(tool_calls=tool_calls))[0]


def test_generated_visual_fixture_is_a_png() -> None:
    prefix, encoded = _solid_red_png_data_url().split(",", maxsplit=1)
    assert prefix == "data:image/png;base64"
    assert base64.b64decode(encoded).startswith(b"\x89PNG\r\n\x1a\n")


def test_load_summary_reports_latency_errors_and_throughput() -> None:
    results = [
        _result(latency_seconds=1.0, ttft_seconds=0.2, completion_tokens=10),
        _result(latency_seconds=3.0, ttft_seconds=0.4, completion_tokens=20),
        _result(ok=False, latency_seconds=2.0, error="boom"),
    ]
    summary = _summarize_load(results, wall_seconds=4.0)
    assert summary["successful"] == 2
    assert summary["failed"] == 1
    assert summary["error_rate"] == pytest.approx(1 / 3)
    assert summary["latency_seconds"]["p50"] == 2.0
    assert summary["ttft_seconds"]["p50"] == pytest.approx(0.3)
    assert summary["tokens"]["completion_tokens_per_second"] == 7.5
