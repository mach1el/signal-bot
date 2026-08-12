"""Unit tests for scalp vs reaction funnel helpers."""

from __future__ import annotations

import pytest

from app.autotrade.reaction_funnel import (
  BUCKET_REACTION,
  BUCKET_SCALP,
  funnel_bucket,
)
from app.autotrade.stats_ingestion import _complete_outcome


pytestmark = pytest.mark.no_database


def test_funnel_bucket_splits_scalp_and_reaction():
  assert funnel_bucket("Key Level Reaction") == BUCKET_REACTION
  assert funnel_bucket("Trendline Reaction") == BUCKET_REACTION
  assert funnel_bucket("HFS Impulse Pullback", family="hfs") == BUCKET_SCALP
  assert funnel_bucket("Range Edge Scalp") == BUCKET_SCALP


@pytest.mark.parametrize(
  ("event", "expected"),
  [
    ({"type": "order_filled"}, "fill"),
    ({"type": "opened"}, "fill"),
    ({"type": "group_result", "reason_code": "group_stop_loss"}, "sl"),
    ({"type": "group_result", "reason_code": "take_profit"}, "tp"),
    ({"type": "group_result", "group_realized_pips": -12.5}, "sl"),
    ({"type": "group_result", "group_realized_pips": 40.0}, "tp"),
    ({"type": "setup_status"}, None),
  ],
)
def test_complete_outcome_classification(event, expected):
  assert _complete_outcome(event) == expected
