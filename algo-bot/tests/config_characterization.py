"""Frozen Phase 2A characterization fixtures loaded from the snapshot file.

Historical catalog-parity helpers. Not a Settings compatibility layer.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any


_SNAPSHOT_PATH = (
  Path(__file__).parent / "fixtures" / "config-phase-2a-characterization.json"
)


def load_snapshot() -> dict[str, Any]:
  return json.loads(_SNAPSHOT_PATH.read_text(encoding="utf-8"))


def direct_conservative_fixture() -> dict[str, Any]:
  return dict(load_snapshot()["fixtures"]["direct_conservative"])


def direct_demo_eval_fixture() -> dict[str, Any]:
  return dict(load_snapshot()["fixtures"]["direct_demo_eval"])


def root_compose_demo_fixture() -> dict[str, Any]:
  return dict(load_snapshot()["fixtures"]["root_compose_demo_eval"])


def test_conftest_fixture() -> dict[str, Any]:
  return dict(load_snapshot()["fixtures"]["test_conftest"])
