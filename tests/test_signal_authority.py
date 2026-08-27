"""PostgreSQL-ledger signal authority contracts."""

from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from fi_intel.application.signal_authority import LedgerSignalAuthority
from fi_intel.graph.signals import Signal, SignalLifecycleState, signal_scope_key
from fi_intel.ledger import (
    AccessPolicy,
    InMemoryIntelligenceLedger,
    SignalStatus,
    entity_identity_id,
    signal_identity_id,
)
from fi_intel.sources.canonical import BarrierSide

NOW = datetime(2026, 8, 27, 10, tzinfo=UTC)


def _signal(state: SignalLifecycleState, as_of: datetime) -> Signal:
    entity_key = "529900EXAMPLE00000001"
    authorization_scope = "policy:test-scope"
    material_arguments = {"instrument": "XS0000000001"}
    scope_key = signal_scope_key(material_arguments, authorization_scope)
    signal_id = signal_identity_id(
        "maturity-wall",
        "2.0.0",
        entity_identity_id("Organization", entity_key),
        scope_key,
    )
    return Signal(
        signal_id=str(signal_id),
        pattern="maturity-wall",
        pattern_version="2.0.0",
        entity_key=entity_key,
        entity_name="Example Bank",
        priority=80,
        opportunity_score=0.8,
        ranking_base_score=0.75,
        lifecycle_state=state,
        opened_at=NOW,
        updated_at=as_of,
        last_confirmed_at=as_of,
        resolved_at=as_of if state is SignalLifecycleState.RESOLVED else None,
        fired_at=NOW,
        as_of=as_of,
        evidence={},
        material_arguments=material_arguments,
        authorization_scope=authorization_scope,
        policy_version="policy-v1",
    )


async def test_signal_is_committed_before_idempotent_projection_and_resolution() -> None:
    policy = AccessPolicy(
        policy_id=uuid4(),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=frozenset({"fi_gcc_public"}),
        created_at=datetime(2024, 1, 1, tzinfo=UTC),
    )
    ledger = InMemoryIntelligenceLedger()
    authority = LedgerSignalAuthority(ledger, policy=policy, correlation_id=uuid4())
    active = _signal(SignalLifecycleState.NEW, NOW)

    await authority.record(active, 0.75)
    await authority.record(active, 0.75)

    history = await ledger.signal_history(UUID(active.signal_id))
    assert [item.to_status for item in history] == [
        SignalStatus.CANDIDATE,
        SignalStatus.CONFIRMED,
    ]

    unchanged = _signal(SignalLifecycleState.UNCHANGED, NOW + timedelta(days=1))
    await authority.record(unchanged, 0.75)
    history = await ledger.signal_history(UUID(active.signal_id))
    assert [item.to_status for item in history] == [
        SignalStatus.CANDIDATE,
        SignalStatus.CONFIRMED,
        SignalStatus.CONFIRMED,
    ]

    resolved = _signal(SignalLifecycleState.RESOLVED, NOW + timedelta(days=2))
    await authority.record(resolved, 0.75)
    history = await ledger.signal_history(UUID(active.signal_id))
    assert history[-1].to_status is SignalStatus.EXPIRED
    events = await ledger.pending_events()
    assert sum(item.event_type == "signal.transitioned.v1" for item in events) == 4
