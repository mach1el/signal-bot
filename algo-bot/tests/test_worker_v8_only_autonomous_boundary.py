"""Proves the autonomous cycle never re-acquires a V6 publish call site.

Per docs/adr-trade-plan-v8-cutover.md Section (Legacy autonomous removal),
TradePlan V8 is the sole autonomous order-creation path: worker.py's
autonomous per-bar entry point (_handle_event, which drives the
scanner-routed, private M1 range, and trend intents) must never call
_publish_strategy_match, _publish_candidate, or _publish_trend_candidate -
the three V6 candidate-building functions - regardless of any config value.
Those three functions still exist and are still directly unit-tested
elsewhere (test_auto_scalp_worker.py, test_worker_veto_regression_replay.py,
test_mapped_reaction_idempotency.py, test_mapped_thesis_lock.py,
test_forming_route_contract.py) for their internal guard/veto logic reuse -
this is a source-text check on the *autonomous wiring* only, not a claim
that the functions themselves are gone. A future edit that reintroduces an
autonomous call site fails this test immediately, mirroring
ctrader-engine's TradePlanExecutionEngineDependencyTests pattern for the
same boundary on the C# side.
"""

from __future__ import annotations

import inspect
import re

import pytest

from app.autotrade import worker

pytestmark = pytest.mark.no_database

_FORBIDDEN_CALLS = (
  "_publish_strategy_match(",
  "_publish_candidate(",
  "_publish_trend_candidate(",
)


def _strip_comments(source: str) -> str:
  return "\n".join(
    re.sub(r"#.*$", "", line) for line in source.split("\n")
  )


@pytest.mark.parametrize("forbidden_call", _FORBIDDEN_CALLS)
def test_autonomous_cycle_never_calls_v6_publish_functions(forbidden_call):
  source = _strip_comments(inspect.getsource(worker._handle_event))
  assert forbidden_call not in source


def test_publish_trade_plan_v8_no_longer_gates_on_contract_mode():
  source = inspect.getsource(worker._publish_trade_plan_v8)
  assert "contract_mode" not in source
  assert "auto_trade_contract_mode" not in source


def test_v6_publish_functions_still_exist_for_their_direct_unit_tests():
  # If these were ever actually deleted, the still-existing direct-caller
  # test files (test_auto_scalp_worker.py, test_worker_veto_regression_
  # replay.py, test_mapped_reaction_idempotency.py, test_mapped_thesis_
  # lock.py, test_forming_route_contract.py) would fail to import/collect -
  # this test names that intent explicitly rather than relying on collection
  # failure to surface it.
  assert callable(worker._publish_strategy_match)
  assert callable(worker._publish_candidate)
  assert callable(worker._publish_trend_candidate)
