"""Optional live-PostgreSQL proof of exclusive claims and expired-lease recovery."""

from __future__ import annotations

import asyncio
import os
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import asyncpg
import pytest

from fi_intel.application.jobs import AnalysisJob, AnalysisJobState, PostgresAnalysisJobStore
from fi_intel.config import Settings
from fi_intel.db.migrations import PostgresMigrationRunner
from fi_intel.governance.access import RequestPrincipal
from fi_intel.retrieval.entitlement import Principal, Side

PG_DSN = os.environ.get("FI_INTEL_TEST_PG_DSN")


@pytest.mark.skipif(PG_DSN is None, reason="FI_INTEL_TEST_PG_DSN not set")
async def test_concurrent_claim_is_exclusive_and_an_expired_lease_is_recovered() -> None:
    assert PG_DSN is not None
    await PostgresMigrationRunner(PG_DSN).apply()
    pool = await asyncpg.create_pool(PG_DSN, min_size=2, max_size=4)
    first_store = PostgresAnalysisJobStore(PG_DSN, pool=pool)
    second_store = PostgresAnalysisJobStore(PG_DSN, pool=pool)
    identity = uuid4().hex
    requested_at = datetime.now(UTC)
    job = AnalysisJob.request(
        Settings(coverage_required_source_ids="source-a"),
        RequestPrincipal(
            subject=f"integration-{identity}",
            principal=Principal(
                principal_id=f"integration-{identity}",
                entitlement_group="fi_gcc_public",
                side=Side.PUBLIC,
            ),
            desks=frozenset({"gcc-fi"}),
            roles=frozenset({"analyst"}),
            purposes=frozenset({"market-intelligence"}),
        ),
        frozenset({f"topic-{identity}"}),
        f"scope-{identity}",
        ("source-a",),
        requested_at=requested_at,
    )

    try:
        await first_store.enqueue(job)
        claims = await asyncio.gather(
            first_store.claim("worker-a", 120),
            second_store.claim("worker-b", 120),
        )
        claimed = [item for item in claims if item is not None]
        assert len(claimed) == 1
        assert claimed[0].job_id == job.job_id
        assert claimed[0].attempt_count == 1

        await pool.execute(
            "UPDATE analysis_job_v4 SET lease_expires_at=$2 WHERE job_id=$1",
            job.job_id,
            requested_at - timedelta(seconds=1),
        )
        recovered = await second_store.claim("worker-recovery", 120)
        assert recovered is not None
        assert recovered.job_id == job.job_id
        assert recovered.attempt_count == 2
        assert recovered.lease_owner == "worker-recovery"

        completed = await second_store.finish(
            job.job_id,
            "worker-recovery",
            AnalysisJobState.COMPLETE,
            run_id=f"run-{identity}",
        )
        assert completed.state is AnalysisJobState.COMPLETE
    finally:
        await pool.close()
