"""Governed, typed, parameterized Cypher pattern assets."""

import re
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from fi_intel.graph.properties import PROJECTED_PROPERTY_NAMES
from fi_intel.ontology.vocab import EdgeType


class PatternDeploymentState(StrEnum):
    DRAFT = "draft"
    SHADOW = "shadow"
    PILOT = "pilot"
    ACTIVE = "active"
    RETIRED = "retired"


class MaterialityOperator(StrEnum):
    GREATER_THAN_OR_EQUAL = "gte"
    LESS_THAN_OR_EQUAL = "lte"
    EQUAL = "eq"


class CoverageScope(StrEnum):
    ASSERTION = "assertion"
    SOURCE_OPERATIONS = "source_operations"
    DESK_ACCOUNT = "desk_account"
    FACTUAL_ENTITY = "factual_entity"


class MaterialityThreshold(BaseModel):
    model_config = ConfigDict(frozen=True)

    attribute: str = Field(min_length=1)
    operator: MaterialityOperator
    value: float
    unit: str = Field(min_length=1)
    query_expression: str = Field(min_length=1)


class CoveragePrerequisite(BaseModel):
    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    scope: CoverageScope = CoverageScope.ASSERTION
    required_attributes: frozenset[str] = frozenset()


class Pattern(BaseModel):
    """A versioned detector contract reviewed as a domain asset."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(min_length=1)
    version: str = Field(pattern=r"^\d+\.\d+\.\d+$")
    precision_lineage: str = Field(pattern=r"^\d+$")
    hypothesis: str = Field(min_length=1)
    eligible_outcome_kinds: frozenset[str] = Field(min_length=1)
    required_claim_types: frozenset[EdgeType] = Field(min_length=1)
    required_attributes: frozenset[str] = Field(min_length=1)
    freshness_days: int = Field(gt=0)
    prediction_horizon_days: int = Field(gt=0)
    materiality_thresholds: tuple[MaterialityThreshold, ...] = Field(min_length=1)
    coverage_prerequisites: tuple[CoveragePrerequisite, ...] = Field(min_length=1)
    material_arguments: tuple[str, ...] = Field(min_length=1)
    priority: int = Field(ge=0, le=100)
    historical_precision: float | None = Field(default=None, ge=0.0, le=1.0)
    allowed_currencies: tuple[str, ...] = ()
    default_materiality_score: float = Field(default=0.0, ge=0.0, le=1.0)
    owner: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    deployment_state: PatternDeploymentState
    authorization_domain: str = Field(min_length=1)
    query_fixture_ids: tuple[str, ...] = Field(min_length=1)
    # Governed markers are rendered from metadata; callers cannot supply
    # materiality or currency literals independently of the reviewed fields.
    cypher_template: str = Field(min_length=1)

    @property
    def cypher(self) -> str:
        materiality = " AND ".join(
            f"({threshold.query_expression}) {_operator_cypher(threshold.operator)} "
            f"$materiality_threshold_{index}"
            for index, threshold in enumerate(self.materiality_thresholds)
        )
        query = self.cypher_template.replace("{materiality}", f"AND {materiality}")

        def currency_scope(match: re.Match[str]) -> str:
            if not self.allowed_currencies:
                raise ValueError("currency marker requires allowed_currencies metadata")
            return f"AND {match.group(1)}.fact_currency IN $allowed_currencies"

        query = re.sub(r"\{currency_scope:(\w+)\}", currency_scope, query)
        return _build(query)

    @property
    def query_parameters(self) -> dict[str, object]:
        parameters: dict[str, object] = {
            f"materiality_threshold_{index}": threshold.value
            for index, threshold in enumerate(self.materiality_thresholds)
        }
        parameters["allowed_currencies"] = [
            currency.casefold() for currency in self.allowed_currencies
        ]
        parameters["default_materiality_score"] = self.default_materiality_score
        return parameters

    @model_validator(mode="after")
    def _validate_query_contract(self) -> "Pattern":
        _validate_governed_markers(self)
        if self.precision_lineage != self.version.split(".", 1)[0]:
            raise ValueError("precision lineage must match the compatible semver major")
        required_columns = {
            "_assertion_ids",
            "_source_ids",
            "_source_doc_ids",
            "_barrier_sides",
            "_latest_recorded_at",
            "_materiality_score",
            "_evidence_confidence",
        }
        missing = sorted(column for column in required_columns if column not in self.cypher)
        if missing:
            raise ValueError(f"pattern query omits lifecycle columns: {missing}")
        if re.search(r"properties_json\s+CONTAINS", self.cypher, flags=re.IGNORECASE):
            raise ValueError("pattern query must compare typed assertion properties")
        missing_typed_attributes = sorted(
            attribute
            for attribute in self.required_attributes
            if f"fact_{attribute}" not in self.cypher
        )
        if missing_typed_attributes:
            raise ValueError(f"pattern query omits typed attributes: {missing_typed_attributes}")
        unprojected_attributes = sorted(self.required_attributes - PROJECTED_PROPERTY_NAMES)
        if unprojected_attributes:
            raise ValueError(f"pattern references unprojected attributes: {unprojected_attributes}")
        coverage_attributes = {
            attribute
            for prerequisite in self.coverage_prerequisites
            for attribute in prerequisite.required_attributes
        }
        if not coverage_attributes <= self.required_attributes:
            raise ValueError("coverage prerequisites must reference required attributes")
        if self.owner == self.reviewer:
            raise ValueError("pattern owner and reviewer must be independent")
        if len(set(self.material_arguments)) != len(self.material_arguments):
            raise ValueError("material arguments must be unique")
        return self

    @property
    def deployable(self) -> bool:
        return self.deployment_state in {
            PatternDeploymentState.PILOT,
            PatternDeploymentState.ACTIVE,
        }

    @property
    def computed_coverage_scopes(self) -> frozenset[CoverageScope]:
        return frozenset(
            prerequisite.scope
            for prerequisite in self.coverage_prerequisites
            if prerequisite.scope is not CoverageScope.ASSERTION
        )


def _validate_governed_markers(pattern: Pattern) -> None:
    if pattern.cypher_template.count("{materiality}") != 1:
        raise ValueError("pattern query must contain one governed materiality marker")
    currency_marker = "{currency_scope:" in pattern.cypher_template
    if pattern.allowed_currencies and not currency_marker:
        raise ValueError("currency-scoped pattern query omits governed currency marker")
    if not pattern.allowed_currencies and currency_marker:
        raise ValueError("currency marker requires an explicit allowed currency scope")
    if len(set(pattern.allowed_currencies)) != len(pattern.allowed_currencies):
        raise ValueError("allowed currencies must be unique")
    if any(not re.fullmatch(r"[A-Z]{3}", value) for value in pattern.allowed_currencies):
        raise ValueError("allowed currencies must be uppercase ISO 4217 codes")


def _pin(alias: str) -> str:
    """Knowledge-time, supersession, and valid-time visibility."""
    return (
        f"{alias}.recorded_at <= datetime($as_of) "
        f"AND ({alias}.superseded_at IS NULL OR {alias}.superseded_at > datetime($as_of)) "
        f"AND (coalesce({alias}.asserted_valid_to, {alias}.valid_to) IS NULL OR "
        f"datetime(coalesce({alias}.asserted_valid_to, {alias}.valid_to)) "
        f"> datetime($as_of)) "
        f"AND ({alias}.state_key IS NULL OR NOT EXISTS {{ "
        f"MATCH (newer:Assertion {{state_key: {alias}.state_key}}) "
        f"WHERE newer.assertion_id <> {alias}.assertion_id "
        f"AND newer.recorded_at <= datetime($as_of) "
        f"AND (newer.superseded_at IS NULL OR newer.superseded_at > datetime($as_of)) "
        "AND newer.source_id IN $allowed_source_ids "
        "AND (newer.barrier_side = 'public' OR $side = 'private') "
        f"AND newer.valid_from <= datetime($as_of) "
        f"AND newer.valid_from > {alias}.valid_from "
        f"AND (coalesce(newer.asserted_valid_to, newer.valid_to) IS NULL OR "
        f"datetime(coalesce(newer.asserted_valid_to, newer.valid_to)) "
        f"> datetime($as_of)) }})"
    )


def _fresh(alias: str) -> str:
    return f"{alias}.recorded_at >= datetime($as_of) - duration({{days: $freshness_days}})"


def _access(alias: str) -> str:
    """Source grant and information-barrier predicate for an assertion."""
    return (
        f"{alias}.source_id IN $allowed_source_ids "
        f"AND ({alias}.barrier_side = 'public' OR $side = 'private')"
    )


def _build(query: str) -> str:
    """Expand governed temporal, freshness, and authorization markers."""
    query = re.sub(r"\{pin:(\w+)\}", lambda match: _pin(match.group(1)), query)
    query = re.sub(r"\{fresh:(\w+)\}", lambda match: _fresh(match.group(1)), query)
    return re.sub(r"\{access:(\w+)\}", lambda match: _access(match.group(1)), query)


def _operator_cypher(operator: MaterialityOperator) -> str:
    return {
        MaterialityOperator.GREATER_THAN_OR_EQUAL: ">=",
        MaterialityOperator.LESS_THAN_OR_EQUAL: "<=",
        MaterialityOperator.EQUAL: "=",
    }[operator]


_OWNER = "FIG Signals"
_REVIEWER = "FIG Model Risk"
_AUTHORIZATION_DOMAIN = "caller-source-grants-and-barrier"
_MANDATE_OUTCOMES = frozenset({"mandate_announced"})
_USD_AMOUNT_SCOPE = ("USD",)


MATURITY_WALL = Pattern(
    name="maturity_wall_no_refi",
    version="3.1.0",
    precision_lineage="3",
    hypothesis=(
        "A material near-term maturity with complete refinancing coverage can precede "
        "a DCM mandate."
    ),
    eligible_outcome_kinds=_MANDATE_OUTCOMES,
    required_claim_types=frozenset({EdgeType.MATURES_ON, EdgeType.ISSUES}),
    required_attributes=frozenset({"amount_usd_mn", "currency"}),
    freshness_days=180,
    prediction_horizon_days=395,
    materiality_thresholds=(
        MaterialityThreshold(
            attribute="amount_usd_mn",
            operator=MaterialityOperator.GREATER_THAN_OR_EQUAL,
            value=250.0,
            unit="USD million",
            query_expression="a.fact_amount_usd_mn",
        ),
    ),
    coverage_prerequisites=(
        CoveragePrerequisite(
            name="refinancing-search-complete",
            description="Authorized refinancing coverage is complete for the instrument.",
            scope=CoverageScope.SOURCE_OPERATIONS,
        ),
        CoveragePrerequisite(
            name="instrument-factual-coverage-complete",
            description=(
                "The declared issuer, exchange, regulator, rating and news universe was "
                "reconciled for this instrument and time window."
            ),
            scope=CoverageScope.FACTUAL_ENTITY,
        ),
    ),
    material_arguments=("instrument", "currency"),
    priority=80,
    historical_precision=None,
    allowed_currencies=_USD_AMOUNT_SCOPE,
    owner=_OWNER,
    reviewer=_REVIEWER,
    deployment_state=PatternDeploymentState.ACTIVE,
    authorization_domain=_AUTHORIZATION_DOMAIN,
    query_fixture_ids=("gulf_meridian_dcm", "steady_state_decoy"),
    cypher_template=(
        """
        MATCH (org:Entity {node_type: 'Organization'})
        MATCH (a:Assertion {predicate: 'MATURES_ON'})-[:SUBJECT]->(inst:Entity)
        MATCH (a)-[:OBJECT]->(mat:Entity)
        MATCH (ai:Assertion {predicate: 'ISSUES'})-[:SUBJECT]->(org)
        MATCH (ai)-[:OBJECT]->(inst)
        WHERE {pin:a} AND {pin:ai} AND {fresh:a} AND {fresh:ai}
          AND {access:a} AND {access:ai}
          {materiality}
          {currency_scope:a}
          AND datetime(a.valid_from) <= datetime($as_of) + duration({days: $window_days})
          AND datetime(a.valid_from) >= datetime($as_of)
          AND NOT EXISTS {
              MATCH (r:Assertion {predicate: 'REFINANCES'})-[:OBJECT]->(inst)
              WHERE {pin:r} AND {access:r}
          }
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               inst.key AS instrument, a.fact_currency AS currency,
               a.fact_amount_usd_mn AS amount_usd_mn,
               a.source_doc_id AS doc, a.properties_json AS props,
               [a.assertion_id, ai.assertion_id] AS _assertion_ids,
               [a.source_id, ai.source_id] AS _source_ids,
               [a.source_doc_id, ai.source_doc_id] AS _source_doc_ids,
               [a.barrier_side, ai.barrier_side] AS _barrier_sides,
               CASE WHEN a.recorded_at >= ai.recorded_at
                    THEN a.recorded_at ELSE ai.recorded_at END AS _latest_recorded_at,
               CASE WHEN a.fact_amount_usd_mn >= $materiality_threshold_0 * 2.0 THEN 1.0
                    ELSE a.fact_amount_usd_mn / ($materiality_threshold_0 * 2.0)
                    END AS _materiality_score,
               (a.confidence + ai.confidence) / 2.0 AS _evidence_confidence
        """
    ),
)

RATING_PLUS_CAPITAL = Pattern(
    name="negative_rating_action_with_capital_decline",
    version="3.1.0",
    precision_lineage="3",
    hypothesis=(
        "A material CET1 decline near a negative outlook action can precede financing activity."
    ),
    eligible_outcome_kinds=_MANDATE_OUTCOMES,
    required_claim_types=frozenset({EdgeType.RATING_ACTION_ON, EdgeType.REPORTS_METRIC}),
    required_attributes=frozenset({"direction", "rating_type", "metric", "value", "prior"}),
    freshness_days=180,
    prediction_horizon_days=180,
    materiality_thresholds=(
        MaterialityThreshold(
            attribute="cet1_decline",
            operator=MaterialityOperator.GREATER_THAN_OR_EQUAL,
            value=0.5,
            unit="percentage points",
            query_expression="cm.fact_prior - cm.fact_value",
        ),
    ),
    coverage_prerequisites=(
        CoveragePrerequisite(
            name="comparable-capital-periods",
            description="Current and prior CET1 observations use comparable reporting periods.",
            required_attributes=frozenset({"value", "prior", "metric"}),
        ),
    ),
    material_arguments=("rating_type", "metric"),
    priority=70,
    historical_precision=None,
    owner=_OWNER,
    reviewer=_REVIEWER,
    deployment_state=PatternDeploymentState.ACTIVE,
    authorization_domain=_AUTHORIZATION_DOMAIN,
    query_fixture_ids=("gulf_meridian_dcm", "steady_state_decoy"),
    cypher_template=(
        """
        MATCH (org:Entity {node_type: 'Organization'})
        MATCH (ra:Assertion {predicate: 'RATING_ACTION_ON'})-[:SUBJECT|OBJECT]->(org)
        MATCH (cm:Assertion {predicate: 'REPORTS_METRIC'})-[:SUBJECT]->(org)
        WHERE {pin:ra} AND {pin:cm} AND {fresh:ra} AND {fresh:cm}
          AND {access:ra} AND {access:cm}
          AND ra.fact_direction = 'negative'
          AND ra.fact_rating_type = 'outlook'
          AND cm.fact_direction = 'down'
          AND cm.fact_metric = 'cet1'
          {materiality}
          AND abs(duration.inDays(date(cm.valid_from), date(ra.valid_from)).days) <= 120
        RETURN DISTINCT org.key AS entity_key, org.display_name AS entity_name,
               ra.fact_rating_type AS rating_type, cm.fact_metric AS metric,
               ra.source_doc_id AS rating_doc, cm.source_doc_id AS metric_doc,
               [ra.assertion_id, cm.assertion_id] AS _assertion_ids,
               [ra.source_id, cm.source_id] AS _source_ids,
               [ra.source_doc_id, cm.source_doc_id] AS _source_doc_ids,
               [ra.barrier_side, cm.barrier_side] AS _barrier_sides,
               CASE WHEN ra.recorded_at >= cm.recorded_at
                    THEN ra.recorded_at ELSE cm.recorded_at END AS _latest_recorded_at,
               CASE WHEN cm.fact_prior - cm.fact_value >= $materiality_threshold_0 * 4.0
                    THEN 1.0 ELSE (cm.fact_prior - cm.fact_value)
                    / ($materiality_threshold_0 * 4.0) END AS _materiality_score,
               (ra.confidence + cm.confidence) / 2.0 AS _evidence_confidence
        """
    ),
)

LEADERSHIP = Pattern(
    name="leadership_change_treasury",
    version="3.1.0",
    precision_lineage="3",
    hypothesis=(
        "A fresh treasury leadership change can create a time-sensitive coverage opportunity."
    ),
    eligible_outcome_kinds=_MANDATE_OUTCOMES,
    required_claim_types=frozenset({EdgeType.LEADERSHIP_CHANGE_AT}),
    required_attributes=frozenset({"role"}),
    freshness_days=120,
    prediction_horizon_days=180,
    materiality_thresholds=(
        MaterialityThreshold(
            attribute="account_tier_score",
            operator=MaterialityOperator.GREATER_THAN_OR_EQUAL,
            value=0.0,
            unit="normalized covered-account tier",
            query_expression="coalesce(org.account_tier_score, $default_materiality_score)",
        ),
    ),
    coverage_prerequisites=(
        CoveragePrerequisite(
            name="account-coverage-complete",
            description="Role classification and account coverage have been confirmed.",
            scope=CoverageScope.DESK_ACCOUNT,
            required_attributes=frozenset({"role"}),
        ),
    ),
    material_arguments=("role",),
    priority=60,
    historical_precision=None,
    default_materiality_score=0.0,
    owner=_OWNER,
    reviewer=_REVIEWER,
    deployment_state=PatternDeploymentState.ACTIVE,
    authorization_domain=_AUTHORIZATION_DOMAIN,
    query_fixture_ids=("gulf_meridian_dcm",),
    cypher_template=(
        """
        MATCH (a:Assertion {predicate: 'LEADERSHIP_CHANGE_AT'})
        MATCH (a)-[:OBJECT]->(org:Entity {node_type: 'Organization'})
        WHERE {pin:a} AND {fresh:a} AND {access:a}
          AND a.fact_role IN ['treasurer', 'cfo']
          {materiality}
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               a.fact_role AS role, a.source_doc_id AS doc,
               a.properties_json AS props,
               [a.assertion_id] AS _assertion_ids,
               [a.source_id] AS _source_ids,
               [a.source_doc_id] AS _source_doc_ids,
               [a.barrier_side] AS _barrier_sides,
               a.recorded_at AS _latest_recorded_at,
               coalesce(org.account_tier_score, $default_materiality_score)
                   AS _materiality_score,
               a.confidence AS _evidence_confidence
        """
    ),
)

PROGRAMME = Pattern(
    name="board_approved_issuance_programme",
    version="3.1.0",
    precision_lineage="3",
    hypothesis="A material approved but unmarketed issuance programme can precede a mandate.",
    eligible_outcome_kinds=_MANDATE_OUTCOMES,
    required_claim_types=frozenset({EdgeType.PROGRAMME_APPROVED_BY}),
    required_attributes=frozenset({"limit_usd_bn", "currency", "status", "marketed"}),
    freshness_days=180,
    prediction_horizon_days=365,
    materiality_thresholds=(
        MaterialityThreshold(
            attribute="limit_usd_bn",
            operator=MaterialityOperator.GREATER_THAN_OR_EQUAL,
            value=0.5,
            unit="USD billion",
            query_expression="a.fact_limit_usd_bn",
        ),
    ),
    coverage_prerequisites=(
        CoveragePrerequisite(
            name="programme-status-known",
            description="Current approval and marketing status are explicitly known.",
            required_attributes=frozenset({"status", "marketed"}),
        ),
    ),
    material_arguments=("programme_key", "currency"),
    priority=60,
    historical_precision=None,
    allowed_currencies=_USD_AMOUNT_SCOPE,
    owner=_OWNER,
    reviewer=_REVIEWER,
    deployment_state=PatternDeploymentState.ACTIVE,
    authorization_domain=_AUTHORIZATION_DOMAIN,
    query_fixture_ids=("gulf_meridian_dcm",),
    cypher_template=(
        """
        MATCH (a:Assertion {predicate: 'PROGRAMME_APPROVED_BY'})-[:SUBJECT]->(programme)
        MATCH (a)-[:OBJECT]->(org:Entity {node_type: 'Organization'})
        WHERE {pin:a} AND {fresh:a} AND {access:a}
          {materiality}
          {currency_scope:a}
          AND a.fact_status = 'approved'
          AND a.fact_marketed = false
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               programme.key AS programme_key, a.fact_currency AS currency,
               a.source_doc_id AS doc, a.properties_json AS props,
               [a.assertion_id] AS _assertion_ids,
               [a.source_id] AS _source_ids,
               [a.source_doc_id] AS _source_doc_ids,
               [a.barrier_side] AS _barrier_sides,
               a.recorded_at AS _latest_recorded_at,
               CASE WHEN a.fact_limit_usd_bn >= $materiality_threshold_0 * 3.0 THEN 1.0
                    ELSE a.fact_limit_usd_bn / ($materiality_threshold_0 * 3.0)
                    END AS _materiality_score,
               a.confidence AS _evidence_confidence
        """
    ),
)

AT1_CALL = Pattern(
    name="at1_call_approaching_no_refi",
    version="3.1.0",
    precision_lineage="3",
    hypothesis=(
        "A material approaching AT1 call with complete refinancing coverage can precede issuance."
    ),
    eligible_outcome_kinds=_MANDATE_OUTCOMES,
    required_claim_types=frozenset({EdgeType.CALLABLE_ON, EdgeType.ISSUES}),
    required_attributes=frozenset({"class", "amount_usd_mn", "currency"}),
    freshness_days=180,
    prediction_horizon_days=548,
    materiality_thresholds=(
        MaterialityThreshold(
            attribute="amount_usd_mn",
            operator=MaterialityOperator.GREATER_THAN_OR_EQUAL,
            value=250.0,
            unit="USD million",
            query_expression="a.fact_amount_usd_mn",
        ),
    ),
    coverage_prerequisites=(
        CoveragePrerequisite(
            name="refinancing-search-complete",
            description="Authorized refinancing coverage is complete for the AT1 instrument.",
            scope=CoverageScope.SOURCE_OPERATIONS,
        ),
        CoveragePrerequisite(
            name="instrument-factual-coverage-complete",
            description=(
                "The declared issuer, exchange, regulator, rating and news universe was "
                "reconciled for this instrument and time window."
            ),
            scope=CoverageScope.FACTUAL_ENTITY,
        ),
    ),
    material_arguments=("instrument", "instrument_class", "currency"),
    priority=65,
    historical_precision=None,
    allowed_currencies=_USD_AMOUNT_SCOPE,
    owner=_OWNER,
    reviewer=_REVIEWER,
    deployment_state=PatternDeploymentState.PILOT,
    authorization_domain=_AUTHORIZATION_DOMAIN,
    query_fixture_ids=("gulf_meridian_dcm",),
    cypher_template=(
        """
        MATCH (org:Entity {node_type: 'Organization'})
        MATCH (a:Assertion {predicate: 'CALLABLE_ON'})-[:SUBJECT]->(inst:Entity)
        MATCH (ai:Assertion {predicate: 'ISSUES'})-[:SUBJECT]->(org)
        MATCH (ai)-[:OBJECT]->(inst)
        WHERE {pin:a} AND {pin:ai} AND {fresh:a} AND {fresh:ai}
          AND {access:a} AND {access:ai}
          AND a.fact_class = 'at1'
          {materiality}
          {currency_scope:a}
          AND datetime(a.valid_from) <= datetime($as_of) + duration({days: $window_days})
          AND datetime(a.valid_from) >= datetime($as_of)
          AND NOT EXISTS {
              MATCH (r:Assertion {predicate: 'REFINANCES'})-[:OBJECT]->(inst)
              WHERE {pin:r} AND {access:r}
          }
        RETURN org.key AS entity_key, org.display_name AS entity_name,
               inst.key AS instrument, a.fact_class AS instrument_class,
               a.fact_currency AS currency, a.source_doc_id AS doc,
               [a.assertion_id, ai.assertion_id] AS _assertion_ids,
               [a.source_id, ai.source_id] AS _source_ids,
               [a.source_doc_id, ai.source_doc_id] AS _source_doc_ids,
               [a.barrier_side, ai.barrier_side] AS _barrier_sides,
               CASE WHEN a.recorded_at >= ai.recorded_at
                    THEN a.recorded_at ELSE ai.recorded_at END AS _latest_recorded_at,
               CASE WHEN a.fact_amount_usd_mn >= $materiality_threshold_0 * 2.0 THEN 1.0
                    ELSE a.fact_amount_usd_mn / ($materiality_threshold_0 * 2.0)
                    END AS _materiality_score,
               (a.confidence + ai.confidence) / 2.0 AS _evidence_confidence
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
