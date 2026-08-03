"""Phase 2H characterization of the docker-compose environment consolidation.

The fixtures capture the pre-cleanup baseline (the bot inherited the shared
``auto-trade-environment`` YAML anchor). These tests assert the post-cleanup
compose structure: the anchor is renamed/scoped to the cTrader engine and the
bot declares only its explicit, deployment-critical overrides.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytestmark = pytest.mark.no_database

_REPO_ROOT = Path(__file__).resolve().parents[2]
_FIXTURES = Path(__file__).resolve().parent / "fixtures" / "phase2h"
_COMPOSE = _REPO_ROOT / "docker-compose.yml"


def _load(name: str) -> dict:
  return json.loads((_FIXTURES / name).read_text(encoding="utf-8"))


def _service_block(text: str, service: str) -> str:
  lines = text.splitlines()
  start = None
  for index, line in enumerate(lines):
    if line.rstrip() == f"  {service}:":
      start = index
      break
  assert start is not None, f"service {service} not found in compose"
  block: list[str] = []
  for line in lines[start + 1:]:
    # A new top-level service or section starts at two-space indent or column 0.
    if line and not line.startswith("   ") and line.strip():
      break
    block.append(line)
  return "\n".join(block)


def test_baseline_fixture_documents_anchor_inheritance():
  baseline = _load("baseline_note.json")
  assert baseline["bot_inherits_anchor"] is True
  assert baseline["anchor_merge_key"] == "<<: *auto-trade-environment"
  assert baseline["anchor_key_count"] == 90


def test_inventory_fixture_records_anchor_provenance():
  inventory = _load("compose_bot_env_inventory.json")
  assert inventory["bot_inherited_anchor"] is True
  assert inventory["anchor_key_count"] == 90
  # Every anchor key is marked as sourced from the anchor.
  from_anchor = inventory["bot_key_from_anchor"]
  assert all(from_anchor[key] for key in inventory["anchor_keys"])
  assert from_anchor["APEXVOID_CONFIG_AUTHORITY"] is False


def test_compose_anchor_renamed_and_scoped_to_ctrader():
  text = _COMPOSE.read_text(encoding="utf-8")
  assert "x-ctrader-auto-trade-environment: &ctrader-auto-trade-environment" in text
  assert "x-auto-trade-environment: &auto-trade-environment" not in text
  engine = _service_block(text, "ctrader-engine")
  assert "<<: *ctrader-auto-trade-environment" in engine


def test_bot_no_longer_inherits_anchor():
  text = _COMPOSE.read_text(encoding="utf-8")
  bot = _service_block(text, "bot")
  assert "<<:" not in bot, "bot must not merge any YAML anchor after Phase 2H"


def test_bot_pins_mapped_zone_and_guard_off():
  text = _COMPOSE.read_text(encoding="utf-8")
  bot = _service_block(text, "bot")
  assert "AUTO_TRADE_PROFILE:" in bot
  assert "AUTO_TRADE_MAPPED_ZONE_ENABLED:" in bot
  assert "AUTO_TRADE_MARKET_MAP_GUARD_ENABLED:" in bot
  assert "APEXVOID_CONFIG_AUTHORITY:" in bot
  # Phase 2I-A: managed-deployment default is canonical.
  assert "${APEXVOID_CONFIG_AUTHORITY:-canonical}" in bot
  # Mapped-zone execution route pinned off by default.
  assert "${AUTO_TRADE_MAPPED_ZONE_ENABLED:-false}" in bot


def test_known_differences_fixture_matches_compose_intent():
  known = _load("known_differences.json")
  assert known["AUTO_TRADE_MAPPED_ZONE_ENABLED"] == "false"
  assert known["AUTO_TRADE_MARKET_MAP_GUARD_ENABLED"] == "follows_mapped_zone"
