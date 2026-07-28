"""TradePlan V7 events must render, not crash Telegram delivery.

Found while wiring TradePlanRuntime.cs (Section M of the V7 cutover):
render_auto_trade_event's fallback branch does an unguarded
labels[event_type] dict lookup with no .get() default - any event type
without an entry raises KeyError. The C# runtime emits five new V7-only
types (plan_armed, order_submitted, order_filled, tp_booked, sl_moved)
that did not previously exist in that dict.
"""

from __future__ import annotations

from app.autotrade import delivery


def test_plan_armed_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "plan_armed",
    "message": "PLAN ARMED Trend Pullback BUY (market_watch)",
  })

  assert text is not None
  assert "PLAN ARMED" in text


def test_v7_order_submitted_event_renders_without_crashing():
  # "order_submitted" (no v7_ prefix) is already claimed by the V6
  # lifecycle as an always-silent type (TELEGRAM_SILENT_LIFECYCLE_TYPES) -
  # confirm it stays silent, and that the V7-distinct name renders.
  silent = delivery.render_auto_trade_event({
    "type": "order_submitted",
    "message": "should stay silent",
  })
  assert silent is None

  text = delivery.render_auto_trade_event({
    "type": "v7_order_submitted",
    "message": "ORDER SUBMITTED BUY 2 leg(s)",
  })

  assert text is not None
  assert "ORDER SUBMITTED" in text


def test_order_filled_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "order_filled",
    "message": "ORDER FILLED BUY 1300 @ 4089.10",
  })

  assert text is not None
  assert "ORDER FILLED" in text


def test_tp_booked_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "tp_booked",
    "message": "TP BOOKED TP1 @ 4096.00",
  })

  assert text is not None
  assert "TP BOOKED" in text


def test_sl_moved_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "sl_moved",
    "message": "SL MOVED to 4089.10 (BE)",
  })

  assert text is not None
  assert "SL MOVED" in text


def test_plan_rejected_event_renders_without_crashing():
  text = delivery.render_auto_trade_event({
    "type": "plan_rejected",
    "message": "insufficient_target_room",
  })

  assert text is not None
  assert "PLAN REJECTED" in text
