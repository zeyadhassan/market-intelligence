"""Policy-scoped Postgres implementation of the analyst application port.

Every sensitive read and write joins the immutable ``access_policy`` carried
by its ledger subject. Unauthorized and missing IDs are deliberately
indistinguishable at this boundary.
"""

import json
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import asyncpg

from fi_intel.api.auth import RequestPrincipal
from fi_intel.api.models import (
    BriefPublicationRequest,
    BriefRequest,
    BriefView,
    EntityAssertionView,
    EntityView,
    EvidenceSpanView,
    FeedbackReceipt,
    FeedbackRequest,
    ReviewDecisionRequest,
    ReviewReceipt,
    RunView,
    SignalCloseReceipt,
    SignalCloseRequest,
    SignalView,
)
from fi_intel.api.service import PublicationNotReadyError, ResourceNotFoundError
from fi_intel.application.runtime_resources import PostgresPoolProvider
from fi_intel.ledger.models import outbox_event_id

LIST_SIGNALS_SQL = """
SELECT s.signal_id, s.pattern_id, s.pattern_version,
       s.subject_entity_id AS entity_id, entity.canonical_name AS entity_name,
       assignment.desk, transition.to_status AS status, transition.score,
       transition.as_of, transition.occurred_at AS changed_at,
       coalesce(assertions.assertion_ids, '{}'::uuid[]) AS assertion_ids,
       coalesce(evidence.evidence_span_ids, '{}'::uuid[]) AS evidence_span_ids,
       feedback.verdict AS latest_feedback,
       CASE WHEN transition.to_status IN ('suppressed', 'expired', 'withdrawn')
            THEN transition.occurred_at END AS closed_at
FROM intelligence_signal s
JOIN analyst_signal_desk assignment ON assignment.signal_id = s.signal_id
JOIN entity_identity entity ON entity.entity_id = s.subject_entity_id
JOIN access_policy signal_policy ON signal_policy.policy_id = s.policy_id
JOIN access_policy assignment_policy ON assignment_policy.policy_id = assignment.policy_id
JOIN access_policy entity_policy ON entity_policy.policy_id = entity.policy_id
JOIN LATERAL (
    SELECT t.transition_id, t.to_status, t.score, t.as_of, t.occurred_at
    FROM signal_transition t
    WHERE t.signal_id = s.signal_id
    ORDER BY t.occurred_at DESC, t.transition_id DESC
    LIMIT 1
) transition ON TRUE
LEFT JOIN LATERAL (
    SELECT array_agg(link.assertion_id ORDER BY link.assertion_id) AS assertion_ids
    FROM signal_transition_assertion link
    WHERE link.transition_id = transition.transition_id
) assertions ON TRUE
LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT link.evidence_span_id ORDER BY link.evidence_span_id)
           AS evidence_span_ids
    FROM signal_transition_assertion transition_link
    JOIN knowledge_assertion_evidence link
      ON link.assertion_id = transition_link.assertion_id
    JOIN evidence_span span ON span.evidence_span_id = link.evidence_span_id
    JOIN access_policy evidence_policy ON evidence_policy.policy_id = span.policy_id
    WHERE transition_link.transition_id = transition.transition_id
      AND $1::text = ANY(evidence_policy.allowed_entitlement_groups)
      AND (evidence_policy.barrier_side = 'public' OR $2::text = 'private')
) evidence ON TRUE
LEFT JOIN LATERAL (
    SELECT item.verdict
    FROM analyst_signal_feedback item
    JOIN access_policy feedback_policy ON feedback_policy.policy_id = item.policy_id
    WHERE item.signal_id = s.signal_id
      AND $1::text = ANY(feedback_policy.allowed_entitlement_groups)
      AND (feedback_policy.barrier_side = 'public' OR $2::text = 'private')
    ORDER BY item.recorded_at DESC, item.feedback_id DESC
    LIMIT 1
) feedback ON TRUE
WHERE assignment.desk = $3::text
  AND assignment.active
  AND $1::text = ANY(signal_policy.allowed_entitlement_groups)
  AND (signal_policy.barrier_side = 'public' OR $2::text = 'private')
  AND $1::text = ANY(assignment_policy.allowed_entitlement_groups)
  AND (assignment_policy.barrier_side = 'public' OR $2::text = 'private')
  AND $1::text = ANY(entity_policy.allowed_entitlement_groups)
  AND (entity_policy.barrier_side = 'public' OR $2::text = 'private')
  AND ($4::text IS NULL OR transition.to_status = $4::text)
ORDER BY transition.occurred_at DESC, s.signal_id
LIMIT $5::int
"""


GET_SIGNAL_SQL = """
SELECT s.signal_id, s.pattern_id, s.pattern_version,
       s.subject_entity_id AS entity_id, entity.canonical_name AS entity_name,
       assignment.desk, transition.to_status AS status, transition.score,
       transition.as_of, transition.occurred_at AS changed_at,
       coalesce(assertions.assertion_ids, '{}'::uuid[]) AS assertion_ids,
       coalesce(evidence.evidence_span_ids, '{}'::uuid[]) AS evidence_span_ids,
       feedback.verdict AS latest_feedback,
       CASE WHEN transition.to_status IN ('suppressed', 'expired', 'withdrawn')
            THEN transition.occurred_at END AS closed_at
FROM intelligence_signal s
JOIN analyst_signal_desk assignment ON assignment.signal_id = s.signal_id
JOIN entity_identity entity ON entity.entity_id = s.subject_entity_id
JOIN access_policy signal_policy ON signal_policy.policy_id = s.policy_id
JOIN access_policy assignment_policy ON assignment_policy.policy_id = assignment.policy_id
JOIN access_policy entity_policy ON entity_policy.policy_id = entity.policy_id
JOIN LATERAL (
    SELECT t.transition_id, t.to_status, t.score, t.as_of, t.occurred_at
    FROM signal_transition t
    WHERE t.signal_id = s.signal_id
    ORDER BY t.occurred_at DESC, t.transition_id DESC
    LIMIT 1
) transition ON TRUE
LEFT JOIN LATERAL (
    SELECT array_agg(link.assertion_id ORDER BY link.assertion_id) AS assertion_ids
    FROM signal_transition_assertion link
    WHERE link.transition_id = transition.transition_id
) assertions ON TRUE
LEFT JOIN LATERAL (
    SELECT array_agg(DISTINCT link.evidence_span_id ORDER BY link.evidence_span_id)
           AS evidence_span_ids
    FROM signal_transition_assertion transition_link
    JOIN knowledge_assertion_evidence link
      ON link.assertion_id = transition_link.assertion_id
    JOIN evidence_span span ON span.evidence_span_id = link.evidence_span_id
    JOIN access_policy evidence_policy ON evidence_policy.policy_id = span.policy_id
    WHERE transition_link.transition_id = transition.transition_id
      AND $2::text = ANY(evidence_policy.allowed_entitlement_groups)
      AND (evidence_policy.barrier_side = 'public' OR $3::text = 'private')
) evidence ON TRUE
LEFT JOIN LATERAL (
    SELECT item.verdict
    FROM analyst_signal_feedback item
    JOIN access_policy feedback_policy ON feedback_policy.policy_id = item.policy_id
    WHERE item.signal_id = s.signal_id
      AND $2::text = ANY(feedback_policy.allowed_entitlement_groups)
      AND (feedback_policy.barrier_side = 'public' OR $3::text = 'private')
    ORDER BY item.recorded_at DESC, item.feedback_id DESC
    LIMIT 1
) feedback ON TRUE
WHERE s.signal_id = $1::uuid
  AND assignment.active
  AND assignment.desk = ANY($4::text[])
  AND $2::text = ANY(signal_policy.allowed_entitlement_groups)
  AND (signal_policy.barrier_side = 'public' OR $3::text = 'private')
  AND $2::text = ANY(assignment_policy.allowed_entitlement_groups)
  AND (assignment_policy.barrier_side = 'public' OR $3::text = 'private')
  AND $2::text = ANY(entity_policy.allowed_entitlement_groups)
  AND (entity_policy.barrier_side = 'public' OR $3::text = 'private')
ORDER BY assignment.desk
LIMIT 1
"""


GET_ENTITY_SQL = """
SELECT entity.entity_id, entity.entity_type, entity.canonical_name,
       coalesce(identifiers.values, '{}'::jsonb) AS identifiers
FROM entity_identity entity
JOIN access_policy entity_policy ON entity_policy.policy_id = entity.policy_id
LEFT JOIN LATERAL (
    SELECT jsonb_object_agg(identifier.scheme, identifier.value) AS values
    FROM analyst_entity_identifier identifier
    JOIN access_policy identifier_policy
      ON identifier_policy.policy_id = identifier.policy_id
    WHERE identifier.entity_id = entity.entity_id
      AND identifier.active
      AND $2::text = ANY(identifier_policy.allowed_entitlement_groups)
      AND (identifier_policy.barrier_side = 'public' OR $3::text = 'private')
) identifiers ON TRUE
WHERE entity.entity_id = $1::uuid
  AND $2::text = ANY(entity_policy.allowed_entitlement_groups)
  AND (entity_policy.barrier_side = 'public' OR $3::text = 'private')
"""


GET_ENTITY_TIMELINE_SQL = """
SELECT assertion.assertion_id, assertion.predicate, assertion.object_json,
       assertion.qualifiers, assertion.event_time, assertion.valid_from,
       assertion.valid_to, assertion.recorded_at, assertion.confidence,
       coalesce(evidence.ids, '{}'::uuid[]) AS evidence_span_ids
FROM knowledge_assertion assertion
JOIN access_policy assertion_policy ON assertion_policy.policy_id = assertion.policy_id
LEFT JOIN LATERAL (
    SELECT array_agg(link.evidence_span_id ORDER BY link.evidence_span_id) AS ids
    FROM knowledge_assertion_evidence link
    JOIN evidence_span span ON span.evidence_span_id = link.evidence_span_id
    JOIN access_policy evidence_policy ON evidence_policy.policy_id = span.policy_id
    WHERE link.assertion_id = assertion.assertion_id
      AND $2::text = ANY(evidence_policy.allowed_entitlement_groups)
      AND (evidence_policy.barrier_side = 'public' OR $3::text = 'private')
) evidence ON TRUE
WHERE assertion.subject_entity_id = $1::uuid
  AND $2::text = ANY(assertion_policy.allowed_entitlement_groups)
  AND (assertion_policy.barrier_side = 'public' OR $3::text = 'private')
ORDER BY coalesce(assertion.event_time, assertion.valid_from) DESC,
         assertion.recorded_at DESC, assertion.assertion_id
"""


GET_EVIDENCE_SQL = """
SELECT span.evidence_span_id, span.document_version_id, version.title,
       span.quote, span.char_start, span.char_end,
       coalesce(asset.metadata ->> 'source_url', asset.object_uri) AS source_url,
       identity.source_id, version.published_at
FROM evidence_span span
JOIN document_version version
  ON version.document_version_id = span.document_version_id
JOIN document_identity identity ON identity.document_id = version.document_id
JOIN raw_asset asset ON asset.raw_asset_id = version.raw_asset_id
JOIN source_registry source ON source.source_id = identity.source_id
JOIN entitlement_grant grant_row
  ON grant_row.source_id = identity.source_id
 AND grant_row.entitlement_group = $2::text
JOIN access_policy span_policy ON span_policy.policy_id = span.policy_id
JOIN access_policy version_policy ON version_policy.policy_id = version.policy_id
JOIN access_policy asset_policy ON asset_policy.policy_id = asset.policy_id
WHERE span.evidence_span_id = $1::uuid
  AND source.licensed
  AND $2::text = ANY(span_policy.allowed_entitlement_groups)
  AND (span_policy.barrier_side = 'public' OR $3::text = 'private')
  AND $2::text = ANY(version_policy.allowed_entitlement_groups)
  AND (version_policy.barrier_side = 'public' OR $3::text = 'private')
  AND $2::text = ANY(asset_policy.allowed_entitlement_groups)
  AND (asset_policy.barrier_side = 'public' OR $3::text = 'private')
"""


INSERT_FEEDBACK_SQL = """
WITH authorized AS (
    SELECT signal.signal_id, signal.policy_id
    FROM intelligence_signal signal
    JOIN analyst_signal_desk assignment ON assignment.signal_id = signal.signal_id
    JOIN access_policy signal_policy ON signal_policy.policy_id = signal.policy_id
    JOIN access_policy assignment_policy
      ON assignment_policy.policy_id = assignment.policy_id
    WHERE signal.signal_id = $2::uuid
      AND assignment.active
      AND assignment.desk = ANY($8::text[])
      AND $6::text = ANY(signal_policy.allowed_entitlement_groups)
      AND (signal_policy.barrier_side = 'public' OR $7::text = 'private')
      AND $6::text = ANY(assignment_policy.allowed_entitlement_groups)
      AND (assignment_policy.barrier_side = 'public' OR $7::text = 'private')
    LIMIT 1
)
INSERT INTO analyst_signal_feedback (
    feedback_id, signal_id, verdict, reason, principal_id, recorded_at, policy_id
)
SELECT $1::uuid, authorized.signal_id, $3::text, $4::text, $5::text,
       $9::timestamptz, authorized.policy_id
FROM authorized
RETURNING feedback_id, signal_id, recorded_at
"""


CLOSE_SIGNAL_SELECT_SQL = """
SELECT signal.signal_id, signal.policy_id, transition.transition_id,
       transition.to_status, transition.occurred_at,
       (SELECT count(*)::int + 1 FROM signal_transition history
        WHERE history.signal_id = signal.signal_id) AS next_aggregate_version
FROM intelligence_signal signal
JOIN analyst_signal_desk assignment ON assignment.signal_id = signal.signal_id
JOIN access_policy signal_policy ON signal_policy.policy_id = signal.policy_id
JOIN access_policy assignment_policy ON assignment_policy.policy_id = assignment.policy_id
JOIN LATERAL (
    SELECT item.transition_id, item.to_status, item.occurred_at
    FROM signal_transition item
    WHERE item.signal_id = signal.signal_id
    ORDER BY item.occurred_at DESC, item.transition_id DESC
    LIMIT 1
) transition ON TRUE
WHERE signal.signal_id = $1::uuid
  AND assignment.active
  AND assignment.desk = ANY($4::text[])
  AND $2::text = ANY(signal_policy.allowed_entitlement_groups)
  AND (signal_policy.barrier_side = 'public' OR $3::text = 'private')
  AND $2::text = ANY(assignment_policy.allowed_entitlement_groups)
  AND (assignment_policy.barrier_side = 'public' OR $3::text = 'private')
LIMIT 1
FOR UPDATE OF signal
"""


INSERT_SIGNAL_CLOSE_SQL = """
INSERT INTO signal_transition (
    transition_id, signal_id, from_status, to_status, occurred_at, as_of,
    score, reason, actor, policy_id
)
VALUES ($1::uuid, $2::uuid, $3::text, $4::text, $5::timestamptz,
        $5::timestamptz, NULL, $6::text, $7::text, $8::uuid)
"""


INSERT_SIGNAL_CLOSE_OUTBOX_SQL = """
INSERT INTO transactional_outbox (
    event_id, event_type, aggregate_type, aggregate_id, aggregate_version,
    occurred_at, correlation_id, causation_id, policy_id, payload
)
VALUES ($1::uuid, 'signal.transitioned.v1', 'signal', $2::uuid, $3::int,
        $4::timestamptz, $5::uuid, NULL, $6::uuid, $7::jsonb)
"""


INSERT_REVIEW_SQL = """
WITH subject AS (
    SELECT signal.signal_id AS subject_id, signal.policy_id
    FROM intelligence_signal signal
    WHERE $2::text = 'signal' AND signal.signal_id = $3::uuid
      AND EXISTS (
          SELECT 1 FROM analyst_signal_desk assignment
          JOIN access_policy assignment_policy
            ON assignment_policy.policy_id = assignment.policy_id
          WHERE assignment.signal_id = signal.signal_id
            AND assignment.active AND assignment.desk = ANY($10::text[])
            AND $8::text = ANY(assignment_policy.allowed_entitlement_groups)
            AND (assignment_policy.barrier_side = 'public' OR $9::text = 'private')
      )
    UNION ALL
    SELECT candidate.candidate_id, candidate.policy_id
    FROM claim_candidate candidate
    WHERE $2::text = 'claim_candidate' AND candidate.candidate_id = $3::uuid
    UNION ALL
    SELECT link.decision_id, link.policy_id
    FROM entity_link_decision link
    WHERE $2::text = 'entity_link' AND link.decision_id = $3::uuid
), authorized AS (
    SELECT subject.subject_id, subject.policy_id
    FROM subject
    JOIN access_policy policy ON policy.policy_id = subject.policy_id
    WHERE $8::text = ANY(policy.allowed_entitlement_groups)
      AND (policy.barrier_side = 'public' OR $9::text = 'private')
)
INSERT INTO analyst_review_decision (
    review_id, subject_type, subject_id, decision, reason,
    decided_by, decided_at, policy_id
)
SELECT $1::uuid, $2::text, authorized.subject_id, $4::text, $5::text,
       $6::text, $7::timestamptz, authorized.policy_id
FROM authorized
RETURNING review_id, subject_type, subject_id, decided_at
"""


INSERT_BRIEF_REQUEST_SQL = """
WITH authorized_desk AS (
    SELECT desk.desk, desk.policy_id
    FROM analyst_desk desk
    JOIN access_policy policy ON policy.policy_id = desk.policy_id
    WHERE desk.desk = $3::text AND desk.active
      AND $6::text = ANY(policy.allowed_entitlement_groups)
      AND (policy.barrier_side = 'public' OR $7::text = 'private')
), run_insert AS (
    INSERT INTO analyst_run (
        run_id, run_type, desk, status, requested_by, started_at,
        counters, policy_id
    )
    SELECT $2::uuid, 'brief', desk, 'queued', $5::text,
           $8::timestamptz, '{}'::jsonb, policy_id
    FROM authorized_desk
    RETURNING run_id, policy_id
), brief_insert AS (
    INSERT INTO analyst_brief_request (
        brief_id, run_id, desk, as_of, requested_by, requested_at, policy_id
    )
    SELECT $1::uuid, run_insert.run_id, authorized_desk.desk,
           $4::timestamptz, $5::text, $8::timestamptz, run_insert.policy_id
    FROM run_insert JOIN authorized_desk USING (policy_id)
    RETURNING brief_id, run_id, desk, as_of, requested_at
)
SELECT brief_id, run_id, desk, as_of, 'queued'::text AS status,
       FALSE AS coverage_complete, NULL::text AS html,
       NULL::uuid AS publication_id, NULL::timestamptz AS published_at
FROM brief_insert
"""


GET_BRIEF_SQL = """
SELECT request.brief_id, request.run_id, request.desk, request.as_of,
       CASE WHEN publication.publication_id IS NOT NULL THEN 'published'
            ELSE run.status END AS status,
       coalesce(publication.coverage_complete, FALSE) AS coverage_complete,
       publication.html, publication.publication_id, publication.published_at
FROM analyst_brief_request request
JOIN analyst_run run ON run.run_id = request.run_id
JOIN access_policy policy ON policy.policy_id = request.policy_id
LEFT JOIN LATERAL (
    SELECT item.publication_id, item.html, item.coverage_complete, item.published_at
    FROM analyst_brief_publication item
    WHERE item.brief_id = request.brief_id
    ORDER BY item.published_at DESC, item.publication_id DESC
    LIMIT 1
) publication ON TRUE
WHERE request.brief_id = $1::uuid
  AND request.desk = ANY($4::text[])
  AND $2::text = ANY(policy.allowed_entitlement_groups)
  AND (policy.barrier_side = 'public' OR $3::text = 'private')
"""


PUBLISH_BRIEF_SQL = """
WITH authorized AS (
    SELECT request.brief_id, request.run_id, request.desk, request.as_of,
           request.policy_id, completion.complete AS coverage_complete
    FROM analyst_brief_request request
    JOIN access_policy policy ON policy.policy_id = request.policy_id
    JOIN analysis_run_completion_v3 completion
      ON completion.run_id = request.run_id::text
    WHERE request.brief_id = $2::uuid
      AND request.desk = ANY($8::text[])
      AND $6::text = ANY(policy.allowed_entitlement_groups)
      AND (policy.barrier_side = 'public' OR $7::text = 'private')
      AND completion.complete
), publication AS (
    INSERT INTO analyst_brief_publication (
        publication_id, brief_id, html, coverage_complete,
        published_by, published_at, policy_id
    )
    SELECT $1::uuid, authorized.brief_id, $3::text, authorized.coverage_complete,
           $4::text, $5::timestamptz, authorized.policy_id
    FROM authorized
    RETURNING publication_id, brief_id, html, coverage_complete, published_at
), run_update AS (
    UPDATE analyst_run run
    SET status = 'completed', finished_at = $5::timestamptz
    FROM authorized
    WHERE run.run_id = authorized.run_id
    RETURNING run.run_id
)
SELECT authorized.brief_id, authorized.run_id, authorized.desk, authorized.as_of,
       'published'::text AS status, publication.coverage_complete,
       publication.html, publication.publication_id, publication.published_at
FROM authorized
JOIN publication USING (brief_id)
JOIN run_update USING (run_id)
"""


GET_RUN_SQL = """
WITH visible_run AS (
    SELECT run.run_id, run.run_type, run.status, run.started_at, run.finished_at,
           run.counters, run.error_summary, 1 AS precedence
    FROM analyst_run run
    JOIN access_policy policy ON policy.policy_id = run.policy_id
    WHERE run.run_id = $1::uuid
      AND (run.desk IS NULL OR run.desk = ANY($4::text[]))
      AND $2::text = ANY(policy.allowed_entitlement_groups)
      AND (policy.barrier_side = 'public' OR $3::text = 'private')
    UNION ALL
    SELECT run.run_id, 'ingest'::text, run.status, run.started_at, run.finished_at,
           '{}'::jsonb, NULL::text, 2 AS precedence
    FROM ingest_run_v2 run
    JOIN access_policy policy ON policy.policy_id = run.policy_id
    WHERE run.run_id = $1::uuid
      AND $2::text = ANY(policy.allowed_entitlement_groups)
      AND (policy.barrier_side = 'public' OR $3::text = 'private')
)
SELECT run_id, run_type, status, started_at, finished_at, counters, error_summary
FROM visible_run
ORDER BY precedence
LIMIT 1
"""


def _uuid(value: str) -> UUID:
    try:
        return UUID(value)
    except ValueError as exc:
        raise ResourceNotFoundError(value) from exc


def _json_dict(value: object) -> dict[str, Any]:
    if isinstance(value, str):
        parsed = json.loads(value)
        return dict(parsed) if isinstance(parsed, dict) else {}
    if isinstance(value, dict):
        return dict(value)
    return {}


def _string_tuple(value: object) -> tuple[str, ...]:
    if isinstance(value, (list, tuple)):
        return tuple(str(item) for item in value)
    return ()


def _signal_view(row: Any) -> SignalView:
    return SignalView(
        signal_id=str(row["signal_id"]),
        pattern_id=row["pattern_id"],
        pattern_version=row["pattern_version"],
        entity_id=str(row["entity_id"]),
        entity_name=row["entity_name"],
        desk=row["desk"],
        status=row["status"],
        score=row["score"],
        as_of=row["as_of"],
        changed_at=row["changed_at"],
        assertion_ids=_string_tuple(row["assertion_ids"]),
        evidence_span_ids=_string_tuple(row["evidence_span_ids"]),
        latest_feedback=row["latest_feedback"],
        closed_at=row["closed_at"],
    )


def _brief_view(row: Any) -> BriefView:
    return BriefView(
        brief_id=str(row["brief_id"]),
        run_id=str(row["run_id"]),
        desk=row["desk"],
        as_of=row["as_of"],
        status=row["status"],
        coverage_complete=row["coverage_complete"],
        html=row["html"],
        publication_id=(str(row["publication_id"]) if row["publication_id"] is not None else None),
        published_at=row["published_at"],
    )


class PostgresAnalystService:
    """Production analyst service backed by the versioned evidence ledger."""

    def __init__(
        self,
        dsn: str,
        *,
        pool: asyncpg.Pool | None = None,
        pool_provider: PostgresPoolProvider | None = None,
    ) -> None:
        if not dsn.strip():
            raise ValueError("Postgres DSN is required")
        self._dsn = dsn
        self._pool = pool
        self._pool_provider = pool_provider
        self._owns_pool = pool is None and pool_provider is None

    async def _get_pool(self) -> asyncpg.Pool:
        if self._pool is None:
            self._pool = (
                await self._pool_provider.get_pool()
                if self._pool_provider is not None
                else await asyncpg.create_pool(self._dsn, min_size=1, max_size=8)
            )
        return self._pool

    @staticmethod
    def _access_args(principal: RequestPrincipal) -> tuple[str, str]:
        return (
            principal.principal.entitlement_group,
            principal.principal.side.value,
        )

    async def list_signals(
        self,
        principal: RequestPrincipal,
        *,
        desk: str,
        status: str | None,
        limit: int,
    ) -> list[SignalView]:
        principal.require_role("analyst", "reviewer", "admin")
        principal.require_desk(desk)
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        rows = await pool.fetch(LIST_SIGNALS_SQL, group, side, desk, status, limit)
        return [_signal_view(row) for row in rows]

    async def get_signal(self, principal: RequestPrincipal, signal_id: str) -> SignalView:
        principal.require_role("analyst", "reviewer", "admin")
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            GET_SIGNAL_SQL,
            _uuid(signal_id),
            group,
            side,
            sorted(principal.desks),
        )
        if row is None:
            raise ResourceNotFoundError(signal_id)
        return _signal_view(row)

    async def submit_feedback(
        self,
        principal: RequestPrincipal,
        signal_id: str,
        request: FeedbackRequest,
    ) -> FeedbackReceipt:
        principal.require_role("analyst", "reviewer", "admin")
        group, side = self._access_args(principal)
        now = datetime.now(UTC)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            INSERT_FEEDBACK_SQL,
            uuid4(),
            _uuid(signal_id),
            request.verdict.value,
            request.reason,
            principal.principal.principal_id,
            group,
            side,
            sorted(principal.desks),
            now,
        )
        if row is None:
            raise ResourceNotFoundError(signal_id)
        return FeedbackReceipt(
            feedback_id=str(row["feedback_id"]),
            signal_id=str(row["signal_id"]),
            recorded_at=row["recorded_at"],
        )

    async def close_signal(
        self,
        principal: RequestPrincipal,
        signal_id: str,
        request: SignalCloseRequest,
    ) -> SignalCloseReceipt:
        principal.require_role("analyst", "reviewer", "admin")
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        async with pool.acquire() as connection, connection.transaction():
            row = await connection.fetchrow(
                CLOSE_SIGNAL_SELECT_SQL,
                _uuid(signal_id),
                group,
                side,
                sorted(principal.desks),
            )
            if row is None:
                raise ResourceNotFoundError(signal_id)
            current_status = str(row["to_status"])
            if current_status in {"suppressed", "expired", "withdrawn"}:
                return SignalCloseReceipt(
                    transition_id=str(row["transition_id"]),
                    signal_id=signal_id,
                    status=current_status,
                    closed_at=row["occurred_at"],
                    already_closed=True,
                )
            target_status = "withdrawn" if current_status == "published" else "suppressed"
            transition_id = uuid4()
            closed_at = datetime.now(UTC)
            reason = f"analyst_close:{request.reason.value}:{request.note}"
            await connection.execute(
                INSERT_SIGNAL_CLOSE_SQL,
                transition_id,
                row["signal_id"],
                current_status,
                target_status,
                closed_at,
                reason,
                principal.principal.principal_id,
                row["policy_id"],
            )
            aggregate_version = int(row["next_aggregate_version"])
            await connection.execute(
                INSERT_SIGNAL_CLOSE_OUTBOX_SQL,
                outbox_event_id("signal.transitioned.v1", row["signal_id"], aggregate_version),
                row["signal_id"],
                aggregate_version,
                closed_at,
                uuid4(),
                row["policy_id"],
                json.dumps(
                    {
                        "from_status": current_status,
                        "signal_id": signal_id,
                        "to_status": target_status,
                        "transition_id": str(transition_id),
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            )
            return SignalCloseReceipt(
                transition_id=str(transition_id),
                signal_id=signal_id,
                status=target_status,
                closed_at=closed_at,
            )

    async def get_entity(self, principal: RequestPrincipal, entity_id: str) -> EntityView:
        principal.require_role("analyst", "reviewer", "admin")
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        entity_uuid = _uuid(entity_id)
        row = await pool.fetchrow(GET_ENTITY_SQL, entity_uuid, group, side)
        if row is None:
            raise ResourceNotFoundError(entity_id)
        timeline_rows = await pool.fetch(
            GET_ENTITY_TIMELINE_SQL,
            entity_uuid,
            group,
            side,
        )
        timeline = tuple(
            EntityAssertionView(
                assertion_id=str(item["assertion_id"]),
                predicate=item["predicate"],
                object_json=_json_dict(item["object_json"]),
                qualifiers=_json_dict(item["qualifiers"]),
                event_time=item["event_time"],
                valid_from=item["valid_from"],
                valid_to=item["valid_to"],
                recorded_at=item["recorded_at"],
                confidence=item["confidence"],
                evidence_span_ids=_string_tuple(item["evidence_span_ids"]),
            )
            for item in timeline_rows
        )
        return EntityView(
            entity_id=str(row["entity_id"]),
            entity_type=row["entity_type"],
            canonical_name=row["canonical_name"],
            identifiers={
                str(key): str(value) for key, value in _json_dict(row["identifiers"]).items()
            },
            timeline=timeline,
        )

    async def get_evidence(
        self, principal: RequestPrincipal, evidence_span_id: str
    ) -> EvidenceSpanView:
        principal.require_role("analyst", "reviewer", "admin")
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            GET_EVIDENCE_SQL,
            _uuid(evidence_span_id),
            group,
            side,
        )
        if row is None:
            raise ResourceNotFoundError(evidence_span_id)
        return EvidenceSpanView(
            evidence_span_id=str(row["evidence_span_id"]),
            document_version_id=str(row["document_version_id"]),
            title=row["title"],
            quote=row["quote"],
            char_start=row["char_start"],
            char_end=row["char_end"],
            source_url=row["source_url"],
            source_id=row["source_id"],
            published_at=row["published_at"],
        )

    async def decide_review(
        self,
        principal: RequestPrincipal,
        subject_type: str,
        subject_id: str,
        request: ReviewDecisionRequest,
    ) -> ReviewReceipt:
        principal.require_role("reviewer", "admin")
        if subject_type not in {"signal", "claim_candidate", "entity_link"}:
            raise ResourceNotFoundError(subject_type)
        group, side = self._access_args(principal)
        now = datetime.now(UTC)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            INSERT_REVIEW_SQL,
            uuid4(),
            subject_type,
            _uuid(subject_id),
            request.decision.value,
            request.reason,
            principal.principal.principal_id,
            now,
            group,
            side,
            sorted(principal.desks),
        )
        if row is None:
            raise ResourceNotFoundError(subject_id)
        return ReviewReceipt(
            review_id=str(row["review_id"]),
            subject_type=row["subject_type"],
            subject_id=str(row["subject_id"]),
            decided_at=row["decided_at"],
        )

    async def request_brief(self, principal: RequestPrincipal, request: BriefRequest) -> BriefView:
        principal.require_role("analyst", "admin")
        principal.require_desk(request.desk)
        group, side = self._access_args(principal)
        now = datetime.now(UTC)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            INSERT_BRIEF_REQUEST_SQL,
            uuid4(),
            uuid4(),
            request.desk,
            request.as_of,
            principal.principal.principal_id,
            group,
            side,
            now,
        )
        if row is None:
            raise ResourceNotFoundError(request.desk)
        return _brief_view(row)

    async def get_brief(self, principal: RequestPrincipal, brief_id: str) -> BriefView:
        principal.require_role("analyst", "publisher", "admin")
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            GET_BRIEF_SQL,
            _uuid(brief_id),
            group,
            side,
            sorted(principal.desks),
        )
        if row is None:
            raise ResourceNotFoundError(brief_id)
        return _brief_view(row)

    async def publish_brief(
        self,
        principal: RequestPrincipal,
        brief_id: str,
        request: BriefPublicationRequest,
    ) -> BriefView:
        principal.require_role("publisher", "admin")
        await self.get_brief(principal, brief_id)
        group, side = self._access_args(principal)
        now = datetime.now(UTC)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            PUBLISH_BRIEF_SQL,
            uuid4(),
            _uuid(brief_id),
            request.html,
            principal.principal.principal_id,
            now,
            group,
            side,
            sorted(principal.desks),
        )
        if row is None:
            raise PublicationNotReadyError(
                "brief publication requires server-computed complete coverage"
            )
        return _brief_view(row)

    async def get_run(self, principal: RequestPrincipal, run_id: str) -> RunView:
        principal.require_role("operator", "admin")
        group, side = self._access_args(principal)
        pool = await self._get_pool()
        row = await pool.fetchrow(
            GET_RUN_SQL,
            _uuid(run_id),
            group,
            side,
            sorted(principal.desks),
        )
        if row is None:
            raise ResourceNotFoundError(run_id)
        counters = {str(key): int(value) for key, value in _json_dict(row["counters"]).items()}
        return RunView(
            run_id=str(row["run_id"]),
            run_type=row["run_type"],
            status=row["status"],
            started_at=row["started_at"],
            finished_at=row["finished_at"],
            counters=counters,
            error_summary=row["error_summary"],
        )

    async def ready(self) -> bool:
        try:
            pool = await self._get_pool()
            return bool(
                await pool.fetchval(
                    """
                    SELECT to_regclass('public.analyst_signal_feedback') IS NOT NULL
                       AND to_regclass('public.intelligence_signal') IS NOT NULL
                    """
                )
            )
        except (OSError, asyncpg.PostgresError):
            return False

    async def close(self) -> None:
        if self._pool is not None and self._owns_pool:
            await self._pool.close()
        self._pool = None
