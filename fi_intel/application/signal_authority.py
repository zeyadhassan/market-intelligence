"""PostgreSQL-first authority for canonical detector signal transitions."""

from __future__ import annotations

from datetime import datetime, timedelta
from uuid import UUID, uuid5

from pydantic import JsonValue

from fi_intel.graph.signals import Signal, SignalLifecycleState, signal_scope_key
from fi_intel.ledger.models import (
    AccessPolicy,
    EntityIdentity,
    OutboxEvent,
    SignalIdentity,
    SignalStatus,
    SignalTransition,
    entity_identity_id,
    outbox_event_id,
)
from fi_intel.ledger.repository import IntelligenceLedger

_TRANSITION_NAMESPACE = UUID("77af9fe7-29d0-5ecf-92cc-75e32de0b747")


def _transition_id(signal_id: UUID, version: int, status: SignalStatus) -> UUID:
    return uuid5(_TRANSITION_NAMESPACE, f"{signal_id}:{version}:{status.value}")


def _desired_status(state: SignalLifecycleState) -> SignalStatus:
    if state is SignalLifecycleState.SUPPRESSED:
        return SignalStatus.SUPPRESSED
    if state is SignalLifecycleState.RESOLVED:
        return SignalStatus.EXPIRED
    return SignalStatus.CONFIRMED


class LedgerSignalAuthority:
    """Append canonical signal state before its Neo4j projection is updated."""

    def __init__(
        self,
        ledger: IntelligenceLedger,
        *,
        policy: AccessPolicy,
        correlation_id: UUID,
    ) -> None:
        self._ledger = ledger
        self._policy = policy
        self._correlation_id = correlation_id

    async def record(self, signal: Signal, score_anchor: float) -> None:
        await self._ledger.register_policy(self._policy)
        subject_entity_id = entity_identity_id("Organization", signal.entity_key)
        entity = EntityIdentity(
            entity_id=subject_entity_id,
            entity_type="Organization",
            canonical_name=signal.entity_key,
            created_at=self._policy.created_at,
            policy_id=self._policy.policy_id,
        )
        await self._ledger.register_entity(
            entity,
            self._event(
                "entity.registered.v1",
                entity.entity_id,
                1,
                {"entity_id": str(entity.entity_id)},
                self._policy.created_at,
            ),
        )
        scope_key = signal_scope_key(signal.material_arguments, signal.authorization_scope)
        identity = SignalIdentity(
            signal_id=UUID(signal.signal_id),
            pattern_id=signal.pattern,
            pattern_version=signal.pattern_version,
            subject_entity_id=subject_entity_id,
            scope_key=scope_key,
            created_at=self._policy.created_at,
            policy_id=self._policy.policy_id,
        )
        history = await self._ledger.signal_history(identity.signal_id)
        previous = history[-1].to_status if history else None
        desired = _desired_status(signal.lifecycle_state)
        assertion_ids = tuple(UUID(value) for value in signal.matched_assertion_ids)

        if previous == desired:
            if desired is not SignalStatus.CONFIRMED or history[-1].as_of == signal.as_of:
                return
        if (
            previous
            in {
                SignalStatus.SUPPRESSED,
                SignalStatus.EXPIRED,
                SignalStatus.WITHDRAWN,
            }
            and desired is SignalStatus.CONFIRMED
        ):
            previous = await self._commit(
                identity,
                previous,
                SignalStatus.CANDIDATE,
                signal,
                score_anchor,
                assertion_ids,
                len(history) + 1,
                signal.as_of,
            )
            history = await self._ledger.signal_history(identity.signal_id)
        if previous is None and desired is not SignalStatus.CANDIDATE:
            previous = await self._commit(
                identity,
                None,
                SignalStatus.CANDIDATE,
                signal,
                score_anchor,
                assertion_ids,
                len(history) + 1,
                signal.as_of,
            )
            history = await self._ledger.signal_history(identity.signal_id)
        if previous != desired or desired is SignalStatus.CONFIRMED:
            occurred_at = signal.as_of
            if history and occurred_at <= history[-1].occurred_at:
                occurred_at = history[-1].occurred_at + timedelta(microseconds=1)
            await self._commit(
                identity,
                previous,
                desired,
                signal,
                score_anchor,
                assertion_ids,
                len(history) + 1,
                occurred_at,
            )

    async def _commit(
        self,
        identity: SignalIdentity,
        previous: SignalStatus | None,
        desired: SignalStatus,
        signal: Signal,
        score_anchor: float,
        assertion_ids: tuple[UUID, ...],
        version: int,
        occurred_at: datetime,
    ) -> SignalStatus:
        transition = SignalTransition(
            transition_id=_transition_id(identity.signal_id, version, desired),
            signal_id=identity.signal_id,
            from_status=previous,
            to_status=desired,
            occurred_at=occurred_at,
            as_of=signal.as_of,
            score=signal.opportunity_score,
            contributing_assertion_ids=assertion_ids,
            reason=(
                f"detector lifecycle={signal.lifecycle_state.value}; "
                f"score_anchor={score_anchor:.4f}"
            ),
            actor="canonical-pattern-registry",
            policy_id=self._policy.policy_id,
        )
        await self._ledger.commit_signal_transition(
            identity,
            transition,
            self._event(
                "signal.transitioned.v1",
                identity.signal_id,
                version,
                {
                    "signal": signal.model_dump(mode="json"),
                    "score_anchor": score_anchor,
                    "ledger_status": desired.value,
                },
                transition.occurred_at,
            ),
        )
        return desired

    def _event(
        self,
        event_type: str,
        aggregate_id: UUID,
        version: int,
        payload: dict[str, JsonValue],
        occurred_at: datetime,
    ) -> OutboxEvent:
        return OutboxEvent(
            event_id=outbox_event_id(event_type, aggregate_id, version),
            event_type=event_type,
            aggregate_type=event_type.rsplit(".", maxsplit=2)[0],
            aggregate_id=aggregate_id,
            aggregate_version=version,
            occurred_at=occurred_at,
            correlation_id=self._correlation_id,
            policy_id=self._policy.policy_id,
            payload=payload,
        )


__all__ = ["LedgerSignalAuthority"]
