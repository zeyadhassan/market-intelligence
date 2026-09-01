"""Service-free contracts for the policy-scoped Postgres analyst API."""

from datetime import UTC, datetime
from pathlib import Path
from typing import cast
from uuid import UUID

import asyncpg
import pytest

from fi_intel.api.auth import RequestPrincipal
from fi_intel.api.models import SignalCloseReason, SignalCloseRequest
from fi_intel.api.postgres import (
    CLOSE_SIGNAL_SELECT_SQL,
    GET_BRIEF_SQL,
    GET_ENTITY_SQL,
    GET_ENTITY_TIMELINE_SQL,
    GET_EVIDENCE_SQL,
    GET_RUN_SQL,
    GET_SIGNAL_SQL,
    INSERT_BRIEF_REQUEST_SQL,
    INSERT_FEEDBACK_SQL,
    INSERT_REVIEW_SQL,
    INSERT_SIGNAL_CLOSE_OUTBOX_SQL,
    INSERT_SIGNAL_CLOSE_SQL,
    LIST_SIGNALS_SQL,
    PUBLISH_BRIEF_SQL,
    PostgresAnalystService,
)
from fi_intel.retrieval.entitlement import Principal, Side

NOW = datetime(2025, 3, 1, 10, tzinfo=UTC)
SIGNAL_ID = UUID("10000000-0000-0000-0000-000000000001")
ENTITY_ID = UUID("20000000-0000-0000-0000-000000000001")
TRANSITION_ID = UUID("30000000-0000-0000-0000-000000000001")
POLICY_ID = UUID("40000000-0000-0000-0000-000000000001")

PRINCIPAL = RequestPrincipal(
    subject="alice",
    principal=Principal(
        principal_id="directory-alice",
        entitlement_group="fi-public",
        side=Side.PUBLIC,
    ),
    desks=frozenset({"fi_gcc"}),
    roles=frozenset({"analyst", "reviewer"}),
    purposes=frozenset({"market_intelligence"}),
)


def _signal_row() -> dict[str, object]:
    return {
        "signal_id": SIGNAL_ID,
        "pattern_id": "capital_programme",
        "pattern_version": "2",
        "entity_id": ENTITY_ID,
        "entity_name": "Example Bank",
        "desk": "fi_gcc",
        "status": "confirmed",
        "score": 0.88,
        "as_of": NOW,
        "changed_at": NOW,
        "assertion_ids": [UUID("50000000-0000-0000-0000-000000000001")],
        "evidence_span_ids": [UUID("60000000-0000-0000-0000-000000000001")],
        "latest_feedback": "needs_review",
        "closed_at": None,
    }


class _ReadPool:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows
        self.fetch_calls: list[tuple[str, tuple[object, ...]]] = []

    async def fetch(self, sql: str, *args: object) -> list[dict[str, object]]:
        self.fetch_calls.append((sql, args))
        return self.rows


class _CloseConnection:
    def __init__(self, status: str = "confirmed") -> None:
        self.status = status
        self.fetchrow_calls: list[tuple[str, tuple[object, ...]]] = []
        self.execute_calls: list[tuple[str, tuple[object, ...]]] = []
        self.transactions = 0

    def acquire(self) -> "_CloseConnection":
        return self

    def transaction(self) -> "_CloseConnection":
        self.transactions += 1
        return self

    async def __aenter__(self) -> "_CloseConnection":
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: object,
    ) -> None:
        del exc_type, exc_value, traceback

    async def fetchrow(self, sql: str, *args: object) -> dict[str, object]:
        self.fetchrow_calls.append((sql, args))
        return {
            "signal_id": SIGNAL_ID,
            "policy_id": POLICY_ID,
            "transition_id": TRANSITION_ID,
            "to_status": self.status,
            "occurred_at": NOW,
            "next_aggregate_version": 3,
        }

    async def execute(self, sql: str, *args: object) -> str:
        self.execute_calls.append((sql, args))
        return "INSERT 0 1"


@pytest.mark.parametrize(
    "sql",
    [
        LIST_SIGNALS_SQL,
        GET_SIGNAL_SQL,
        GET_ENTITY_SQL,
        GET_ENTITY_TIMELINE_SQL,
        GET_EVIDENCE_SQL,
        INSERT_FEEDBACK_SQL,
        CLOSE_SIGNAL_SELECT_SQL,
        INSERT_REVIEW_SQL,
        INSERT_BRIEF_REQUEST_SQL,
        GET_BRIEF_SQL,
        PUBLISH_BRIEF_SQL,
        GET_RUN_SQL,
    ],
)
def test_sensitive_sql_requires_policy_group_and_barrier(sql: str) -> None:
    normalized = " ".join(sql.lower().split())
    assert "join access_policy" in normalized
    assert "allowed_entitlement_groups" in normalized
    assert "barrier_side = 'public'" in normalized
    assert "= 'private'" in normalized


def test_evidence_sql_rechecks_current_source_licence_and_entitlement() -> None:
    normalized = " ".join(GET_EVIDENCE_SQL.lower().split())
    assert "join source_registry" in normalized
    assert "join entitlement_grant" in normalized
    assert "source.licensed" in normalized
    assert "span.quote" in normalized


def test_migration_has_identity_audit_policy_and_append_only_contracts() -> None:
    migration = (
        Path(__file__).parents[1] / "deploy" / "migrations" / "0005_analyst_api.sql"
    ).read_text(encoding="utf-8")
    normalized = migration.lower()

    assert "create table principal_access" in normalized
    assert "valid_until" in normalized and "revoked_at" in normalized
    assert "create table analyst_signal_feedback" in normalized
    assert "create table analyst_review_decision" in normalized
    assert "create table analyst_brief_publication" in normalized
    assert "reject_analyst_history_mutation" in normalized
    assert "assert_policy_not_wider" in normalized
    assert "insert into schema_migration" not in normalized
    assert not normalized.lstrip().startswith("begin;")


async def test_signal_inbox_uses_one_policy_scoped_query_and_maps_detail() -> None:
    pool = _ReadPool([_signal_row()])
    service = PostgresAnalystService("postgresql://unused")
    service._pool = cast(asyncpg.Pool, pool)  # noqa: SLF001

    result = await service.list_signals(
        PRINCIPAL,
        desk="fi_gcc",
        status="confirmed",
        limit=25,
    )

    assert len(result) == 1
    assert result[0].entity_name == "Example Bank"
    assert result[0].evidence_span_ids == ("60000000-0000-0000-0000-000000000001",)
    assert pool.fetch_calls == [
        (LIST_SIGNALS_SQL, ("fi-public", "public", "fi_gcc", "confirmed", 25))
    ]


async def test_close_signal_locks_authorized_subject_and_appends_transition() -> None:
    connection = _CloseConnection()
    service = PostgresAnalystService("postgresql://unused")
    service._pool = cast(asyncpg.Pool, connection)  # noqa: SLF001

    receipt = await service.close_signal(
        PRINCIPAL,
        str(SIGNAL_ID),
        SignalCloseRequest(reason=SignalCloseReason.ACTIONED, note="Mandate handed to coverage."),
    )

    assert connection.transactions == 1
    assert connection.fetchrow_calls[0][0] == CLOSE_SIGNAL_SELECT_SQL
    assert len(connection.execute_calls) == 2
    sql, args = connection.execute_calls[0]
    assert sql == INSERT_SIGNAL_CLOSE_SQL
    assert args[2:4] == ("confirmed", "suppressed")
    assert args[6:] == ("directory-alice", POLICY_ID)
    outbox_sql, outbox_args = connection.execute_calls[1]
    assert outbox_sql == INSERT_SIGNAL_CLOSE_OUTBOX_SQL
    assert outbox_args[1:3] == (SIGNAL_ID, 3)
    assert '"to_status":"suppressed"' in str(outbox_args[-1])
    assert receipt.status == "suppressed"
    assert not receipt.already_closed


async def test_close_signal_is_idempotent_for_terminal_lifecycle() -> None:
    connection = _CloseConnection(status="withdrawn")
    service = PostgresAnalystService("postgresql://unused")
    service._pool = cast(asyncpg.Pool, connection)  # noqa: SLF001

    receipt = await service.close_signal(
        PRINCIPAL,
        str(SIGNAL_ID),
        SignalCloseRequest(reason=SignalCloseReason.STALE, note="Already closed."),
    )

    assert receipt.already_closed
    assert receipt.transition_id == str(TRANSITION_ID)
    assert connection.execute_calls == []
