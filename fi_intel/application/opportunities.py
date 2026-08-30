"""PostgreSQL logical opportunity lifecycle and daily read projection."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

import asyncpg
from pydantic import BaseModel, ConfigDict, Field

from fi_intel.application.jobs import AnalysisJob, stable_digest
from fi_intel.results.manifest import ImmutableResultManifest, ResultVersion


class OpportunityLifecycleState(StrEnum):
    NEW = "new"
    UPDATED = "updated"
    UNCHANGED = "unchanged"
    WEAKENED = "weakened"
    CONTRADICTED = "contradicted"
    RESOLVED = "resolved"
    SUPPRESSED = "suppressed"
    HELD = "held"


class OpportunityDecision(BaseModel):
    model_config = ConfigDict(frozen=True)

    logical_opportunity_id: str = Field(pattern=r"^[0-9a-f]{64}$")
    state: OpportunityLifecycleState
    result_version_id: str
    write_result_version: bool
    material_change: dict[str, Any]


def material_result_fingerprint(manifest: ImmutableResultManifest) -> str:
    """Hash analyst-visible material content while excluding run volatility."""

    payload = {
        "topic_id": manifest.topic_id,
        "authorization_scope": manifest.authorization_scope,
        "temporal_policy_version": manifest.temporal_policy_version,
        "entity_id": manifest.entity_id,
        "signal_id": manifest.signal_id,
        "assertion_ids": sorted(manifest.assertion_ids),
        "source_versions": sorted(
            (item.source_id, item.document_version_id, item.content_hash)
            for item in manifest.source_versions
        ),
        "opportunity": manifest.opportunity.model_dump(mode="json"),
        "evidence": sorted(
            (
                item.evidence_id,
                item.content_hash,
                item.excerpt,
                item.lexical_score,
                item.vector_score,
                item.reranker_score,
                item.fallback_tier,
            )
            for item in manifest.evidence
        ),
        "triage_score": manifest.triage_score,
        "validation_results": manifest.validation_results,
        "policy_digest": manifest.policy_digest,
    }
    return stable_digest(payload)


class PostgresOpportunityRepository:
    def __init__(self, dsn: str, *, pool: asyncpg.Pool | None = None) -> None:
        self._dsn = dsn
        self._pool = pool
        self._owns_pool = pool is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = await asyncpg.create_pool(self._dsn, min_size=1, max_size=4)
        return self._pool

    @staticmethod
    def identity(manifest: ImmutableResultManifest) -> str:
        return stable_digest(
            [
                manifest.topic_id,
                manifest.entity_id,
                manifest.signal_id,
                manifest.authorization_scope,
                "opportunity-lifecycle-v1",
            ]
        )

    async def classify(self, result: ResultVersion) -> OpportunityDecision:
        manifest = result.manifest
        logical_id = self.identity(manifest)
        current_fingerprint = material_result_fingerprint(manifest)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            """
            SELECT transition.state, transition.result_version_id,
                   transition.material_change, previous.manifest
            FROM opportunity_transition_v4 transition
            LEFT JOIN result_version_v3 previous
              ON previous.result_version_id = transition.result_version_id
            WHERE transition.logical_opportunity_id = $1
            ORDER BY transition.occurred_at DESC, transition.transition_id DESC LIMIT 1
            """,
            logical_id,
        )
        if row is None or row["result_version_id"] is None or row["manifest"] is None:
            return OpportunityDecision(
                logical_opportunity_id=logical_id,
                state=OpportunityLifecycleState.NEW,
                result_version_id=result.result_version_id,
                write_result_version=True,
                material_change={
                    "previous_fingerprint": None,
                    "current_fingerprint": current_fingerprint,
                    "reason": "first governed observation",
                },
            )
        previous_manifest = ImmutableResultManifest.model_validate(_json(row["manifest"]))
        previous_fingerprint = material_result_fingerprint(previous_manifest)
        previous_result_id = str(row["result_version_id"])
        if previous_fingerprint == current_fingerprint:
            return OpportunityDecision(
                logical_opportunity_id=logical_id,
                state=OpportunityLifecycleState.UNCHANGED,
                result_version_id=previous_result_id,
                write_result_version=False,
                material_change={
                    "previous_fingerprint": previous_fingerprint,
                    "current_fingerprint": current_fingerprint,
                    "reason": "material output is unchanged",
                },
            )
        has_contradiction = any(
            claim.claim_type.value == "contradiction" for claim in manifest.opportunity.claims
        )
        old_score = previous_manifest.triage_score or 0.0
        new_score = manifest.triage_score or 0.0
        if has_contradiction:
            state = OpportunityLifecycleState.CONTRADICTED
            reason = "material contradictory evidence entered the admitted result"
        elif new_score < old_score:
            state = OpportunityLifecycleState.WEAKENED
            reason = "material score decreased"
        else:
            state = OpportunityLifecycleState.UPDATED
            reason = "evidence or governed interpretation changed"
        return OpportunityDecision(
            logical_opportunity_id=logical_id,
            state=state,
            result_version_id=result.result_version_id,
            write_result_version=True,
            material_change={
                "previous_fingerprint": previous_fingerprint,
                "current_fingerprint": current_fingerprint,
                "previous_score": old_score,
                "current_score": new_score,
                "reason": reason,
            },
        )

    async def record(
        self,
        result: ResultVersion,
        decision: OpportunityDecision,
        *,
        occurred_at: datetime,
    ) -> None:
        manifest = result.manifest
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            await connection.execute(
                """
                INSERT INTO logical_opportunity_v4 (
                    logical_opportunity_id, topic_id, entity_id, subject_key,
                    authorization_scope, lifecycle_policy_version, created_at
                ) VALUES ($1,$2,$3,$4,$5,$6,$7) ON CONFLICT DO NOTHING
                """,
                decision.logical_opportunity_id,
                manifest.topic_id,
                manifest.entity_id,
                manifest.signal_id,
                manifest.authorization_scope,
                "opportunity-lifecycle-v1",
                occurred_at,
            )
            transition_id = stable_digest(
                [
                    decision.logical_opportunity_id,
                    decision.state.value,
                    decision.result_version_id,
                    decision.material_change,
                ]
            )
            await connection.execute(
                """
                INSERT INTO opportunity_transition_v4 (
                    transition_id, logical_opportunity_id, result_version_id,
                    state, material_change, condition_outcome,
                    commercial_outcome, occurred_at
                ) VALUES ($1,$2,$3,$4,$5::jsonb,'active','unknown_outcome',$6)
                ON CONFLICT DO NOTHING
                """,
                transition_id,
                decision.logical_opportunity_id,
                decision.result_version_id,
                decision.state.value,
                json.dumps(decision.material_change, sort_keys=True),
                occurred_at,
            )

    async def resolve_missing(
        self,
        topic_id: str,
        authorization_scope: str,
        observed_ids: set[str],
        *,
        occurred_at: datetime,
    ) -> int:
        """Resolve prior active conditions only after a complete daily run."""

        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (logical.logical_opportunity_id)
                   logical.logical_opportunity_id, transition.result_version_id,
                   transition.state
            FROM logical_opportunity_v4 logical
            JOIN opportunity_transition_v4 transition USING (logical_opportunity_id)
            WHERE logical.topic_id=$1 AND logical.authorization_scope=$2
            ORDER BY logical.logical_opportunity_id, transition.occurred_at DESC,
                     transition.transition_id DESC
            """,
            topic_id,
            authorization_scope,
        )
        count = 0
        for row in rows:
            logical_id = str(row["logical_opportunity_id"])
            if logical_id in observed_ids or str(row["state"]) in {
                OpportunityLifecycleState.RESOLVED.value,
                OpportunityLifecycleState.SUPPRESSED.value,
            }:
                continue
            material_change = {
                "reason": "detector condition no longer present under complete coverage",
                "commercial_outcome": "unknown_outcome",
            }
            transition_id = stable_digest(
                [logical_id, "resolved", row["result_version_id"], material_change]
            )
            await pool.execute(
                """
                INSERT INTO opportunity_transition_v4 (
                    transition_id, logical_opportunity_id, result_version_id,
                    state, material_change, condition_outcome,
                    commercial_outcome, occurred_at
                ) VALUES ($1,$2,$3,'resolved',$4::jsonb,
                          'condition_resolved','unknown_outcome',$5)
                ON CONFLICT DO NOTHING
                """,
                transition_id,
                logical_id,
                row["result_version_id"],
                json.dumps(material_change, sort_keys=True),
                occurred_at,
            )
            count += 1
        return count

    async def materialize_topic(
        self,
        job: AnalysisJob,
        topic_id: str,
        *,
        run_id: str | None,
        coverage: dict[str, Any],
        latest_source_time: datetime | None,
        safe_message: str,
    ) -> None:
        pool = await self._get_pool()
        rows = await pool.fetch(
            """
            SELECT DISTINCT ON (logical.logical_opportunity_id)
                   transition.state, transition.result_version_id,
                   transition.occurred_at
            FROM logical_opportunity_v4 logical
            JOIN opportunity_transition_v4 transition USING (logical_opportunity_id)
            WHERE logical.topic_id=$1 AND logical.authorization_scope=$2
              AND transition.result_version_id IS NOT NULL
              AND transition.state <> 'suppressed'
            ORDER BY logical.logical_opportunity_id,
                     transition.occurred_at DESC, transition.transition_id DESC
            """,
            topic_id,
            job.authorization_scope,
        )
        ordered = sorted(
            rows,
            key=lambda row: (
                _lifecycle_order(str(row["state"])),
                row["occurred_at"],
                str(row["result_version_id"]),
            ),
            reverse=True,
        )
        result_ids = [str(row["result_version_id"]) for row in ordered]
        lifecycle = {str(row["result_version_id"]): str(row["state"]) for row in ordered}
        counts: dict[str, int] = {}
        for state in lifecycle.values():
            counts[state] = counts.get(state, 0) + 1
        await pool.execute(
            """
            INSERT INTO daily_topic_read_model_v4 (
                topic_id, business_date, authorization_scope, analysis_job_id,
                run_id, state, coverage_summary, latest_source_time,
                lifecycle_counts, ordered_result_version_ids, result_lifecycle,
                safe_message, materialized_at
            ) VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8,$9::jsonb,$10,$11::jsonb,$12,$13)
            ON CONFLICT (topic_id, business_date, authorization_scope) DO UPDATE SET
                analysis_job_id=EXCLUDED.analysis_job_id,
                run_id=EXCLUDED.run_id,
                state=EXCLUDED.state,
                coverage_summary=EXCLUDED.coverage_summary,
                latest_source_time=EXCLUDED.latest_source_time,
                lifecycle_counts=EXCLUDED.lifecycle_counts,
                ordered_result_version_ids=EXCLUDED.ordered_result_version_ids,
                result_lifecycle=EXCLUDED.result_lifecycle,
                safe_message=EXCLUDED.safe_message,
                materialized_at=EXCLUDED.materialized_at
            """,
            topic_id,
            job.business_date,
            job.authorization_scope,
            job.job_id,
            run_id,
            job.state.value,
            json.dumps(coverage, sort_keys=True),
            latest_source_time,
            json.dumps(counts, sort_keys=True),
            result_ids,
            json.dumps(lifecycle, sort_keys=True),
            safe_message,
            datetime.now(UTC),
        )

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None


def _json(value: object) -> Any:
    return json.loads(value) if isinstance(value, str) else value


def _lifecycle_order(state: str) -> int:
    order = {
        "new": 8,
        "updated": 7,
        "weakened": 6,
        "contradicted": 5,
        "resolved": 4,
        "unchanged": 3,
        "held": 2,
        "suppressed": 1,
    }
    return order.get(state, 0)


__all__ = [
    "OpportunityDecision",
    "OpportunityLifecycleState",
    "PostgresOpportunityRepository",
    "material_result_fingerprint",
]
