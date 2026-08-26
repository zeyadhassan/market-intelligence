"""Caller identity → allowed source set, enforced in the data layer.

ENTITLEMENT_SQL is the single source of truth for the entitlement
predicate. The Postgres path executes it verbatim; the in-memory test
store mirrors the same checks directly.

Two independent conditions, both required:
  1. an entitlement_grant row links the caller's group to the source, and
  2. the caller's barrier side admits the document's barrier side
     (private-side principals see both sides; public-side see public only).
"""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class Side(StrEnum):
    PUBLIC = "public"
    PRIVATE = "private"


class Principal(BaseModel):
    """The authenticated caller as far as retrieval is concerned."""

    model_config = ConfigDict(frozen=True)

    principal_id: str
    entitlement_group: str
    side: Side = Side.PUBLIC


#: Source of truth for the entitlement predicate. Every
#: retrieval query embeds this fragment; there is no unfiltered variant.
ENTITLEMENT_SQL = """
JOIN source_registry sr ON sr.source_id = d.source_id
JOIN entitlement_grant eg ON eg.source_id = d.source_id
                         AND eg.entitlement_group = %(group)s
WHERE sr.licensed
  AND (d.barrier_side = 'public' OR %(side)s = 'private')
"""

#: As-of predicate, applied in SQL — never in Python after fetching.
AS_OF_SQL = "AND d.recorded_at <= %(as_of)s"


def grants_for(group: str, all_grants: set[tuple[str, str]]) -> set[str]:
    """The source_ids a group may read, from (group, source_id) grant rows."""
    return {source_id for grp, source_id in all_grants if grp == group}
