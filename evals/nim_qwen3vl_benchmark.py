"""Smoke-test, evaluate, and load-test the local Qwen3-VL NVIDIA NIM.

The defaults target the model directly on the server so gateway behavior does
not contaminate the model measurements.  Point ``--base-url`` at the HTTPS
gateway when the routing path itself also needs to be exercised.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import json
import math
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import zlib
from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

DEFAULT_BASE_URL = "http://127.0.0.1:8899/v1"
DEFAULT_MODEL = "qwen3-vl-235b-nim"
DEFAULT_CONTAINER = "nim-qwen3vl"


@dataclass(frozen=True)
class RequestResult:
    request_id: str
    ok: bool
    latency_seconds: float
    ttft_seconds: float | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    finish_reason: str | None = None
    content: str = ""
    tool_calls: tuple[dict[str, Any], ...] = ()
    error: str | None = None


@dataclass(frozen=True)
class EvaluationCase:
    name: str
    description: str
    messages: tuple[dict[str, Any], ...]
    check: Callable[[RequestResult], tuple[bool, str]]
    extra_payload: dict[str, Any] | None = None


@dataclass(frozen=True)
class EvaluationResult:
    name: str
    description: str
    passed: bool
    detail: str
    latency_seconds: float
    finish_reason: str | None
    response_excerpt: str
    error: str | None


def _percentile(values: Sequence[float], percentile: float) -> float | None:
    """Return an interpolated percentile for a non-empty numeric sequence."""

    if not values:
        return None
    ordered = sorted(values)
    position = (len(ordered) - 1) * percentile
    lower = math.floor(position)
    upper = math.ceil(position)
    if lower == upper:
        return ordered[lower]
    weight = position - lower
    return ordered[lower] * (1.0 - weight) + ordered[upper] * weight


def _usage(payload: dict[str, Any]) -> tuple[int | None, int | None, int | None]:
    usage = payload.get("usage") or {}
    return (
        _optional_int(usage.get("prompt_tokens")),
        _optional_int(usage.get("completion_tokens")),
        _optional_int(usage.get("total_tokens")),
    )


def _optional_int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) else None


def _error_text(exc: Exception) -> str:
    if isinstance(exc, httpx.HTTPStatusError):
        body = re.sub(r"\s+", " ", exc.response.text).strip()[:500]
        return f"HTTP {exc.response.status_code}: {body or exc.response.reason_phrase}"
    return f"{type(exc).__name__}: {str(exc)[:500]}"


async def _non_streaming_request(
    client: httpx.AsyncClient,
    *,
    request_id: str,
    payload: dict[str, Any],
) -> RequestResult:
    started = time.perf_counter()
    try:
        response = await client.post("chat/completions", json=payload)
        response.raise_for_status()
        body = response.json()
        choice = body["choices"][0]
        message = choice.get("message") or {}
        content = message.get("content") or ""
        if not isinstance(content, str):
            content = json.dumps(content, ensure_ascii=False)
        tool_calls = message.get("tool_calls") or []
        prompt_tokens, completion_tokens, total_tokens = _usage(body)
        return RequestResult(
            request_id=request_id,
            ok=bool(content.strip() or tool_calls),
            latency_seconds=time.perf_counter() - started,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=choice.get("finish_reason"),
            content=content,
            tool_calls=tuple(call for call in tool_calls if isinstance(call, dict)),
            error=None if content.strip() or tool_calls else "empty response",
        )
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return RequestResult(
            request_id=request_id,
            ok=False,
            latency_seconds=time.perf_counter() - started,
            error=_error_text(exc),
        )


async def _streaming_request(
    client: httpx.AsyncClient,
    *,
    request_id: str,
    payload: dict[str, Any],
) -> RequestResult:
    started = time.perf_counter()
    first_token_at: float | None = None
    content_parts: list[str] = []
    finish_reason: str | None = None
    prompt_tokens: int | None = None
    completion_tokens: int | None = None
    total_tokens: int | None = None
    try:
        stream_payload = {**payload, "stream": True, "stream_options": {"include_usage": True}}
        async with client.stream("POST", "chat/completions", json=stream_payload) as response:
            response.raise_for_status()
            async for line in response.aiter_lines():
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if not data or data == "[DONE]":
                    continue
                chunk = json.loads(data)
                current_prompt, current_completion, current_total = _usage(chunk)
                prompt_tokens = current_prompt if current_prompt is not None else prompt_tokens
                completion_tokens = (
                    current_completion if current_completion is not None else completion_tokens
                )
                total_tokens = current_total if current_total is not None else total_tokens
                choices = chunk.get("choices") or []
                if not choices:
                    continue
                choice = choices[0]
                delta = choice.get("delta") or {}
                visible_piece = delta.get("content") or ""
                reasoning_piece = delta.get("reasoning_content") or ""
                tool_piece = delta.get("tool_calls") or []
                if first_token_at is None and (visible_piece or reasoning_piece or tool_piece):
                    first_token_at = time.perf_counter()
                if isinstance(visible_piece, str):
                    content_parts.append(visible_piece)
                finish_reason = choice.get("finish_reason") or finish_reason
        content = "".join(content_parts)
        elapsed = time.perf_counter() - started
        return RequestResult(
            request_id=request_id,
            ok=bool(content.strip()),
            latency_seconds=elapsed,
            ttft_seconds=(first_token_at - started) if first_token_at is not None else None,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            total_tokens=total_tokens,
            finish_reason=finish_reason,
            content=content,
            error=None if content.strip() else "empty streamed response",
        )
    except (httpx.HTTPError, json.JSONDecodeError, KeyError, TypeError, ValueError) as exc:
        return RequestResult(
            request_id=request_id,
            ok=False,
            latency_seconds=time.perf_counter() - started,
            ttft_seconds=(first_token_at - started) if first_token_at is not None else None,
            error=_error_text(exc),
        )


async def _request(
    client: httpx.AsyncClient,
    *,
    request_id: str,
    model: str,
    messages: Sequence[dict[str, Any]],
    max_tokens: int,
    temperature: float,
    stream: bool,
    extra_payload: dict[str, Any] | None = None,
) -> RequestResult:
    payload: dict[str, Any] = {
        "model": model,
        "messages": list(messages),
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if extra_payload:
        payload.update(extra_payload)
    if stream:
        return await _streaming_request(client, request_id=request_id, payload=payload)
    return await _non_streaming_request(client, request_id=request_id, payload=payload)


def _clean_answer(content: str) -> str:
    without_thinking = re.sub(r"<think>.*?</think>", "", content, flags=re.DOTALL | re.I)
    return without_thinking.strip().strip("`").strip()


def _exact(expected: str) -> Callable[[RequestResult], tuple[bool, str]]:
    def check(result: RequestResult) -> tuple[bool, str]:
        actual = _clean_answer(result.content)
        passed = result.ok and actual.casefold() == expected.casefold()
        return passed, f"expected exactly {expected!r}; received {actual[:160]!r}"

    return check


def _json_financial_check(result: RequestResult) -> tuple[bool, str]:
    content = _clean_answer(result.content)
    match = re.search(r"\{.*\}", content, flags=re.DOTALL)
    try:
        value = json.loads(match.group(0) if match else content)
    except (json.JSONDecodeError, AttributeError):
        return False, "response was not valid JSON"
    expected = {"currency": "QAR", "amount_millions": 125.5, "period": "H1 2026"}
    passed = value == expected
    return passed, f"expected {expected!r}; received {value!r}"


def _tool_call_check(result: RequestResult) -> tuple[bool, str]:
    for call in result.tool_calls:
        function = call.get("function") or {}
        if function.get("name") != "get_weather":
            continue
        arguments = function.get("arguments") or "{}"
        try:
            parsed = json.loads(arguments) if isinstance(arguments, str) else arguments
        except json.JSONDecodeError:
            return False, "get_weather arguments were not valid JSON"
        city = str(parsed.get("city", "")) if isinstance(parsed, dict) else ""
        if city.casefold() == "doha":
            return True, "received a structured get_weather(city=Doha) tool call"
        return False, f"get_weather used the wrong city: {city!r}"
    return False, "no structured get_weather tool call was returned"


def _solid_red_png_data_url(width: int = 32, height: int = 32) -> str:
    """Build a tiny deterministic PNG without adding an image dependency."""

    signature = b"\x89PNG\r\n\x1a\n"

    def chunk(kind: bytes, data: bytes) -> bytes:
        checksum = zlib.crc32(kind + data) & 0xFFFFFFFF
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", checksum)

    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)
    scanline = b"\x00" + (b"\xff\x00\x00" * width)
    pixels = scanline * height
    png = signature + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(pixels))
    png += chunk(b"IEND", b"")
    return "data:image/png;base64," + base64.b64encode(png).decode("ascii")


def _evaluation_cases() -> tuple[EvaluationCase, ...]:
    return (
        EvaluationCase(
            name="instruction_following",
            description="Follows a strict short-output instruction.",
            messages=({"role": "user", "content": "Reply with exactly: LOAD_TEST_OK"},),
            check=_exact("LOAD_TEST_OK"),
        ),
        EvaluationCase(
            name="financial_arithmetic",
            description="Computes a basic banking ratio without rounding drift.",
            messages=(
                {
                    "role": "user",
                    "content": (
                        "A bank has QAR 18.4 billion of CET1 capital and QAR 92 billion of "
                        "risk-weighted assets. Return only the CET1 ratio with two decimal "
                        "places and a percent sign."
                    ),
                },
            ),
            check=_exact("20.00%"),
        ),
        EvaluationCase(
            name="structured_json",
            description="Returns an exact machine-readable financial fact.",
            messages=(
                {
                    "role": "user",
                    "content": (
                        "Return only compact JSON with exactly these keys and values: currency "
                        "is QAR, amount_millions is the number 125.5, and period is H1 2026."
                    ),
                },
            ),
            check=_json_financial_check,
        ),
        EvaluationCase(
            name="arabic_instruction",
            description="Follows a minimal Arabic instruction.",
            messages=({"role": "user", "content": "أجب بالكلمة التالية فقط دون شرح: تم"},),
            check=_exact("تم"),
        ),
        EvaluationCase(
            name="vision_path",
            description="Exercises the Qwen3-VL image input path with known ground truth.",
            messages=(
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "text",
                            "text": "What is the dominant color of this image? Reply only RED.",
                        },
                        {
                            "type": "image_url",
                            "image_url": {"url": _solid_red_png_data_url()},
                        },
                    ],
                },
            ),
            check=_exact("RED"),
        ),
        EvaluationCase(
            name="tool_calling",
            description="Checks the configured Qwen tool-call parser and schema output.",
            messages=(
                {
                    "role": "user",
                    "content": "Use the get_weather tool to get the weather in Doha.",
                },
            ),
            check=_tool_call_check,
            extra_payload={
                "tools": [
                    {
                        "type": "function",
                        "function": {
                            "name": "get_weather",
                            "description": "Get the current weather for a city.",
                            "parameters": {
                                "type": "object",
                                "properties": {"city": {"type": "string"}},
                                "required": ["city"],
                            },
                        },
                    }
                ],
                "tool_choice": "auto",
                "parallel_tool_calls": False,
            },
        ),
    )


async def _run_evaluation(
    client: httpx.AsyncClient,
    *,
    model: str,
    max_tokens: int,
) -> list[EvaluationResult]:
    results: list[EvaluationResult] = []
    for case in _evaluation_cases():
        response = await _request(
            client,
            request_id=f"eval:{case.name}",
            model=model,
            messages=case.messages,
            max_tokens=max_tokens,
            temperature=0.0,
            stream=False,
            extra_payload=case.extra_payload,
        )
        passed, detail = case.check(response)
        results.append(
            EvaluationResult(
                name=case.name,
                description=case.description,
                passed=passed,
                detail=detail,
                latency_seconds=response.latency_seconds,
                finish_reason=response.finish_reason,
                response_excerpt=response.content[:500],
                error=response.error,
            )
        )
        state = "PASS" if passed else "FAIL"
        print(f"  [{state}] {case.name} ({response.latency_seconds:.2f}s): {detail}")
    return results


BUILTIN_LOAD_PROMPTS = (
    "In one sentence under 40 words, explain why a bank monitors its CET1 ratio under stress.",
    "Summarize in one sentence the difference between liquidity risk and solvency risk.",
    "A bank's net profit rose from QAR 4.8bn to QAR 5.4bn. State the percentage increase.",
    "List exactly three common early-warning indicators for corporate credit deterioration.",
    "اشرح في جملة واحدة لماذا تراقب البنوك نسبة القروض المتعثرة.",
)


def _load_prompts(path: Path | None) -> tuple[str, ...]:
    if path is None:
        return BUILTIN_LOAD_PROMPTS
    prompts = tuple(line.strip() for line in path.read_text(encoding="utf-8").splitlines())
    prompts = tuple(prompt for prompt in prompts if prompt and not prompt.startswith("#"))
    if not prompts:
        raise ValueError(f"prompt file contains no usable lines: {path}")
    return prompts


async def _run_load(
    client: httpx.AsyncClient,
    *,
    model: str,
    prompts: Sequence[str],
    requests: int,
    concurrency: int,
    max_tokens: int,
    temperature: float,
    stream: bool,
) -> tuple[list[RequestResult], float]:
    semaphore = asyncio.Semaphore(concurrency)
    completed = 0
    progress_lock = asyncio.Lock()

    async def one(index: int) -> RequestResult:
        nonlocal completed
        async with semaphore:
            result = await _request(
                client,
                request_id=f"load:{index + 1}",
                model=model,
                messages=({"role": "user", "content": prompts[index % len(prompts)]},),
                max_tokens=max_tokens,
                temperature=temperature,
                stream=stream,
            )
        async with progress_lock:
            completed += 1
            if completed == requests or completed % max(1, requests // 10) == 0:
                print(f"  completed {completed}/{requests}", flush=True)
        return result

    started = time.perf_counter()
    results = await asyncio.gather(*(one(index) for index in range(requests)))
    return list(results), time.perf_counter() - started


def _summarize_load(results: Sequence[RequestResult], wall_seconds: float) -> dict[str, Any]:
    successful = [result for result in results if result.ok]
    failures = [result for result in results if not result.ok]
    latencies = [result.latency_seconds for result in successful]
    ttfts = [result.ttft_seconds for result in successful if result.ttft_seconds is not None]
    prompt_tokens = sum(result.prompt_tokens or 0 for result in successful)
    completion_tokens = sum(result.completion_tokens or 0 for result in successful)
    total_tokens = sum(result.total_tokens or 0 for result in successful)
    return {
        "requests": len(results),
        "successful": len(successful),
        "failed": len(failures),
        "error_rate": len(failures) / len(results) if results else 0.0,
        "wall_seconds": wall_seconds,
        "requests_per_second": len(successful) / wall_seconds if wall_seconds else 0.0,
        "latency_seconds": {
            "min": min(latencies) if latencies else None,
            "mean": sum(latencies) / len(latencies) if latencies else None,
            "p50": _percentile(latencies, 0.50),
            "p95": _percentile(latencies, 0.95),
            "p99": _percentile(latencies, 0.99),
            "max": max(latencies) if latencies else None,
        },
        "ttft_seconds": {
            "p50": _percentile(ttfts, 0.50),
            "p95": _percentile(ttfts, 0.95),
            "p99": _percentile(ttfts, 0.99),
        },
        "tokens": {
            "prompt": prompt_tokens or None,
            "completion": completion_tokens or None,
            "total": total_tokens or None,
            "completion_tokens_per_second": (
                completion_tokens / wall_seconds if completion_tokens and wall_seconds else None
            ),
        },
        "finish_reasons": {
            reason: sum(result.finish_reason == reason for result in successful)
            for reason in sorted(
                {result.finish_reason for result in successful if result.finish_reason is not None}
            )
        },
        "failures": [
            {"request_id": result.request_id, "error": result.error} for result in failures[:20]
        ],
    }


def _podman_snapshot(container: str) -> dict[str, Any]:
    if shutil.which("podman") is None:
        return {"available": False, "reason": "podman executable not found"}
    commands = {
        "state": [
            "podman",
            "inspect",
            "--format",
            "{{.State.Status}}|{{.State.StartedAt}}|{{.RestartCount}}",
            container,
        ],
        "stats": [
            "podman",
            "stats",
            "--no-stream",
            "--format",
            "{{.Name}}|{{.CPU}}|{{.MemUsage}}|{{.NetIO}}|{{.BlockIO}}|{{.PIDs}}",
            container,
        ],
    }
    snapshot: dict[str, Any] = {"available": True, "container": container}
    for name, command in commands.items():
        try:
            completed = subprocess.run(  # noqa: S603
                command,
                check=False,
                capture_output=True,
                text=True,
                timeout=15,
            )
        except (OSError, subprocess.SubprocessError) as exc:
            snapshot[name] = {"ok": False, "error": _error_text(exc)}
            continue
        snapshot[name] = {
            "ok": completed.returncode == 0,
            "value": completed.stdout.strip() if completed.returncode == 0 else None,
            "error": completed.stderr.strip()[:500] if completed.returncode != 0 else None,
        }
    return snapshot


def _container_is_running(snapshot: dict[str, Any]) -> bool:
    state = snapshot.get("state") or {}
    value = state.get("value") if isinstance(state, dict) else None
    return isinstance(value, str) and value.split("|", maxsplit=1)[0] == "running"


def _format_metric(value: Any, suffix: str = "") -> str:
    return "n/a" if value is None else f"{value:.3f}{suffix}"


def _print_load_summary(summary: dict[str, Any]) -> None:
    latency = summary["latency_seconds"]
    ttft = summary["ttft_seconds"]
    tokens = summary["tokens"]
    print(
        f"  success: {summary['successful']}/{summary['requests']} "
        f"(error rate {summary['error_rate']:.1%})"
    )
    print(
        "  latency: "
        f"p50={_format_metric(latency['p50'], 's')} "
        f"p95={_format_metric(latency['p95'], 's')} "
        f"p99={_format_metric(latency['p99'], 's')} "
        f"max={_format_metric(latency['max'], 's')}"
    )
    print(
        "  TTFT: "
        f"p50={_format_metric(ttft['p50'], 's')} "
        f"p95={_format_metric(ttft['p95'], 's')} "
        f"p99={_format_metric(ttft['p99'], 's')}"
    )
    print(
        f"  throughput: {summary['requests_per_second']:.3f} requests/s; "
        f"{_format_metric(tokens['completion_tokens_per_second'], ' completion tokens/s')}"
    )
    if summary["finish_reasons"]:
        print(f"  finish reasons: {summary['finish_reasons']}")
    for failure in summary["failures"][:5]:
        print(f"  failure {failure['request_id']}: {failure['error']}")


def _parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL)
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--container", default=DEFAULT_CONTAINER)
    parser.add_argument("--requests", type=int, default=20, help="Measured load requests.")
    parser.add_argument("--concurrency", type=int, default=2)
    parser.add_argument("--warmup", type=int, default=2, help="Sequential unmeasured requests.")
    parser.add_argument("--max-tokens", type=int, default=128)
    parser.add_argument("--eval-max-tokens", type=int, default=256)
    parser.add_argument("--temperature", type=float, default=0.0)
    parser.add_argument("--timeout", type=float, default=300.0)
    parser.add_argument(
        "--stream",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use streaming load requests so time-to-first-token can be measured.",
    )
    parser.add_argument(
        "--prompt-file",
        type=Path,
        help="UTF-8 file with one load prompt per line.",
    )
    parser.add_argument("--skip-eval", action="store_true")
    parser.add_argument("--skip-load", action="store_true")
    parser.add_argument("--insecure", action="store_true", help="Disable TLS certificate checks.")
    parser.add_argument("--trust-env", action="store_true", help="Honor HTTP proxy environment.")
    parser.add_argument("--api-key-env", default="NIM_API_KEY")
    parser.add_argument("--basic-auth-user-env", default="NIM_BASIC_AUTH_USERNAME")
    parser.add_argument("--basic-auth-password-env", default="NIM_BASIC_AUTH_PASSWORD")
    parser.add_argument("--output", type=Path, help="Write the full JSON report to this path.")
    parser.add_argument("--min-quality-score", type=float, default=0.80)
    parser.add_argument("--max-error-rate", type=float, default=0.0)
    parser.add_argument("--max-p95-seconds", type=float)
    parser.add_argument("--require-container-running", action="store_true")
    return _validate_args(parser, parser.parse_args(argv))


def _validate_args(
    parser: argparse.ArgumentParser,
    args: argparse.Namespace,
) -> argparse.Namespace:
    rules = (
        (1 <= args.requests <= 100_000, "--requests must be between 1 and 100000"),
        (1 <= args.concurrency <= 64, "--concurrency must be between 1 and 64"),
        (args.concurrency <= args.requests, "--concurrency cannot exceed --requests"),
        (0 <= args.warmup <= 100, "--warmup must be between 0 and 100"),
        (args.max_tokens >= 1 and args.eval_max_tokens >= 1, "token limits must be positive"),
        (args.timeout > 0, "--timeout must be positive"),
        (0.0 <= args.temperature <= 2.0, "--temperature must be between 0 and 2"),
        (
            0.0 <= args.min_quality_score <= 1.0,
            "--min-quality-score must be between 0 and 1",
        ),
        (0.0 <= args.max_error_rate <= 1.0, "--max-error-rate must be between 0 and 1"),
        (not (args.skip_eval and args.skip_load), "cannot skip both evaluation and load test"),
    )
    for valid, message in rules:
        if not valid:
            parser.error(message)
    return args


def _client_auth(args: argparse.Namespace) -> tuple[dict[str, str], httpx.BasicAuth | None]:
    headers = {"Accept": "application/json"}
    api_key = os.getenv(args.api_key_env)
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    username = os.getenv(args.basic_auth_user_env)
    password = os.getenv(args.basic_auth_password_env)
    if bool(username) != bool(password):
        raise RuntimeError(
            f"{args.basic_auth_user_env} and {args.basic_auth_password_env} must be set together"
        )
    auth = httpx.BasicAuth(username, password) if username and password else None
    return headers, auth


async def _evaluate_and_gate(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    failures: list[str],
) -> list[EvaluationResult]:
    if args.skip_eval:
        return []
    print("\nDeterministic capability evaluation:")
    evaluations = await _run_evaluation(
        client,
        model=args.model,
        max_tokens=args.eval_max_tokens,
    )
    quality_score = sum(item.passed for item in evaluations) / len(evaluations)
    print(f"  quality score: {quality_score:.1%}")
    if quality_score < args.min_quality_score:
        failures.append(f"quality score {quality_score:.1%} is below {args.min_quality_score:.1%}")
    return evaluations


async def _warm_up(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    prompts: Sequence[str],
) -> None:
    print(f"\nWarmup: {args.warmup} sequential request(s)")
    for index in range(args.warmup):
        warmup = await _request(
            client,
            request_id=f"warmup:{index + 1}",
            model=args.model,
            messages=({"role": "user", "content": prompts[index % len(prompts)]},),
            max_tokens=args.max_tokens,
            temperature=args.temperature,
            stream=args.stream,
        )
        if not warmup.ok:
            raise RuntimeError(f"warmup {index + 1} failed: {warmup.error}")


async def _load_and_gate(
    client: httpx.AsyncClient,
    args: argparse.Namespace,
    prompts: Sequence[str],
    failures: list[str],
) -> tuple[list[RequestResult], dict[str, Any] | None]:
    if args.skip_load:
        return [], None
    await _warm_up(client, args, prompts)
    print(
        f"Load test: {args.requests} requests at concurrency {args.concurrency} "
        f"(stream={args.stream})"
    )
    load_results, wall_seconds = await _run_load(
        client,
        model=args.model,
        prompts=prompts,
        requests=args.requests,
        concurrency=args.concurrency,
        max_tokens=args.max_tokens,
        temperature=args.temperature,
        stream=args.stream,
    )
    load_summary = _summarize_load(load_results, wall_seconds)
    _print_load_summary(load_summary)
    if load_summary["error_rate"] > args.max_error_rate:
        failures.append(
            f"load error rate {load_summary['error_rate']:.1%} exceeds {args.max_error_rate:.1%}"
        )
    p95 = load_summary["latency_seconds"]["p95"]
    if args.max_p95_seconds is not None and (p95 is None or p95 > args.max_p95_seconds):
        failures.append(
            f"p95 latency {_format_metric(p95, 's')} exceeds {args.max_p95_seconds:.3f}s"
        )
    return load_results, load_summary


async def _async_main(args: argparse.Namespace) -> tuple[dict[str, Any], list[str]]:
    base_url = args.base_url.rstrip("/") + "/"
    prompts = _load_prompts(args.prompt_file)
    before = _podman_snapshot(args.container)
    print(f"Target: {base_url}  model={args.model}  container={args.container}")
    if before.get("available"):
        state = before.get("state", {})
        print(f"Container before: {state.get('value') or state.get('error') or 'unknown'}")
    else:
        print(f"Container telemetry unavailable: {before.get('reason')}")

    failures: list[str] = []
    if args.require_container_running and not _container_is_running(before):
        failures.append(f"container {args.container!r} is not confirmed running")

    headers, auth = _client_auth(args)
    timeout = httpx.Timeout(args.timeout)

    evaluations: list[EvaluationResult] = []
    load_results: list[RequestResult] = []
    load_summary: dict[str, Any] | None = None
    async with httpx.AsyncClient(
        base_url=base_url,
        headers=headers,
        auth=auth,
        verify=not args.insecure,
        trust_env=args.trust_env,
        timeout=timeout,
        limits=httpx.Limits(
            max_connections=max(args.concurrency + 2, 10),
            max_keepalive_connections=max(args.concurrency, 5),
        ),
    ) as client:
        evaluations = await _evaluate_and_gate(client, args, failures)
        load_results, load_summary = await _load_and_gate(client, args, prompts, failures)

    after = _podman_snapshot(args.container)
    if after.get("available"):
        stats = after.get("stats", {})
        print(f"Container after: {stats.get('value') or stats.get('error') or 'unknown'}")

    report = {
        "schema_version": 1,
        "generated_at": datetime.now(UTC).isoformat(),
        "target": {
            "base_url": base_url,
            "model": args.model,
            "container": args.container,
            "tls_verify": not args.insecure,
            "trust_env": args.trust_env,
        },
        "configuration": {
            "requests": args.requests,
            "concurrency": args.concurrency,
            "warmup": args.warmup,
            "max_tokens": args.max_tokens,
            "eval_max_tokens": args.eval_max_tokens,
            "temperature": args.temperature,
            "timeout": args.timeout,
            "stream": args.stream,
            "prompt_file": str(args.prompt_file) if args.prompt_file else None,
            "min_quality_score": args.min_quality_score,
            "max_error_rate": args.max_error_rate,
            "max_p95_seconds": args.max_p95_seconds,
        },
        "evaluation": [asdict(item) for item in evaluations],
        "load_summary": load_summary,
        "load_requests": [asdict(item) for item in load_results],
        "container_before": before,
        "container_after": after,
        "gate_failures": failures,
        "passed": not failures,
    }
    return report, failures


def main(argv: Sequence[str] | None = None) -> int:
    args = _parse_args(argv)
    try:
        report, failures = asyncio.run(_async_main(args))
    except (OSError, RuntimeError, ValueError) as exc:
        print(f"Benchmark failed: {_error_text(exc)}", file=sys.stderr)
        return 2
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(
            json.dumps(report, ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8",
        )
        print(f"\nJSON report: {args.output}")
    if failures:
        print("\nFAILED gates:", file=sys.stderr)
        for failure in failures:
            print(f"  - {failure}", file=sys.stderr)
        return 1
    print("\nAll configured gates passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
