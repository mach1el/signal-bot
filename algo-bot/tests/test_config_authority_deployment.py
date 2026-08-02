"""Bootstrap authority deployment surface contracts."""

from pathlib import Path

import pytest


pytestmark = pytest.mark.no_database
_ROOT = Path(__file__).parents[2]


def test_env_example_documents_authority():
  text = (_ROOT / ".env.example").read_text(encoding="utf-8")
  assert "APEXVOID_CONFIG_AUTHORITY=legacy" in text


def test_compose_defaults_authority_to_legacy():
  text = (_ROOT / "docker-compose.yml").read_text(encoding="utf-8")
  assert "APEXVOID_CONFIG_AUTHORITY: ${APEXVOID_CONFIG_AUTHORITY:-legacy}" in text


def test_production_template_defaults_authority_to_legacy():
  text = (_ROOT / "deployment-template/docker-compose.yml.j2").read_text(encoding="utf-8")
  assert "'APEXVOID_CONFIG_AUTHORITY': 'legacy'" in text
  assert "combine(apexvoid_trading_bot_env)" in text
