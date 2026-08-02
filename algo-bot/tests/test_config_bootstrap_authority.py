"""Strict bootstrap authority selector contracts."""

import pytest

from app.configuration.bootstrap_authority import (
  RuntimeConfigurationAuthority,
  resolve_runtime_configuration_authority,
)


pytestmark = pytest.mark.no_database


def test_runtime_authority_defaults_to_legacy():
  assert resolve_runtime_configuration_authority({}) is RuntimeConfigurationAuthority.LEGACY


def test_runtime_authority_accepts_legacy_case_insensitively():
  assert resolve_runtime_configuration_authority({
    "APEXVOID_CONFIG_AUTHORITY": "  LeGaCy ",
  }) is RuntimeConfigurationAuthority.LEGACY


def test_runtime_authority_accepts_canonical_case_insensitively():
  assert resolve_runtime_configuration_authority({
    "APEXVOID_CONFIG_AUTHORITY": " CANONICAL ",
  }) is RuntimeConfigurationAuthority.CANONICAL


def test_runtime_authority_rejects_unknown_value():
  with pytest.raises(ValueError, match="must be legacy or canonical"):
    resolve_runtime_configuration_authority({
      "APEXVOID_CONFIG_AUTHORITY": "canonical_rehearsal",
    })


def test_runtime_authority_rejects_explicit_blank():
  with pytest.raises(ValueError, match="cannot be blank"):
    resolve_runtime_configuration_authority({
      "APEXVOID_CONFIG_AUTHORITY": "  ",
    })
