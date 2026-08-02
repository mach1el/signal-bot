"""Phase 2D2 selectable-runtime architecture guards."""

import ast
from pathlib import Path

import pytest

import app.core.config as active_config
from app.core.config import Settings


pytestmark = pytest.mark.no_database

_ROOT = Path(__file__).parents[2]
_APP = Path(__file__).parents[1] / "app"
_ACTIVATION_MODULES = {
  "app.configuration.facade",
  "app.configuration.authority",
  "app.configuration.activation_rehearsal",
  "app.configuration.readiness",
}


def _imports(path):
  tree = ast.parse(path.read_text(encoding="utf-8"))
  result = set()
  for node in ast.walk(tree):
    if isinstance(node, ast.Import):
      result.update(item.name for item in node.names)
    elif isinstance(node, ast.ImportFrom) and node.module:
      result.add(node.module)
  return result


def test_active_config_uses_strict_bootstrap_and_production_loader():
  imports = _imports(_APP / "core/config.py")
  assert "app.configuration.bootstrap_authority" in imports
  assert "app.configuration.python_loader" in imports
  source = (_APP / "core/config.py").read_text(encoding="utf-8")
  assert "settings = _ACTIVE_CONFIGURATION.settings" in source
  assert "CanonicalSettingsFacade(" not in source


def test_main_does_not_import_activation_modules():
  assert not _imports(_APP / "main.py") & _ACTIVATION_MODULES


def test_trading_modules_do_not_import_facade():
  for path in _APP.rglob("*.py"):
    if "configuration" in path.parts:
      continue
    imports = _imports(path)
    assert "app.configuration.facade" not in imports, path
    assert "app.configuration.authority" not in imports, path
    assert "app.configuration.activation_rehearsal" not in imports, path


def test_active_settings_remains_legacy_instance():
  assert isinstance(active_config.settings, Settings)
  assert type(active_config.settings) is Settings


def test_production_consumers_do_not_read_generated_contracts():
  for path in _APP.rglob("*.py"):
    if "configuration" in path.parts:
      continue
    if path == _APP / "core/config.py":
      continue
    source = path.read_text(encoding="utf-8")
    assert ".generated.json" not in source, path
    assert "configuration.generated" not in source, path


def test_facade_construction_is_confined_to_rehearsal_package():
  for path in _APP.rglob("*.py"):
    if "configuration" in path.parts:
      continue
    assert "CanonicalSettingsFacade(" not in path.read_text(encoding="utf-8"), path


def test_canonical_rehearsal_is_not_requested_by_production_consumers():
  for path in _APP.rglob("*.py"):
    if "configuration" in path.parts:
      continue
    assert "CANONICAL_REHEARSAL" not in path.read_text(encoding="utf-8"), path


def test_activation_rehearsal_has_no_import_time_execution():
  for name in ("authority.py", "activation_rehearsal.py", "readiness.py"):
    tree = ast.parse((_APP / "configuration" / name).read_text(encoding="utf-8"))
    for node in tree.body:
      if not isinstance(node, (ast.Assign, ast.AnnAssign, ast.Expr)):
        continue
      calls = [child for child in ast.walk(node) if isinstance(child, ast.Call)]
      assert not any(
        getattr(call.func, "id", "").startswith(("build_", "run_", "rehearse_"))
        for call in calls
      ), name


def test_only_production_authority_values_are_documented():
  paths = (
    _ROOT / ".env.example",
    _ROOT / "docker-compose.yml",
    _ROOT / "deployment-template/docker-compose.yml.j2",
  )
  for path in paths:
    source = path.read_text(encoding="utf-8")
    assert "APEXVOID_CONFIG_AUTHORITY" in source
    assert "CANONICAL_REHEARSAL" not in source


def test_runtime_binding_surfaces_do_not_reference_phase_2d1():
  paths = [
    *(_ROOT / "ctrader-engine").rglob("*.cs"),
    *(_ROOT / ".github/workflows").glob("*"),
    _ROOT / "docker-compose.yml",
    _ROOT / "deployment-template/docker-compose.yml.j2",
  ]
  for path in paths:
    if not path.is_file():
      continue
    source = path.read_text(encoding="utf-8")
    assert "CanonicalSettingsFacade" not in source, path
    assert "CANONICAL_REHEARSAL" not in source, path
