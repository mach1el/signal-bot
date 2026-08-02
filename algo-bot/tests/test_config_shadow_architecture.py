"""Architecture isolation and CLI redaction guards for Phase 2C."""

import ast
import json
from pathlib import Path

import pytest

import app.core.config as active_config
from app.core.config import Settings
from app.configuration.shadow_cli import main as shadow_cli_main


pytestmark = pytest.mark.no_database

_APP = Path(__file__).parents[1] / "app"


def _imports(path: Path) -> set[str]:
  tree = ast.parse(path.read_text(encoding="utf-8"))
  imported = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      imported.update(alias.name for alias in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      imported.add(node.module)
  return imported


def test_shadow_loader_not_imported_by_active_config():
  imports = _imports(_APP / "core/config.py")
  assert "app.configuration.resolver" not in imports
  assert "app.configuration.shadow_loader" not in imports


def test_shadow_loader_not_imported_by_main():
  imports = _imports(_APP / "main.py")
  assert "app.configuration.shadow_loader" not in imports


def test_shadow_loader_not_imported_by_trading_workers():
  for path in _APP.rglob("*.py"):
    if "configuration" in path.parts:
      continue
    assert "app.configuration.shadow_loader" not in _imports(path), path


def test_no_production_module_reads_generated_profile_artifact():
  for path in _APP.rglob("*.py"):
    if path.name == "generate.py":
      continue
    assert "profiles.generated.json" not in path.read_text(encoding="utf-8")


def test_shadow_loading_is_not_executed_at_module_import():
  source = (_APP / "configuration/shadow_loader.py").read_text(encoding="utf-8")
  tree = ast.parse(source)
  calls = [
    node for node in tree.body
    if isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr))
    and any(
      isinstance(child, ast.Call)
      and getattr(child.func, "id", None) == "load_shadow_configuration"
      for child in ast.walk(node)
    )
  ]
  assert calls == []


def test_active_settings_remains_legacy_instance():
  assert isinstance(active_config.settings, Settings)


def test_cli_summary_and_json_are_secret_safe(tmp_path, monkeypatch, capsys):
  secret = "phase-2c-cli-secret-must-not-leak"
  values = {
    "TELEGRAM_BOT_TOKEN": secret,
    "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
    "POSTGRES_PASSWORD": secret,
    "CTRADER_ACCESS_TOKEN": secret,
    "CTRADER_ACCOUNT_ID": "123456",
    "CTRADER_CLIENT_ID": "phase-2c-client-id",
    "CTRADER_CLIENT_SECRET": secret,
    "CTRADER_REFRESH_TOKEN": secret,
  }
  for key, value in values.items():
    monkeypatch.setenv(key, value)
  output = tmp_path / "shadow.json"
  assert shadow_cli_main([
    "--report-summary", "--report-sources", "--json-output", str(output),
  ]) == 0
  stdout = capsys.readouterr().out
  content = output.read_text(encoding="utf-8")
  assert "NON-AUTHORITATIVE SHADOW LOAD" in stdout
  assert secret not in stdout
  assert secret not in content
  assert json.loads(content)["authoritative"] is False
