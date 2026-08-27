"""Episode definitions for the synthetic corpus.

Two institutions, engineered to be hard to tell apart lexically and easy
to tell apart factually:

- Gulf Meridian Bank Q.P.S.C. — the positive episode. A sequence of events
  (rating outlook change, AT1 call approaching, treasurer departure, board
  programme approval, maturity wall) that historically precedes a DCM
  mandate. Detectors must fire here, before day 205 of the episode window.

- Northern Harbour Bank — the steady-state decoy. Superficially similar
  (Gulf-region bank, similar name rhythm, ordinary results, ordinary
  dividend) but with no pattern that precedes a mandate. A detector that
  fires here is broken even if it also fires on Gulf Meridian.

Ground truth is expressed as data so that backtests and negative tests
assert against the same source.
"""

from datetime import date

from pydantic import BaseModel, ConfigDict

EPISODE_START = date(2024, 1, 1)
#: Signals firing after this day-of-episode are too late to be useful.
SIGNAL_DEADLINE_DAY = 205

GULF_MERIDIAN_LEI = "213800GMBQPSC000000001"
NORTHERN_HARBOUR_LEI = "213800NHB00000000002"

#: Name variants that must all resolve to the Gulf Meridian LEI.
GULF_MERIDIAN_NAME_VARIANTS = (
    "Gulf Meridian Bank",
    "Gulf Meridian Bank Q.P.S.C.",
    "Gulf Meridian",
)

#: A genuinely different institution with a confusingly similar name.
#: Must never merge with Gulf Meridian.
GULF_MERIDIAN_CAPITAL_LEI = "213800GMCAPITAL000003"
GULF_MERIDIAN_CAPITAL_NAME = "Gulf Meridian Capital Partners"


class ExpectedSignal(BaseModel):
    """One signal the pattern library must fire for the positive episode."""

    model_config = ConfigDict(frozen=True)

    pattern: str
    entity_lei: str
    fires_by: date
    rationale: str


class Episode(BaseModel):
    model_config = ConfigDict(frozen=True)

    episode_id: str
    entity_lei: str
    entity_names: tuple[str, ...]
    is_decoy: bool
    expected_signals: tuple[ExpectedSignal, ...]


GULF_MERIDIAN = Episode(
    episode_id="gulf_meridian_dcm",
    entity_lei=GULF_MERIDIAN_LEI,
    entity_names=GULF_MERIDIAN_NAME_VARIANTS,
    is_decoy=False,
    expected_signals=(
        ExpectedSignal(
            pattern="negative_rating_action_with_capital_decline",
            entity_lei=GULF_MERIDIAN_LEI,
            fires_by=date(2024, 3, 1),
            rationale="Outlook revised to negative while CET1 fell year over year.",
        ),
        ExpectedSignal(
            pattern="leadership_change_treasury",
            entity_lei=GULF_MERIDIAN_LEI,
            fires_by=date(2024, 4, 15),
            rationale="Group treasurer departed; replacement not yet named.",
        ),
        ExpectedSignal(
            pattern="board_approved_issuance_programme",
            entity_lei=GULF_MERIDIAN_LEI,
            fires_by=date(2024, 5, 1),
            rationale="Board approved a USD 1.5bn EMTN programme update.",
        ),
        ExpectedSignal(
            pattern="maturity_wall_no_refi",
            entity_lei=GULF_MERIDIAN_LEI,
            fires_by=date(2024, 6, 1),
            rationale="USD 500m sukuk matures within 12 months; no refinancing announced.",
        ),
    ),
)

NORTHERN_HARBOUR = Episode(
    episode_id="steady_state_decoy",
    entity_lei=NORTHERN_HARBOUR_LEI,
    entity_names=("Northern Harbour Bank",),
    is_decoy=True,
    expected_signals=(),
)
