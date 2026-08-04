"""Range Box Scalp scale-out plan and Telegram presentation."""

from __future__ import annotations

import pytest

from app.autotrade import delivery
from app.configuration.python_loader import (
  CanonicalConfigurationError,
  load_python_canonical_settings,
)
from app.configuration.source_types import ConfigurationSourceBundle


_SAFE = {
  "TELEGRAM_BOT_TOKEN": "123456:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghi",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "apexvoid",
}


def _load(**extra):
  return load_python_canonical_settings(ConfigurationSourceBundle(
    process_environment={**_SAFE, **{k: str(v) for k, v in extra.items()}},
  ))


def test_range_box_scale_out_config_validation():
  _load(
    AUTO_TRADE_RANGE_BOX_SCALE_OUT_ENABLED="true",
    AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS="70",
    AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS="30",
    AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION="0.5",
  )
  with pytest.raises(CanonicalConfigurationError):
    _load(
      AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS="70",
      AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS="70",
      AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION="0.5",
    )
  with pytest.raises(CanonicalConfigurationError):
    _load(
      AUTO_TRADE_RANGE_BOX_SCALE_OUT_THRESHOLD_PIPS="70",
      AUTO_TRADE_RANGE_BOX_SCALE_OUT_TRIGGER_PIPS="30",
      AUTO_TRADE_RANGE_BOX_SCALE_OUT_FRACTION="1.0",
    )

def test_opened_card_shows_tp1_and_full_tp_without_remaining_lot():
  text = delivery.render_auto_trade_event({
    "type": "opened",
    "setup": "Range Box Scalp",
    "mode": "auto_box_scalp",
    "targets_pips": [30, 110],
    "scale_out_fraction": 0.5,
    "message": (
      "BUY 0.10 lots filled 4000.20, SL 3993.70 · 65p structure · "
      "TP1 +30p book 50% · Full TP +110p · range 4,000.00-4,008.00 · sizing=min"
    ),
  })
  assert text is not None
  assert "ORDER FILLED" in text
  assert "TP1: <b>+30 pips</b> · book 50%" in text
  assert "Full TP: <b>+110 pips</b>" in text
  assert "lot" not in text.lower()
  assert "Remaining" not in text
  assert "$" not in text


def test_partial_scale_out_card_stays_minimal():
  partial = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "TP1 +30.0 pips closed volume 500",
    "daily_seq": 2,
    "volume": 500,
    "remaining_volume": 500,
    "group_initial_volume": 1000,
    "leg_realized_pips": 30.0,
    "group_realized_pips": 15.0,
    "lot_size": 10_000,
    "setup": "Range Box Scalp",
  })
  assert partial is not None
  assert "TP1 booked" in partial
  assert "Leg: <b>+30.0 pips</b>" in partial
  assert "Net so far" not in partial
  assert "Remaining" not in partial
  assert "lot" not in partial.lower()
  assert "$" not in partial


def test_full_tp_close_reports_its_own_leg_not_the_weighted_net():
  # 50% @ +30 and 50% @ +110 -> the closing leg itself is +110; the
  # volume-weighted +70 group total is no longer shown anywhere.
  final = delivery.render_auto_trade_event({
    "type": "take_profit",
    "message": "FULL TP +110.0 pips closed volume 500",
    "daily_seq": 2,
    "volume": 500,
    "remaining_volume": 0,
    "group_initial_volume": 1000,
    "leg_realized_pips": 110.0,
    "group_realized_pips": 70.0,
    "lot_size": 10_000,
    "setup": "Range Box Scalp",
  })
  assert final is not None
  assert "Leg: <b>+110.0 pips</b>" in final
  assert "Total net" not in final
  assert "Remaining" not in final
  assert "$" not in final
