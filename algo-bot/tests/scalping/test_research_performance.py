"""Observe-only research stamps and performance join — no live hard gates."""

from __future__ import annotations

import pytest

from app.scalping.models import (
  ARCHETYPE_BREAKOUT_RETEST,
  ARCHETYPE_IMPULSE_PULLBACK,
  ARCHETYPE_RANGE_SWEEP,
  OPPORTUNITY_VERSION,
  ScalpOpportunity,
)
from app.scalping.performance import (
  aggregate_performance_rows,
  rows_from_math_shadow,
)
from app.scalping.research_stamp import (
  annotate_opportunity_research,
  research_agree_rows,
)


pytestmark = pytest.mark.no_database


def _opp(
  *,
  archetype: str = ARCHETYPE_RANGE_SWEEP,
  direction: str = "BUY",
  measured: dict | None = None,
) -> ScalpOpportunity:
  return ScalpOpportunity(
    version=OPPORTUNITY_VERSION,
    opportunity_id="opp1",
    context_id="ctx1",
    symbol="XAU",
    archetype=archetype,
    direction=direction,
    discovered_at=100,
    source_bar_ts=90,
    zone_low=4000.0,
    zone_high=4005.0,
    key_level=4002.0,
    trigger_type="sweep_reclaim",
    trigger_bar_ts=90,
    trigger_price=4002.0,
    invalidation_price=3990.0,
    expected_target_price=4025.0,
    expected_target_pips=25.0,
    expected_stop_pips=15.0,
    expected_reward_risk=1.5,
    location_position=0.25,
    score=1.0,
    reasons=("test",),
    expires_at=400,
    episode_id="ep1",
    measured=dict(measured or {}),
  )


def _stamp(opp: ScalpOpportunity) -> ScalpOpportunity:
  return annotate_opportunity_research(
    opp,
    atr=4.0,
    range_low=4000.0,
    range_high=4050.0,
    nearest_resistance_low=4048.0,
    nearest_support_high=4000.0,
    bar_open=4003.0,
    bar_high=4004.0,
    bar_low=3999.0,
    bar_close=4002.5,
    spread=0.2,
    target_min_price=1.0,
    session="london",
    utc_hour=10,
  )


def test_research_stamp_attaches_features_without_mutating_core_fields():
  base = _opp()
  stamped = _stamp(base)
  assert stamped.opportunity_id == base.opportunity_id
  assert stamped.archetype == base.archetype
  assert stamped.direction == base.direction
  assert stamped.trigger_price == base.trigger_price
  assert "scalp_features" in stamped.measured
  assert "math_counterfactual" in stamped.measured
  assert stamped.measured["scalp_features"]["atr"] == pytest.approx(4.0)


def test_range_sweep_counterfactual_does_not_block_identity():
  stamped = _stamp(_opp())
  cf = stamped.measured["math_counterfactual"]
  assert cf["math_model"] == "liquidity_sweep_reversal"
  assert "allowed" in cf
  # Stamp is observe-only: opportunity object is still the live discovery.
  assert stamped.reasons == ("test",)


def test_breakout_stamps_math_model():
  stamped = _stamp(
    _opp(
      archetype=ARCHETYPE_BREAKOUT_RETEST,
      measured={
        "compression_box": {"box_low": 3990.0, "box_high": 4010.0},
        "breakout_evidence": {
          "accepted_break": True,
          "retest_rejection": True,
          "break_displacement": 3.0,
          "state": "armed",
        },
      },
    )
  )
  cf = stamped.measured["math_counterfactual"]
  assert cf["math_model"] == "breakout_retest_continuation"
  assert cf["allowed"] in {True, False}
  assert stamped.measured["math_agree"] is not None


def test_impulse_insufficient_inputs_without_origin():
  stamped = _stamp(_opp(archetype=ARCHETYPE_IMPULSE_PULLBACK))
  assert stamped.measured["math_counterfactual"]["reason_code"] == "insufficient_inputs"
  assert stamped.measured["math_would_allow"] is None


def test_impulse_counterfactual_with_origin_extreme():
  stamped = _stamp(
    _opp(
      archetype=ARCHETYPE_IMPULSE_PULLBACK,
      measured={"impulse_origin": 3990.0, "impulse_extreme": 4020.0},
    )
  )
  cf = stamped.measured["math_counterfactual"]
  assert cf["math_model"] == "impulse_pullback_continuation"
  assert cf["allowed"] in {True, False}


def test_research_agree_rows_and_performance_aggregate():
  stamped = _stamp(_opp())
  rows = research_agree_rows([stamped], session="london", bar_ts=123)
  assert len(rows) == 1
  assert rows[0]["live_discovered"] is True
  assert rows[0]["archetype"] == ARCHETYPE_RANGE_SWEEP

  report = aggregate_performance_rows([
    {
      "archetype": ARCHETYPE_RANGE_SWEEP,
      "session": "london",
      "math_agree": True,
      "math_reason": "sweep_reclaim_location_room_ok",
      "realized_pips": 12.0,
      "outcome": "tp",
    },
    {
      "archetype": ARCHETYPE_RANGE_SWEEP,
      "session": "london",
      "math_agree": False,
      "realized_pips": -8.0,
      "outcome": "sl",
    },
    {
      "archetype": ARCHETYPE_BREAKOUT_RETEST,
      "session": "london",
      "math_agree": None,
    },
  ])
  assert report["total_rows"] == 3
  assert report["rows_with_outcome"] == 2
  cells = {(c["archetype"], c["math_agree"]): c for c in report["table"]}
  assert cells[(ARCHETYPE_RANGE_SWEEP, "agree")]["wins"] == 1
  assert cells[(ARCHETYPE_RANGE_SWEEP, "disagree")]["losses"] == 1
  assert cells[(ARCHETYPE_BREAKOUT_RETEST, "unknown")]["count"] == 1
  assert cells[(ARCHETYPE_RANGE_SWEEP, "agree")]["expectancy_pips"] == pytest.approx(12.0)


def test_rows_from_math_shadow_payload():
  payload = {
    "session": "asia",
    "research": {
      "agree_rows": [
        {
          "archetype": ARCHETYPE_RANGE_SWEEP,
          "math_agree": True,
          "math_would_allow": True,
          "opportunity_id": "a",
        }
      ],
    },
  }
  rows = rows_from_math_shadow(payload)
  assert len(rows) == 1
  assert rows[0]["session"] == "asia"
  report = aggregate_performance_rows(rows)
  assert report["total_rows"] == 1
