"""TradePlan V7 events must render, not crash Telegram delivery.

Found while wiring TradePlanRuntime.cs (Section M of the V7 cutover):
render_auto_trade_event's fallback branch does an unguarded
labels[event_type] dict lookup with no .get() default - any event type
without an entry raises KeyError. The C# runtime emits five new V7-only
types (plan_armed, order_submitted, order_filled, tp_booked, sl_moved)
that did not previously exist in that dict.
"""

from __future__ import annotations

import pytest

from app.autotrade import delivery


pytestmark = pytest.mark.no_database


def test_plan_armed_event_stays_silent_and_does_not_crash():
  # PLAN ARMED as a separate user-visible idle-waiting phase is exactly
  # what the direct-publish model must not show - the plan card already
  # says PLAN PUBLISHED, and the owner reading a lingering ARMED card as
  # "still waiting" is precisely the confusion this must avoid. Moved into
  # TELEGRAM_SILENT_LIFECYCLE_TYPES; still must not crash on the lookup.
  text = delivery.render_auto_trade_event({
    "type": "plan_armed",
    "message": "PLAN ARMED Trend Pullback BUY (market_watch)",
  })

  assert text is None


def test_v7_order_submitted_event_renders_without_crashing():
  # "order_submitted" (no v7_ prefix) is already claimed by the V6
  # lifecycle as an always-silent type (TELEGRAM_SILENT_LIFECYCLE_TYPES) -
  # confirm it stays silent, and that the V7-distinct name is also silent
  # (owner does not want ORDERS SUBMITTED cards).
  silent = delivery.render_auto_trade_event({
    "type": "order_submitted",
    "message": "should stay silent",
  })
  assert silent is None

  text = delivery.render_auto_trade_event({
    "type": "v7_order_submitted",
    "message": "ORDERS SUBMITTED BUY L1=800 L2=300 (pending=1)",
  })

  assert text is None
  assert "v7_order_submitted" in delivery.TELEGRAM_SILENT_LIFECYCLE_TYPES


def test_event_setup_id_strips_v7_plan_prefix():
  assert delivery._event_match_id({
    "match_id": "abc123",
    "candidate_id": "v7:abc123",
  }) == "abc123"
  assert delivery._event_match_id({
    "candidate_id": "v7:setup-only",
  }) == "setup-only"
  assert delivery._event_match_id({
    "plan_id": "v7:from-plan",
  }) == "from-plan"


def test_order_filled_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "order_filled",
    "message": (
      "ENTRY GROUP FULLY FILLED BUY volume=1100 "
      "weighted=4074.1181818181818181818181818"
    ),
  })

  assert text is not None
  assert "ORDER FILLED" in text
  assert "lot=1100" in text
  assert "volume=" not in text
  assert "weighted=4074.12" in text
  assert "4074.118181818" not in text


def test_clean_message_formats_partial_fill_lot_and_price():
  cleaned = delivery._clean_message(
    "ENTRY L1 FILLED volume=800 @ 4074.6812345; L2 still pending"
  )
  assert "lot=800" in cleaned
  assert "volume=" not in cleaned
  assert "@ 4074.68" in cleaned
  assert "4074.6812345" not in cleaned


def test_tp_booked_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "tp_booked",
    "message": "TP1 COMPLETED TP1 closed L1=320 L2=120 remaining=660 (2/2)",
  })

  assert text is not None
  assert "TP COMPLETED" in text


def test_sl_moved_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "sl_moved",
    "message": "GROUP SL MOVED TO BE 4089.10 (2/2)",
  })

  assert text is not None
  assert "GROUP SL MOVED" in text


def test_plan_closed_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "position_closed",
    "message": "GROUP STOP LOSS (2/2)",
  })

  assert text is not None
  assert "PLAN CLOSED" in text or "GROUP STOP LOSS" in text


def test_plan_rejected_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "plan_rejected",
    "message": "insufficient_target_room",
  })

  assert text is not None
  assert "PLAN REJECTED" in text
