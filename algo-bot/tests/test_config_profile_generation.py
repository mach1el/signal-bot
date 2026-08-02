"""Deterministic immutable profile artifact tests."""

import json
from pathlib import Path

import pytest

from app.configuration.generate import REPOSITORY_ROOT
from app.configuration.generate import render_artifacts


pytestmark = pytest.mark.no_database

_PROFILE_PATH = Path("contracts/configuration/profiles.generated.json")


def test_profiles_generated_artifact_is_current():
  expected = render_artifacts()[_PROFILE_PATH]
  assert (REPOSITORY_ROOT / _PROFILE_PATH).read_bytes() == expected


def test_profiles_generated_artifact_is_deterministic():
  first = render_artifacts()[_PROFILE_PATH]
  second = render_artifacts()[_PROFILE_PATH]
  assert first == second
  artifact = json.loads(first)
  counts = {
    item["name"]: item["assignment_count"]
    for item in artifact["profiles"]
  }
  assert counts == {"conservative": 0, "demo_eval": 48}


def test_profiles_generated_artifact_contains_no_secrets():
  content = render_artifacts()[_PROFILE_PATH].decode("utf-8")
  assert "TOKEN" not in content.upper()
  assert "PASSWORD" not in content.upper()
  assert "SECRET" not in content.upper()
  assert "phase-2c" not in content
