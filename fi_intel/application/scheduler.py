"""Durable scheduling for the canonical daily analysis path."""

from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict

from fi_intel.application.jobs import AnalysisJob, PostgresAnalysisJobStore
from fi_intel.application.runtime_resources import RuntimeResources
from fi_intel.application.topics import PostgresTopicCatalog
from fi_intel.governance.access import RequestPrincipal
from fi_intel.graph.signals import signal_authorization_scope
from fi_intel.retrieval.entitlement import Principal, Side


class SchedulerReport(BaseModel):
    model_config = ConfigDict(frozen=True)

    active_principals: int
    authorization_scopes: int
    enqueued_jobs: int
    topic_count: int


class CanonicalScheduler:
    """Coalesce active subscriptions into one daily job per access scope."""

    def __init__(self, resources: RuntimeResources) -> None:
        self._resources = resources
        self._jobs = PostgresAnalysisJobStore(
            resources.settings.postgres_dsn,
            pool=resources.postgres_pool,
        )
        self._topics = PostgresTopicCatalog(
            resources.settings.postgres_dsn,
            pool=resources.postgres_pool,
        )

    async def run_once(self, *, now: datetime | None = None) -> SchedulerReport:
        instant = now or datetime.now(UTC)
        pool = self._resources.postgres_pool
        rows = await pool.fetch(
            """
            WITH latest_subscription AS (
              SELECT DISTINCT ON (principal_id, topic_id)
                     principal_id, topic_id, active
              FROM topic_subscription_transition_v3
              ORDER BY principal_id, topic_id, occurred_at DESC, transition_id DESC
            )
            SELECT access.subject, access.principal_id, access.entitlement_group,
                   access.barrier_side, access.desks, access.roles, access.purposes,
                   subscription.topic_id
            FROM principal_access access
            JOIN latest_subscription subscription
              ON subscription.principal_id=access.principal_id AND subscription.active
            JOIN analysis_topic_v4 topic
              ON topic.topic_id=subscription.topic_id AND topic.active
            WHERE access.active AND access.revoked_at IS NULL
              AND access.valid_from <= $1
              AND (access.valid_until IS NULL OR access.valid_until > $1)
            ORDER BY access.principal_id, subscription.topic_id
            """,
            instant,
        )
        principals: dict[str, RequestPrincipal] = {}
        topics_by_principal: dict[str, set[str]] = defaultdict(set)
        for row in rows:
            principal_id = str(row["principal_id"])
            principals[principal_id] = RequestPrincipal(
                subject=str(row["subject"]),
                principal=Principal(
                    principal_id=principal_id,
                    entitlement_group=str(row["entitlement_group"]),
                    side=Side(str(row["barrier_side"])),
                ),
                desks=frozenset(str(item) for item in row["desks"]),
                roles=frozenset(str(item) for item in row["roles"]),
                purposes=frozenset(str(item) for item in row["purposes"]),
            )
            topics_by_principal[principal_id].add(str(row["topic_id"]))

        scope_entries: dict[str, tuple[RequestPrincipal, set[str], tuple[str, ...]]] = {}
        for principal_id, principal in principals.items():
            source_rows = await pool.fetch(
                """
                SELECT grant_row.source_id FROM entitlement_grant grant_row
                JOIN source_registry source USING (source_id)
                WHERE grant_row.entitlement_group=$1 AND source.licensed
                  AND (source.barrier_side='public' OR $2='private')
                ORDER BY grant_row.source_id
                """,
                principal.principal.entitlement_group,
                principal.principal.side.value,
            )
            source_ids = tuple(str(item["source_id"]) for item in source_rows)
            scope = signal_authorization_scope(
                principal.principal.entitlement_group,
                principal.principal.side.value,
                source_ids,
            )
            if scope not in scope_entries:
                scope_entries[scope] = (principal, set(), source_ids)
            scope_entries[scope][1].update(topics_by_principal[principal_id])

        enqueued = 0
        topic_total = 0
        for scope, (principal, topic_ids, source_ids) in scope_entries.items():
            governed = await self._topics.require_many(tuple(sorted(topic_ids)))
            configured_sources = self._resources.settings.configured_coverage_source_ids
            for topic_id, topic in governed.items():
                required_sources = set(source_ids) & set(topic.required_source_ids)
                if configured_sources:
                    required_sources &= configured_sources
                job = AnalysisJob.request(
                    self._resources.settings,
                    principal,
                    frozenset({topic_id}),
                    scope,
                    tuple(sorted(required_sources)),
                    requested_at=instant,
                )
                await self._jobs.enqueue(job)
                enqueued += 1
                topic_total += 1
        return SchedulerReport(
            active_principals=len(principals),
            authorization_scopes=len(scope_entries),
            enqueued_jobs=enqueued,
            topic_count=topic_total,
        )


__all__ = ["CanonicalScheduler", "SchedulerReport"]
