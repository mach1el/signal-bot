"""Repository and synthetic AST coverage for legacy Settings usage."""

from pathlib import Path

import pytest

from app.configuration.usage_audit import audit_legacy_settings_usage


pytestmark = pytest.mark.no_database

_ROOT = Path(__file__).parents[2]


def _all_rows(report, section):
  return [
    row
    for bucket, rows in report[section].items()
    for row in rows
  ]


def test_legacy_usage_audit_is_deterministic():
  assert audit_legacy_settings_usage(_ROOT) == audit_legacy_settings_usage(_ROOT)


def test_legacy_usage_audit_tracks_import_aliases(tmp_path):
  source = tmp_path / "algo-bot/app/example.py"
  source.parent.mkdir(parents=True)
  source.write_text(
    "from app.core.config import settings as legacy\nvalue = legacy.log_level\n",
    encoding="utf-8",
  )
  report = audit_legacy_settings_usage(tmp_path)
  reads = report["production"]["attribute_reads"]
  assert [(item["attribute"], item["classification"]) for item in reads] == [
    ("log_level", "facade_supported"),
  ]


def test_legacy_usage_audit_classifies_reads_and_writes(tmp_path):
  source = tmp_path / "algo-bot/app/example.py"
  source.parent.mkdir(parents=True)
  source.write_text(
    "from app.core.config import settings\n"
    "value = settings.log_level\n"
    "settings.log_level = 'DEBUG'\n",
    encoding="utf-8",
  )
  report = audit_legacy_settings_usage(tmp_path)
  assert report["production"]["attribute_reads"][0]["classification"] == "facade_supported"
  assert report["production"]["attribute_writes"][0]["classification"] == "unsafe_mutation"


def test_no_unclassified_production_settings_usage():
  report = audit_legacy_settings_usage(_ROOT)
  rows = _all_rows(report, "production")
  assert rows
  assert all(row["classification"] for row in rows)
  assert report["activation_blockers"] == []


def test_legacy_usage_audit_classifies_introspection_and_type_dependencies(tmp_path):
  source = tmp_path / "algo-bot/tests/example.py"
  source.parent.mkdir(parents=True)
  source.write_text(
    "from app.core.config import Settings, settings\n"
    "dump = settings.model_dump()\n"
    "fields = Settings.model_fields\n"
    "same_type = isinstance(settings, Settings)\n",
    encoding="utf-8",
  )
  report = audit_legacy_settings_usage(tmp_path)
  assert {item["operation"] for item in report["tests"]["method_calls"]} == {
    "method:model_dump",
  }
  assert {item["operation"] for item in report["tests"]["type_dependencies"]} == {
    "isinstance",
    "settings_class_attribute",
  }


def test_production_settings_mutation_is_activation_blocker(tmp_path):
  source = tmp_path / "algo-bot/app/example.py"
  source.parent.mkdir(parents=True)
  source.write_text(
    "from app.core.config import settings\nsetattr(settings, 'log_level', 'DEBUG')\n",
    encoding="utf-8",
  )
  report = audit_legacy_settings_usage(tmp_path)
  assert report["activation_blockers"][0]["classification"] == "unsafe_mutation"
