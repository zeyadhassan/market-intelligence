"""Stable access policies used by canonical source and projection workers."""

from datetime import UTC, datetime
from uuid import UUID, uuid5

from fi_intel.ledger.models import AccessPolicy
from fi_intel.sources.canonical import BarrierSide

POLICY_NAMESPACE = UUID("689960ce-920a-596d-85c7-cb2ca788170f")
POLICY_CREATED_AT = datetime(2024, 1, 1, tzinfo=UTC)


def public_source_policy() -> AccessPolicy:
    audiences = frozenset({"fi_gcc_public", "fi_gcc_private", "open_web_public"})
    return AccessPolicy(
        policy_id=uuid5(POLICY_NAMESPACE, "public|" + "|".join(sorted(audiences))),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=audiences,
        created_at=POLICY_CREATED_AT,
    )


def reference_source_policy() -> AccessPolicy:
    audiences = frozenset({"fi_gcc_public", "fi_gcc_private", "open_reference"})
    return AccessPolicy(
        policy_id=uuid5(POLICY_NAMESPACE, "reference|" + "|".join(sorted(audiences))),
        barrier_side=BarrierSide.PUBLIC,
        allowed_entitlement_groups=audiences,
        created_at=POLICY_CREATED_AT,
    )


__all__ = ["POLICY_NAMESPACE", "public_source_policy", "reference_source_policy"]
