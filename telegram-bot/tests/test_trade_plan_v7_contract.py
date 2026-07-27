"""Python half of the shared TradePlan V7 contract fixture.

The same file is read by `ctrader-engine/tests/TradePlanV7ContractTests.cs`, so
the plan Python builds and the plan C# parses/validates are held to one table.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest

from app.autotrade.trade_plan import TradePlan, TradePlanError

pytestmark = pytest.mark.no_database

FIXTURE = (
  Path(__file__).resolve().parents[2]
  / "contracts"
  / "autotrade"
  / "trade-plan-v7.json"
)


def _fixture() -> dict:
  return json.loads(FIXTURE.read_text())


def _valid_plans() -> dict[str, dict]:
  return {case["name"]: case["plan"] for case in _fixture()["valid_plans"]}


def _apply_override(plan: dict, path: str, value: Any) -> None:
  parts = path.split(".")
  node = plan
  for part in parts[:-1]:
    node = node[int(part)] if part.isdigit() else node[part]
  last = parts[-1]
  if last.isdigit():
    node[int(last)] = value
  else:
    node[last] = value


def _build_invalid_plan(case: dict) -> dict:
  overrides = case["plan_overrides"]
  plan = copy.deepcopy(_valid_plans()[overrides["base"]])
  for path, value in overrides.items():
    if path == "base":
      continue
    _apply_override(plan, path, value)
  return plan


@pytest.mark.parametrize("case", _fixture()["valid_plans"], ids=lambda c: c["name"])
def test_shared_valid_plan_round_trips(case):
  plan = TradePlan.from_dict(case["plan"])
  assert plan.plan_id == case["plan"]["plan_id"]
  assert plan.to_dict()["plan_id"] == case["plan"]["plan_id"]
  assert plan.to_dict()["version"] == 7


@pytest.mark.parametrize("case", _fixture()["invalid_plans"], ids=lambda c: c["name"])
def test_shared_invalid_plan_rejected(case):
  payload = _build_invalid_plan(case)
  with pytest.raises(TradePlanError, match=case["error"]):
    TradePlan.from_dict(payload)


def test_market_watch_entry_prices_are_the_zone_edges():
  plan = TradePlan.from_dict(_valid_plans()["market_watch_buy"])
  assert plan.entry.entry_prices() == (plan.entry.zone_low, plan.entry.zone_high)


def test_limit_ladder_entry_prices_are_the_leg_prices():
  plan = TradePlan.from_dict(_valid_plans()["limit_ladder_buy"])
  assert plan.entry.entry_prices() == tuple(leg.price for leg in plan.entry.legs)


def test_plan_has_no_planned_star_ambiguous_fields():
  # The whole point of V7 is that there is exactly one stop and one route,
  # not a family of planned/base/final variants for something else to
  # recompute and compare against.
  plan = TradePlan.from_dict(_valid_plans()["market_watch_buy"])
  serialized = json.dumps(plan.to_dict())
  assert "planned_" not in serialized
  assert "stop_adjustment" not in serialized
