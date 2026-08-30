"""Model-call cost/latency accounting.

Model usage is recorded separately from document-access auditing. Usage
logging is best-effort and does not fail the model call that produced it.
"""

from datetime import datetime
from typing import Literal, Protocol, runtime_checkable
from uuid import UUID

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.logging import get_logger

_log = get_logger(component="governance.model_usage")

# Optional per-million-token rates for internal cost estimates. Unknown
# models are recorded at zero cost rather than assigned a guessed rate.
MODEL_PRICING: dict[str, tuple[float, float]] = {}
ModelCallStatus = Literal["succeeded", "failed", "timed_out", "refused", "malformed"]


def estimate_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Best-effort spend estimate for one model call. See MODEL_PRICING."""
    rates = MODEL_PRICING.get(model)
    if rates is None:
        _log.warning("model_usage.unknown_pricing", model=model)
        return 0.0
    input_rate, output_rate = rates
    return (input_tokens * input_rate + output_tokens * output_rate) / 1_000_000.0


class ModelCallEvent(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    component: str  # extraction, reasoning, embedding, reranking, or entailment
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    latency_ms: float
    gpu_seconds: float | None = None
    subject_id: str  # governed, component-specific work-item identifier
    recorded_at: datetime
    status: ModelCallStatus = "succeeded"
    error_type: str | None = None
    release_id: UUID | None = None
    artifact_digest: str | None = Field(default=None, pattern=r"^[0-9a-f]{64}$")
    prompt_version: str | None = None
    schema_version: str | None = None


class ModelUsageSnapshot(BaseModel):
    """Measured or conservatively projected capacity consumed by one run."""

    model_config = ConfigDict(frozen=True)

    calls: int = Field(default=0, ge=0)
    input_tokens: int = Field(default=0, ge=0)
    output_tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: float = Field(default=0.0, ge=0.0)
    gpu_seconds: float | None = Field(default=None, ge=0.0)

    @property
    def total_tokens(self) -> int:
        return self.input_tokens + self.output_tokens

    def project(self, estimate: "ModelCallEstimate") -> "ModelUsageSnapshot":
        gpu_seconds = None
        if self.gpu_seconds is not None or estimate.gpu_seconds is not None:
            gpu_seconds = (self.gpu_seconds or 0.0) + (estimate.gpu_seconds or 0.0)
        return ModelUsageSnapshot(
            calls=self.calls + 1,
            input_tokens=self.input_tokens + estimate.input_tokens,
            output_tokens=self.output_tokens + estimate.output_tokens,
            cost_usd=self.cost_usd + estimate.cost_usd,
            latency_ms=self.latency_ms + estimate.latency_ms,
            gpu_seconds=gpu_seconds,
        )

    def conservative_merge(self, other: "ModelUsageSnapshot") -> "ModelUsageSnapshot":
        """Keep the larger observed/projected value for every capacity axis."""

        gpu_values = [value for value in (self.gpu_seconds, other.gpu_seconds) if value is not None]
        return ModelUsageSnapshot(
            calls=max(self.calls, other.calls),
            input_tokens=max(self.input_tokens, other.input_tokens),
            output_tokens=max(self.output_tokens, other.output_tokens),
            cost_usd=max(self.cost_usd, other.cost_usd),
            latency_ms=max(self.latency_ms, other.latency_ms),
            gpu_seconds=max(gpu_values) if gpu_values else None,
        )


class ModelCallEstimate(BaseModel):
    """P90-like historical estimate used before admitting another model call."""

    model_config = ConfigDict(frozen=True)

    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    latency_ms: float = Field(ge=0.0)
    gpu_seconds: float | None = Field(default=None, ge=0.0)
    sample_size: int = Field(default=0, ge=0)


class ModelCapacityLimits(BaseModel):
    """Hard per-brief capacity limits expressed in measurable units."""

    model_config = ConfigDict(frozen=True)

    max_calls: int = Field(default=5, ge=0)
    max_total_tokens: int = Field(default=100_000, ge=0)
    max_latency_ms: float = Field(default=600_000.0, ge=0.0)
    max_gpu_seconds: float | None = Field(default=None, ge=0.0)
    cold_start_estimate: ModelCallEstimate = Field(
        default_factory=lambda: ModelCallEstimate(
            input_tokens=12_000,
            output_tokens=2_000,
            cost_usd=0.0,
            latency_ms=60_000.0,
        )
    )

    def allows(self, current: ModelUsageSnapshot, estimate: ModelCallEstimate) -> bool:
        projected = current.project(estimate)
        if projected.calls > self.max_calls:
            return False
        if projected.total_tokens > self.max_total_tokens:
            return False
        if projected.latency_ms > self.max_latency_ms:
            return False
        if self.max_gpu_seconds is not None:
            if (
                estimate.gpu_seconds is None
                or projected.gpu_seconds is None
                or projected.gpu_seconds > self.max_gpu_seconds
            ):
                return False
        return True


@runtime_checkable
class ModelUsageLog(Protocol):
    async def record(self, event: ModelCallEvent) -> None: ...

    async def snapshot(self, run_id: str, component: str) -> ModelUsageSnapshot: ...

    async def estimate(
        self, component: str, model: str, fallback: ModelCallEstimate
    ) -> ModelCallEstimate: ...


class InMemoryModelUsageLog:
    def __init__(self) -> None:
        self.events: list[ModelCallEvent] = []

    async def record(self, event: ModelCallEvent) -> None:
        self.events.append(event)

    def total_cost_usd(self) -> float:
        return sum(e.cost_usd for e in self.events)

    async def snapshot(self, run_id: str, component: str) -> ModelUsageSnapshot:
        events = [
            event
            for event in self.events
            if event.run_id == run_id and event.component == component
        ]
        gpu_values = [event.gpu_seconds for event in events if event.gpu_seconds is not None]
        return ModelUsageSnapshot(
            calls=len(events),
            input_tokens=sum(event.input_tokens for event in events),
            output_tokens=sum(event.output_tokens for event in events),
            cost_usd=sum(event.cost_usd for event in events),
            latency_ms=sum(event.latency_ms for event in events),
            gpu_seconds=sum(gpu_values) if gpu_values else None,
        )

    async def estimate(
        self, component: str, model: str, fallback: ModelCallEstimate
    ) -> ModelCallEstimate:
        events = [
            event for event in self.events if event.component == component and event.model == model
        ][-100:]
        return _estimate_from_events(events, fallback)


class PostgresModelUsageLog:
    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    async def record(self, event: ModelCallEvent) -> None:
        try:
            pool = await self._get_pool()
            await pool.execute(
                """
                INSERT INTO model_call_log
                    (run_id, component, model, input_tokens, output_tokens,
                     cost_usd, latency_ms, gpu_seconds, subject_id, recorded_at,
                     status, error_type, release_id, artifact_digest,
                     prompt_version, schema_version)
                VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10,
                        $11, $12, $13, $14, $15, $16)
                """,
                event.run_id,
                event.component,
                event.model,
                event.input_tokens,
                event.output_tokens,
                event.cost_usd,
                event.latency_ms,
                event.gpu_seconds,
                event.subject_id,
                event.recorded_at,
                event.status,
                event.error_type,
                event.release_id,
                event.artifact_digest,
                event.prompt_version,
                event.schema_version,
            )
        except Exception:  # best-effort by design, see module docstring
            _log.exception(
                "model_usage.record_failed", component=event.component, model=event.model
            )

    async def total_cost_usd(self, run_id: str) -> float:
        """Actual accumulated spend for one run — compare against a
        compiler's static per-signal cost estimate to calibrate it over
        time (see fi_intel/agents/brief.py's per_signal_cost)."""
        pool = await self._get_pool()
        row = await pool.fetchrow(
            "SELECT COALESCE(SUM(cost_usd), 0.0) AS total FROM model_call_log WHERE run_id = $1",
            run_id,
        )
        return float(row["total"]) if row is not None else 0.0

    async def snapshot(self, run_id: str, component: str) -> ModelUsageSnapshot:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT COUNT(*) AS calls,
                   COALESCE(SUM(input_tokens), 0) AS input_tokens,
                   COALESCE(SUM(output_tokens), 0) AS output_tokens,
                   COALESCE(SUM(cost_usd), 0.0) AS cost_usd,
                   COALESCE(SUM(latency_ms), 0.0) AS latency_ms,
                   SUM(gpu_seconds) AS gpu_seconds
            FROM model_call_log
            WHERE run_id = $1 AND component = $2
            """,
            run_id,
            component,
        )
        if row is None:
            return ModelUsageSnapshot()
        return ModelUsageSnapshot(
            calls=row["calls"],
            input_tokens=row["input_tokens"],
            output_tokens=row["output_tokens"],
            cost_usd=row["cost_usd"],
            latency_ms=row["latency_ms"],
            gpu_seconds=row["gpu_seconds"],
        )

    async def estimate(
        self, component: str, model: str, fallback: ModelCallEstimate
    ) -> ModelCallEstimate:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            WITH recent AS (
                SELECT input_tokens, output_tokens, cost_usd, latency_ms, gpu_seconds
                FROM model_call_log
                WHERE component = $1 AND model = $2
                ORDER BY recorded_at DESC, call_id DESC
                LIMIT 100
            )
            SELECT COUNT(*) AS sample_size,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY input_tokens) AS input_tokens,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY output_tokens) AS output_tokens,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY cost_usd) AS cost_usd,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY latency_ms) AS latency_ms,
                   percentile_cont(0.9) WITHIN GROUP (ORDER BY gpu_seconds)
                       FILTER (WHERE gpu_seconds IS NOT NULL) AS gpu_seconds
            FROM recent
            """,
            component,
            model,
        )
        if row is None or row["sample_size"] < 5:
            return fallback
        return ModelCallEstimate(
            input_tokens=max(int(row["input_tokens"]), fallback.input_tokens),
            output_tokens=max(int(row["output_tokens"]), fallback.output_tokens),
            cost_usd=max(float(row["cost_usd"]), fallback.cost_usd),
            latency_ms=max(float(row["latency_ms"]), fallback.latency_ms),
            gpu_seconds=(
                max(float(row["gpu_seconds"]), fallback.gpu_seconds or 0.0)
                if row["gpu_seconds"] is not None
                else fallback.gpu_seconds
            ),
            sample_size=row["sample_size"],
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


def _estimate_from_events(
    events: list[ModelCallEvent], fallback: ModelCallEstimate
) -> ModelCallEstimate:
    if len(events) < 5:
        return fallback

    def percentile_90(values: list[float]) -> float:
        ordered = sorted(values)
        return ordered[min(len(ordered) - 1, int(0.9 * len(ordered)))]

    gpu_values = [event.gpu_seconds for event in events if event.gpu_seconds is not None]
    return ModelCallEstimate(
        input_tokens=max(
            int(percentile_90([float(event.input_tokens) for event in events])),
            fallback.input_tokens,
        ),
        output_tokens=max(
            int(percentile_90([float(event.output_tokens) for event in events])),
            fallback.output_tokens,
        ),
        cost_usd=max(percentile_90([event.cost_usd for event in events]), fallback.cost_usd),
        latency_ms=max(percentile_90([event.latency_ms for event in events]), fallback.latency_ms),
        gpu_seconds=(
            max(percentile_90(gpu_values), fallback.gpu_seconds or 0.0)
            if gpu_values
            else fallback.gpu_seconds
        ),
        sample_size=len(events),
    )
