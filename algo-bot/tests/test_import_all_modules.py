"""Import every app module once — catches NameError / missing settings at CI."""

from __future__ import annotations

import importlib
import pkgutil

import pytest

import app


@pytest.mark.no_database
def test_every_module_imports_cleanly():
  failures = []
  for module in pkgutil.walk_packages(app.__path__, prefix="app."):
    try:
      importlib.import_module(module.name)
    except Exception as exc:  # noqa: BLE001 — the point is to catch everything
      failures.append(f"{module.name}: {type(exc).__name__}: {exc}")
  assert not failures, "modules failed to import:\n" + "\n".join(failures)
