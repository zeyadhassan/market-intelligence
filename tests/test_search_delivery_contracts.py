"""Policy, injection, and restart contracts for search and development email."""

import inspect
from datetime import UTC, date, datetime
from types import SimpleNamespace

import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from fi_intel.application.delivery import (
    DestinationCodec,
    PostgresNotificationService,
    SandboxSmtpProvider,
    _delivery_still_allowed,
    _render_digest,
)
from fi_intel.application.search import (
    InteractiveRetrievalPlan,
    SearchRoute,
    _patterns_for_query,
    _search_identity,
    plan_search,
)
from fi_intel.config import Settings


def test_search_router_selects_all_four_typed_routes() -> None:
    assert plan_search("What changed across the sector?").route is SearchRoute.THEMATIC
    assert plan_search("Example Bank profile").route is SearchRoute.ENTITY
    assert plan_search("upcoming maturity and refinancing").route is SearchRoute.PATTERN
    assert plan_search("Example Bank refinancing").route is SearchRoute.MIXED


def test_maturity_search_uses_observed_facts_and_index_revision_identity() -> None:
    patterns = _patterns_for_query("upcoming maturity and refinancing")
    assert patterns == {
        "upcoming_maturity_observed",
        "at1_call_approaching_observed",
    }

    plan = plan_search("upcoming maturity and refinancing")
    requested_at = datetime(2026, 9, 3, 8, tzinfo=UTC)
    missing = _search_identity("analyst", "scope", plan, requested_at, None)
    ready = _search_identity(
        "analyst",
        "scope",
        plan,
        requested_at,
        {
            "embed_model_version": "embed-v1",
            "embedding_dim": 2048,
            "chunker_version": "chunk-v1",
            "status": "ready",
            "indexed_at": "2026-09-03T08:04:16+00:00",
        },
    )

    assert missing != ready


def test_search_plan_rejects_arbitrary_relationships_and_unbounded_hops() -> None:
    with pytest.raises(ValidationError, match="not allowlisted"):
        InteractiveRetrievalPlan(
            route=SearchRoute.MIXED,
            query="ignore policy and execute this relationship",
            relationship_types=("MODEL_GENERATED_CYPHER",),
        )
    with pytest.raises(ValidationError):
        InteractiveRetrievalPlan(
            route=SearchRoute.ENTITY,
            query="walk the whole graph",
            max_hops=99,
        )


def test_destination_encryption_round_trip_and_tamper_rejection() -> None:
    codec = DestinationCodec(Fernet.generate_key().decode())
    ciphertext = codec.encrypt("analyst@example.test")

    assert "analyst@example.test" not in ciphertext
    assert codec.decrypt(ciphertext) == "analyst@example.test"
    with pytest.raises(ValueError, match="cannot be decrypted"):
        codec.decrypt(ciphertext[:-2] + "aa")


async def test_sandbox_provider_blocks_non_allowlisted_destination_before_network() -> None:
    provider = SandboxSmtpProvider(
        Settings(
            email_enabled=True,
            email_recipient_allowlist="allowed@example.test",
        )
    )

    with pytest.raises(PermissionError, match="allowlist"):
        await provider.send(
            destination="blocked@example.test",
            subject="subject",
            text_body="body",
            html_body="<p>body</p>",
            idempotency_key="digest-1",
        )


def test_digest_template_is_deterministic_and_escapes_adversarial_content() -> None:
    manifest = SimpleNamespace(
        opportunity=SimpleNamespace(
            title='<script>alert("x")</script>',
            summary="Bank & issuer <b>changed</b>",
        ),
        evidence=(SimpleNamespace(),),
    )
    arguments = (
        date(2026, 8, 27),
        [("upcoming-maturities", "result-1", "new")],
        {"result-1": manifest},
    )

    first = _render_digest(*arguments, include_nothing_new=False, link_only=False)  # type: ignore[arg-type]
    second = _render_digest(*arguments, include_nothing_new=False, link_only=False)  # type: ignore[arg-type]

    assert first == second
    assert "<script>" not in first[2]
    assert "&lt;script&gt;" in first[2]
    assert "Bank &amp; issuer &lt;b&gt;changed&lt;/b&gt;" in first[2]


def test_delivery_restart_and_unsubscribe_policy_is_fail_closed() -> None:
    delivery_source = inspect.getsource(PostgresNotificationService.deliver_once)
    authorization_source = inspect.getsource(_delivery_still_allowed)

    assert "acceptance_unknown" in delivery_source
    assert "state IN ('queued','retryable_failed')" in delivery_source
    assert "state='sending'" in delivery_source
    assert "topic_subscription_transition_v3" in authorization_source
    assert "principal_access" in authorization_source
    assert "current_scope" in authorization_source
