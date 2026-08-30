"""Database-free guards for graph authorization wiring."""

import inspect
import re

import pytest

import fi_intel.cli as cli
from fi_intel.cli import serve, worker_analysis
from fi_intel.governance.audit import InMemoryAuditLog
from fi_intel.governance.policy import trusted_test_access
from fi_intel.graph.client import GraphClient
from fi_intel.graph.queries import ALL_PATTERNS


def test_every_pattern_assertion_alias_has_an_access_predicate() -> None:
    for pattern in ALL_PATTERNS:
        aliases = set(re.findall(r"\((\w+):Assertion", pattern.cypher))
        assert aliases, pattern.name
        for alias in aliases:
            assert f"{alias}.source_id IN $allowed_source_ids" in pattern.cypher
            assert f"{alias}.barrier_side = 'public' OR $side = 'private'" in pattern.cypher
        for column in (
            "_assertion_ids",
            "_latest_recorded_at",
            "_materiality_score",
            "_evidence_confidence",
        ):
            assert column in pattern.cypher
        assert "properties_json CONTAINS" not in pattern.cypher


async def test_production_graph_context_requires_successful_audit() -> None:
    access = trusted_test_access("wire", require_audit=True)
    client = GraphClient("bolt://localhost:1", "unused", "unused")
    try:
        with pytest.raises(RuntimeError, match="requires an audit log"):
            await client.audit_access(access, [("wire", "doc-1")])
    finally:
        await client.close()


async def test_graph_audit_records_the_verified_principal_once_per_document() -> None:
    audit = InMemoryAuditLog()
    access = trusted_test_access(
        "wire",
        principal_id="verified-user",
        entitlement_group="verified-group",
        require_audit=True,
    )
    client = GraphClient("bolt://localhost:1", "unused", "unused", audit=audit)
    try:
        await client.audit_access(access, [("wire", "doc-1"), ("wire", "doc-1")])
    finally:
        await client.close()

    assert len(audit.events) == 1
    assert audit.events[0].principal == "verified-user"
    assert audit.events[0].entitlement_group == "verified-group"


async def test_graph_audit_records_zero_result_probe() -> None:
    audit = InMemoryAuditLog()
    access = trusted_test_access("wire", require_audit=True)
    client = GraphClient("bolt://localhost:1", "unused", "unused", audit=audit)
    try:
        await client.audit_access(access, [])
    finally:
        await client.close()

    assert len(audit.events) == 1
    assert audit.events[0].operation == "graph_read"
    assert audit.events[0].result_count == 0
    assert audit.events[0].source_id is None


@pytest.mark.parametrize("command", [serve, worker_analysis])
def test_public_cli_does_not_accept_self_asserted_policy_fields(command) -> None:
    parameters = inspect.signature(command).parameters
    assert {"principal", "group", "side"}.isdisjoint(parameters)


def test_direct_result_command_implementations_are_removed() -> None:
    for name in (
        "backtest",
        "brief",
        "entities_resolve",
        "extract",
        "index_run",
        "ingest_run",
        "migrate",
        "patterns_run",
        "research",
        "search",
        "sources_peek",
    ):
        assert not hasattr(cli, name)
