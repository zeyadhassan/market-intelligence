"""Durable state and idempotency boundary for canonical daily analysis."""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol, runtime_checkable

import asyncpg
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from fi_intel.agents.investigation import InvestigationState
from fi_intel.application.runtime_resources import PostgresPoolProvider
from fi_intel.results.manifest import ResultVersion


def _digest(value: object) -> str:
    return hashlib.sha256(
        json.dumps(value, default=str, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


class AnalysisRunRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str = Field(min_length=1)
    run_type: str = "daily_analysis"
    mode: str
    principal_id: str
    authorization_scope: str
    policy_version: str
    temporal_pin: AwareDatetime
    input_manifest_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    created_at: AwareDatetime


class AnalysisScopeJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    topic_id: str
    scope_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    idempotency_key: str
    created_at: AwareDatetime

    @classmethod
    def create(
        cls, *, run_id: str, topic_id: str, scope: object, created_at: datetime
    ) -> AnalysisScopeJob:
        scope_digest = _digest(scope)
        idempotency_key = f"{run_id}:{topic_id}:{scope_digest}"
        return cls(
            job_id=_digest(idempotency_key),
            run_id=run_id,
            topic_id=topic_id,
            scope_digest=scope_digest,
            idempotency_key=idempotency_key,
            created_at=created_at,
        )


class DetectorExecutionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    execution_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    job_id: str
    pattern_name: str
    pattern_version: str
    state: str
    coverage_decision: dict[str, Any]
    input_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    output_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class ValidationDecisionRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    decision_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    investigation_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    claim_id: str = Field(min_length=1)
    validator_version: str = Field(min_length=1)
    status: str = Field(min_length=1)
    field_evidence: tuple[dict[str, Any], ...]
    reasons: tuple[str, ...] = ()
    decided_at: AwareDatetime


class AnalysisCompletion(BaseModel):
    model_config = ConfigDict(frozen=True)

    completion_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    run_id: str
    required_source_ids: tuple[str, ...]
    completed_source_ids: tuple[str, ...]
    required_job_ids: tuple[str, ...]
    completed_job_ids: tuple[str, ...]
    coverage_reasons: tuple[str, ...] = ()
    computed_at: AwareDatetime
    complete: bool

    @classmethod
    def compute(
        cls,
        *,
        run_id: str,
        required_source_ids: set[str],
        completed_source_ids: set[str],
        required_job_ids: set[str],
        completed_job_ids: set[str],
        coverage_reasons: tuple[str, ...] = (),
        computed_at: datetime | None = None,
    ) -> AnalysisCompletion:
        required_sources = tuple(sorted(required_source_ids))
        completed_sources = tuple(sorted(completed_source_ids))
        required_jobs = tuple(sorted(required_job_ids))
        completed_jobs = tuple(sorted(completed_job_ids))
        payload = {
            "run_id": run_id,
            "required_source_ids": required_sources,
            "completed_source_ids": completed_sources,
            "required_job_ids": required_jobs,
            "completed_job_ids": completed_jobs,
            "coverage_reasons": coverage_reasons,
        }
        return cls(
            completion_id=_digest(payload),
            run_id=run_id,
            required_source_ids=required_sources,
            completed_source_ids=completed_sources,
            required_job_ids=required_jobs,
            completed_job_ids=completed_jobs,
            coverage_reasons=coverage_reasons,
            computed_at=computed_at or datetime.now(UTC),
            complete=(
                bool(required_sources)
                and required_source_ids <= completed_source_ids
                and required_job_ids <= completed_job_ids
                and not coverage_reasons
            ),
        )


class DeadLetterRecord(BaseModel):
    model_config = ConfigDict(frozen=True)

    run_id: str
    stage: str
    subject_id: str
    retryable: bool
    attempt_count: int = Field(gt=0)
    safe_error_summary: str
    payload_digest: str = Field(pattern=r"^[0-9a-f]{64}$")
    quarantined_at: AwareDatetime

    @property
    def dead_letter_id(self) -> str:
        return _digest([self.run_id, self.stage, self.subject_id, self.payload_digest])


class EvaluationVerdict(StrEnum):
    USEFUL = "useful"
    NOT_RELEVANT = "not_relevant"
    INCORRECT = "incorrect"
    DUPLICATE = "duplicate"
    TOO_OLD = "too_old"


class AnalysisStateConflictError(RuntimeError):
    """An idempotency key was reused for different immutable analysis content."""


@runtime_checkable
class AnalysisStateStore(Protocol):
    async def load_run(self, run_id: str) -> AnalysisRunRecord | None: ...

    async def latest_completion(self, run_id: str) -> AnalysisCompletion | None: ...

    async def create_run(self, run: AnalysisRunRecord) -> AnalysisRunRecord: ...

    async def transition_run(
        self,
        run_id: str,
        state: InvestigationState,
        *,
        occurred_at: datetime,
        safe_error_summary: str | None = None,
    ) -> None: ...

    async def create_scope_job(self, job: AnalysisScopeJob) -> None: ...

    async def record_detector(self, execution: DetectorExecutionRecord) -> None: ...

    async def record_validation(self, decision: ValidationDecisionRecord) -> None: ...

    async def record_completion(self, completion: AnalysisCompletion) -> None: ...

    async def put_result(self, result: ResultVersion) -> None: ...

    async def record_exposure(
        self, result_version_id: str, principal_id: str, channel: str, exposed_at: datetime
    ) -> str: ...

    async def record_evaluation(
        self,
        exposure_id: str,
        verdict: EvaluationVerdict,
        rationale: str,
        evaluator_id: str,
        recorded_at: datetime,
    ) -> str: ...

    async def dead_letter(self, record: DeadLetterRecord) -> None: ...

    async def close(self) -> None: ...


class PostgresAnalysisStateStore:
    """Append-only PostgreSQL implementation for the v3 analysis migrations."""

    def __init__(
        self,
        dsn: str,
        *,
        pool: asyncpg.Pool | None = None,
        pool_provider: PostgresPoolProvider | None = None,
    ) -> None:
        self._dsn = dsn
        self._pool = pool
        self._pool_provider = pool_provider
        self._owns_pool = pool is None and pool_provider is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = (
                await self._pool_provider.get_pool()
                if self._pool_provider is not None
                else await asyncpg.create_pool(self._dsn, min_size=1, max_size=6)
            )
        return self._pool

    async def load_run(self, run_id: str) -> AnalysisRunRecord | None:
        pool = await self._get_pool()
        row = await pool.fetchrow("SELECT * FROM analysis_run_v3 WHERE run_id = $1", run_id)
        if row is None:
            return None
        return AnalysisRunRecord(
            run_id=row["run_id"],
            run_type=row["run_type"],
            mode=row["mode"],
            principal_id=row["principal_id"],
            authorization_scope=row["authorization_scope"],
            policy_version=row["policy_version"],
            temporal_pin=row["temporal_pin"],
            input_manifest_digest=row["input_manifest_digest"],
            created_at=row["created_at"],
        )

    async def latest_completion(self, run_id: str) -> AnalysisCompletion | None:
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT * FROM analysis_run_completion_v3
            WHERE run_id = $1
            ORDER BY computed_at DESC, completion_id DESC LIMIT 1
            """,
            run_id,
        )
        if row is None:
            return None
        return AnalysisCompletion(
            completion_id=row["completion_id"],
            run_id=row["run_id"],
            required_source_ids=tuple(row["required_source_ids"]),
            completed_source_ids=tuple(row["completed_source_ids"]),
            required_job_ids=tuple(row["required_job_ids"]),
            completed_job_ids=tuple(row["completed_job_ids"]),
            coverage_reasons=tuple(row["coverage_reasons"]),
            computed_at=row["computed_at"],
            complete=row["complete"],
        )

    async def create_run(self, run: AnalysisRunRecord) -> AnalysisRunRecord:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO analysis_run_v3 (
                run_id, run_type, mode, principal_id, authorization_scope,
                policy_version, temporal_pin, input_manifest_digest, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9)
            ON CONFLICT (run_id) DO NOTHING
            """,
            run.run_id,
            run.run_type,
            run.mode,
            run.principal_id,
            run.authorization_scope,
            run.policy_version,
            run.temporal_pin,
            run.input_manifest_digest,
            run.created_at,
        )
        stored = await self.load_run(run.run_id)
        if stored is None:
            raise RuntimeError("analysis run was not persisted")
        immutable_identity = (
            "run_type",
            "mode",
            "authorization_scope",
            "policy_version",
            "input_manifest_digest",
        )
        if any(getattr(stored, field) != getattr(run, field) for field in immutable_identity):
            raise AnalysisStateConflictError(
                "analysis run ID conflicts with immutable input content"
            )
        # Coalesced callers join the first transaction's temporal pin and
        # triggering principal instead of creating subtly different runs.
        return stored

    async def transition_run(
        self,
        run_id: str,
        state: InvestigationState,
        *,
        occurred_at: datetime,
        safe_error_summary: str | None = None,
    ) -> None:
        transition_id = _digest(
            [run_id, state.value, safe_error_summary or "", occurred_at.isoformat()]
        )
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO analysis_run_transition_v3 (
                transition_id, run_id, state, safe_error_summary, occurred_at
            ) VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING
            """,
            transition_id,
            run_id,
            state.value,
            safe_error_summary,
            occurred_at,
        )

    async def create_scope_job(self, job: AnalysisScopeJob) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO analysis_scope_job_v3 (
                job_id, run_id, topic_id, scope_digest, idempotency_key, created_at
            ) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
            """,
            job.job_id,
            job.run_id,
            job.topic_id,
            job.scope_digest,
            job.idempotency_key,
            job.created_at,
        )
        row = await pool.fetchrow(
            "SELECT * FROM analysis_scope_job_v3 WHERE job_id = $1", job.job_id
        )
        if row is None:
            raise RuntimeError("analysis scope job was not persisted")
        stored_identity = (
            str(row["run_id"]),
            str(row["topic_id"]),
            str(row["scope_digest"]),
            str(row["idempotency_key"]),
        )
        requested_identity = (
            job.run_id,
            job.topic_id,
            job.scope_digest,
            job.idempotency_key,
        )
        if stored_identity != requested_identity:
            raise AnalysisStateConflictError(
                "analysis scope-job ID conflicts with immutable input content"
            )

    async def record_detector(self, execution: DetectorExecutionRecord) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO detector_execution_v3 (
                execution_id, job_id, pattern_name, pattern_version, state,
                coverage_decision, input_digest, output_digest, started_at, finished_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8,$9,$10)
            ON CONFLICT DO NOTHING
            """,
            execution.execution_id,
            execution.job_id,
            execution.pattern_name,
            execution.pattern_version,
            execution.state,
            json.dumps(execution.coverage_decision, sort_keys=True),
            execution.input_digest,
            execution.output_digest,
            execution.started_at,
            execution.finished_at,
        )
        row = await pool.fetchrow(
            "SELECT * FROM detector_execution_v3 WHERE execution_id = $1",
            execution.execution_id,
        )
        if row is None:
            raise RuntimeError("detector execution was not persisted")
        coverage = row["coverage_decision"]
        if isinstance(coverage, str):
            coverage = json.loads(coverage)
        stored_identity = (
            str(row["job_id"]),
            str(row["pattern_name"]),
            str(row["pattern_version"]),
            str(row["state"]),
            coverage,
            str(row["input_digest"]),
            str(row["output_digest"]),
        )
        requested_identity = (
            execution.job_id,
            execution.pattern_name,
            execution.pattern_version,
            execution.state,
            execution.coverage_decision,
            execution.input_digest,
            execution.output_digest,
        )
        if stored_identity != requested_identity:
            raise AnalysisStateConflictError(
                "detector execution ID conflicts with immutable input/output content"
            )

    async def record_validation(self, decision: ValidationDecisionRecord) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO validation_decision_v3 (
                decision_id, investigation_id, claim_id, validator_version,
                status, field_evidence, reasons, decided_at
            ) VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7,$8)
            ON CONFLICT (decision_id) DO NOTHING
            """,
            decision.decision_id,
            decision.investigation_id,
            decision.claim_id,
            decision.validator_version,
            decision.status,
            json.dumps(decision.field_evidence, sort_keys=True),
            list(decision.reasons),
            decision.decided_at,
        )
        row = await pool.fetchrow(
            "SELECT * FROM validation_decision_v3 WHERE decision_id = $1",
            decision.decision_id,
        )
        if row is None:
            raise RuntimeError("validation decision was not persisted")
        field_evidence = row["field_evidence"]
        if isinstance(field_evidence, str):
            field_evidence = json.loads(field_evidence)
        stored_identity = (
            str(row["investigation_id"]),
            str(row["claim_id"]),
            str(row["validator_version"]),
            str(row["status"]),
            tuple(field_evidence),
            tuple(row["reasons"]),
        )
        requested_identity = (
            decision.investigation_id,
            decision.claim_id,
            decision.validator_version,
            decision.status,
            decision.field_evidence,
            decision.reasons,
        )
        if stored_identity != requested_identity:
            raise AnalysisStateConflictError(
                "validation-decision ID conflicts with immutable decision content"
            )

    async def record_completion(self, completion: AnalysisCompletion) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO analysis_run_completion_v3 (
                completion_id, run_id, required_source_ids, completed_source_ids,
                required_job_ids, completed_job_ids, coverage_reasons, computed_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8) ON CONFLICT DO NOTHING
            """,
            completion.completion_id,
            completion.run_id,
            list(completion.required_source_ids),
            list(completion.completed_source_ids),
            list(completion.required_job_ids),
            list(completion.completed_job_ids),
            list(completion.coverage_reasons),
            completion.computed_at,
        )

    async def put_result(self, result: ResultVersion) -> None:
        manifest = result.manifest
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO result_version_v3 (
                result_version_id, logical_result_id, investigation_id, output_hash,
                manifest, publication_state, created_at
            ) VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7) ON CONFLICT DO NOTHING
            """,
            result.result_version_id,
            result.logical_result_id,
            manifest.investigation.investigation_id,
            result.output_hash,
            manifest.canonical_payload(),
            manifest.decision.value,
            manifest.investigation.updated_at,
        )

    async def record_exposure(
        self, result_version_id: str, principal_id: str, channel: str, exposed_at: datetime
    ) -> str:
        exposure_id = _digest([result_version_id, principal_id, channel])
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO result_exposure_v3 (
                exposure_id, result_version_id, principal_id, channel, exposed_at
            ) VALUES ($1,$2,$3,$4,$5) ON CONFLICT DO NOTHING
            """,
            exposure_id,
            result_version_id,
            principal_id,
            channel,
            exposed_at,
        )
        return exposure_id

    async def record_evaluation(
        self,
        exposure_id: str,
        verdict: EvaluationVerdict,
        rationale: str,
        evaluator_id: str,
        recorded_at: datetime,
    ) -> str:
        evaluation_id = _digest(
            [exposure_id, verdict.value, rationale, evaluator_id, recorded_at.isoformat()]
        )
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO result_evaluation_v3 (
                evaluation_id, exposure_id, verdict, rationale, evaluator_id, recorded_at
            ) VALUES ($1,$2,$3,$4,$5,$6) ON CONFLICT DO NOTHING
            """,
            evaluation_id,
            exposure_id,
            verdict.value,
            rationale,
            evaluator_id,
            recorded_at,
        )
        return evaluation_id

    async def dead_letter(self, record: DeadLetterRecord) -> None:
        pool = await self._get_pool()
        await pool.execute(
            """
            INSERT INTO analysis_dead_letter_v3 (
                dead_letter_id, run_id, stage, subject_id, retryable,
                attempt_count, safe_error_summary, payload_digest, quarantined_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9) ON CONFLICT DO NOTHING
            """,
            record.dead_letter_id,
            record.run_id,
            record.stage,
            record.subject_id,
            record.retryable,
            record.attempt_count,
            record.safe_error_summary[:500],
            record.payload_digest,
            record.quarantined_at,
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


__all__ = [
    "AnalysisCompletion",
    "AnalysisRunRecord",
    "AnalysisScopeJob",
    "AnalysisStateConflictError",
    "AnalysisStateStore",
    "DeadLetterRecord",
    "DetectorExecutionRecord",
    "EvaluationVerdict",
    "PostgresAnalysisStateStore",
    "ValidationDecisionRecord",
]
