"""The closed T-Box vocabulary.

Extraction selects node and edge types from these enums. Any unknown type
goes to a `proposed_type` review
queue and is never auto-admitted. Adding a member here is a
schema change: it requires a migration and a review, not just a commit.
"""

from enum import StrEnum


class NodeType(StrEnum):
    ORGANIZATION = "Organization"
    PERSON = "Person"
    INSTRUMENT = "Instrument"
    PROGRAMME = "Programme"
    RATING = "Rating"
    EVENT = "Event"
    METRIC = "Metric"


class EdgeType(StrEnum):
    # Structure
    SUBSIDIARY_OF = "SUBSIDIARY_OF"
    # Events affecting organizations
    RATING_ACTION_ON = "RATING_ACTION_ON"          # Rating -> Organization
    LEADERSHIP_CHANGE_AT = "LEADERSHIP_CHANGE_AT"  # Person/Event -> Organization
    PROGRAMME_APPROVED_BY = "PROGRAMME_APPROVED_BY"  # Programme -> Organization
    # Debt
    ISSUES = "ISSUES"                  # Organization -> Instrument
    MATURES_ON = "MATURES_ON"          # Instrument -> Event(maturity)
    CALLABLE_ON = "CALLABLE_ON"        # Instrument -> Event(first call)
    REFINANCES = "REFINANCES"          # Instrument/Programme -> Instrument
    # Metrics
    REPORTS_METRIC = "REPORTS_METRIC"  # Organization -> Metric value event
    # Outcome
    MANDATE_OF = "MANDATE_OF"          # Event(mandate) -> Organization
