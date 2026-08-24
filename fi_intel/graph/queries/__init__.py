"""Parameterized, versioned Cypher pattern templates.

Patterns are parameterized templates, never free-form LLM Cypher
(injection + accuracy risk). Each carries a version and a named owner. The
registry runs them and writes Signal nodes; each is independently
toggleable.

Owner: FIG platform team.
"""

import re

from pydantic import BaseModel, ConfigDict


class Pattern(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str
    version: str
    priority: int
    # Parameterized Cypher. Parameters: $as_of (temporal pin), $window_days.
    cypher: str


def _pin(alias: str) -> str:
    """Temporal visibility predicate for one assertion variable.

    Only assertions recorded on/before as_of and not superseded by then are
    visible. Applied to every assertion a pattern reads (invariant 10).
    """
    return (
        f"{alias}.recorded_at <= datetime($as_of) "
        f"AND ({alias}.superseded_at IS NULL OR {alias}.superseded_at > datetime($as_of))"
    )


def _build(query: str) -> str:
    """Expand {pin:alias} markers into the temporal predicate."""
    return re.sub(r"\{pin:(\w+)\}", lambda m: _pin(m.group(1)), query)


MATURITY_WALL = Pattern(
    name="maturity_wall_no_refi",
    version="1.0",
    priority=80,
    cypher=_build(
        """
        MATCH (org:Entity {node_type: 'Organization'})
        MATCH (a:Assertion {predicate: 'MATURES_ON'})-[:SUBJECT]->(inst:Entity)
        MATCH (a)-[:OBJECT]->(mat:Entity)
        MATCH (ai:Assertion {predicate: 'ISSUES'})-[:SUBJECT]->(org)
        MATCH (ai)-[:OBJECT]->(inst)
        WHERE {pin:a} AND {pin:ai}
          AND datetime(a.valid_from) <= datetime($as_of) + duration({days: $window_days})
          AND datetime(a.valid_from) >= datetime($as_of)
          AND NOT EXISTS {
              MATCH (r:Assertion {predicate: 'REFINANCES'})-[:SUBJECT]->(x)
              WHERE r.recorded_at <= datetime($as_of)
          }
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               inst.key AS instrument, a.properties_json AS props,
               a.source_doc_id AS doc
        """
    ),
)

RATING_PLUS_CAPITAL = Pattern(
    name="negative_rating_action_with_capital_decline",
    version="1.0",
    priority=70,
    cypher=_build(
        """
        MATCH (org:Entity {node_type: 'Organization'})
        MATCH (ra:Assertion {predicate: 'RATING_ACTION_ON'})-[:SUBJECT]->(org)
        MATCH (cm:Assertion {predicate: 'REPORTS_METRIC'})-[:SUBJECT]->(org)
        WHERE {pin:ra} AND {pin:cm}
          AND ra.properties_json CONTAINS '"direction": "negative"'
          AND cm.properties_json CONTAINS '"direction": "down"'
        RETURN DISTINCT org.key AS entity_key, org.display_name AS entity_name,
               ra.source_doc_id AS rating_doc, cm.source_doc_id AS metric_doc
        """
    ),
)

LEADERSHIP = Pattern(
    name="leadership_change_treasury",
    version="1.0",
    priority=50,
    cypher=_build(
        """
        MATCH (a:Assertion {predicate: 'LEADERSHIP_CHANGE_AT'})
        MATCH (a)-[:OBJECT]->(org:Entity {node_type: 'Organization'})
        WHERE {pin:a}
          AND (a.properties_json CONTAINS '"role": "treasurer"'
               OR a.properties_json CONTAINS '"role": "cfo"')
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               a.source_doc_id AS doc, a.properties_json AS props
        """
    ),
)

PROGRAMME = Pattern(
    name="board_approved_issuance_programme",
    version="1.0",
    priority=60,
    cypher=_build(
        """
        MATCH (a:Assertion {predicate: 'PROGRAMME_APPROVED_BY'})
        MATCH (a)-[:OBJECT]->(org:Entity {node_type: 'Organization'})
        WHERE {pin:a}
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               a.source_doc_id AS doc, a.properties_json AS props
        """
    ),
)

AT1_CALL = Pattern(
    name="at1_call_approaching_no_refi",
    version="1.0",
    priority=65,
    cypher=_build(
        """
        MATCH (org:Entity {node_type: 'Organization'})
        MATCH (a:Assertion {predicate: 'CALLABLE_ON'})-[:SUBJECT]->(inst:Entity)
        MATCH (ai:Assertion {predicate: 'ISSUES'})-[:SUBJECT]->(org)
        MATCH (ai)-[:OBJECT]->(inst)
        WHERE {pin:a} AND {pin:ai}
          AND a.properties_json CONTAINS '"class": "AT1"'
          AND datetime(a.valid_from) <= datetime($as_of) + duration({days: $window_days})
          AND datetime(a.valid_from) >= datetime($as_of)
          AND NOT EXISTS {
              MATCH (r:Assertion {predicate: 'REFINANCES'})-[:SUBJECT]->(x)
              WHERE r.recorded_at <= datetime($as_of)
          }
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               inst.key AS instrument, a.source_doc_id AS doc
        """
    ),
)

ALL_PATTERNS: tuple[Pattern, ...] = (
    MATURITY_WALL,
    RATING_PLUS_CAPITAL,
    LEADERSHIP,
    PROGRAMME,
    AT1_CALL,
)
