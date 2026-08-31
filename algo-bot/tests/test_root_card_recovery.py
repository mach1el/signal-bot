"""Root-card recovery helpers used when fill arrives without a Telegram root."""

from __future__ import annotations

from decimal import Decimal
from types import SimpleNamespace

import pytest

from app.autotrade.setup_card import strategy_match_from_trade_plan


pytestmark = pytest.mark.no_database


def test_strategy_match_from_trade_plan_builds_card_fields():
  plan = SimpleNamespace(
    setup_id="abc123",
    symbol="XAU",
    created_at=1_700_000_000,
    expires_at=1_700_000_600,
    analysis=SimpleNamespace(
      strategy="Impulse Pullback Scalp",
      strategy_family="scalp",
      direction="BUY",
      formation_timeframe="M1",
      confirmation_bar_ts=1_700_000_000,
      formation_bar_ts=1_700_000_000,
      confluence=3,
      reasons=("impulse_pullback_continuation",),
      bias="range",
    ),
    source_structure=SimpleNamespace(
      structure_id="struct1",
      kind="demand",
      timeframe="M1",
      low=Decimal("4405.0"),
      high=Decimal("4406.0"),
      invalidation_price=Decimal("4403.0"),
    ),
    entry=SimpleNamespace(
      zone_low=Decimal("4405.1"),
      zone_high=Decimal("4405.9"),
    ),
    targets=(SimpleNamespace(price=Decimal("4408.0")),),
  )
  match = strategy_match_from_trade_plan(plan)
  assert match.match_id == "abc123"
  assert match.strategy == "Impulse Pullback Scalp"
  assert match.direction == "BUY"
  assert match.entry_low == pytest.approx(4405.1)
  assert match.entry_high == pytest.approx(4405.9)
  assert match.structural_zone_id == "struct1"
