"""Reproducible Python/C# verification and diagnostic-report tests."""

import pytest

from app.configuration.activation_verification import activation_verification_plan
from app.configuration.activation_verification import verify_csharp_locally


pytestmark = pytest.mark.no_database


def test_activation_verification_plan_covers_python_and_csharp():
  plan = activation_verification_plan()
  assert [item.surface for item in plan.python] == [
    "catalog",
    "generated_contracts",
    "phase_2d1_tests",
  ]
  assert [item.surface for item in plan.csharp] == [
    "dotnet_sdk",
    "ctrader_tests",
  ]
  assert any(
    "CTraderFeed.Tests.csproj" in token
    for item in plan.csharp
    for token in item.command
  )


def test_missing_dotnet_is_reported_honestly(tmp_path):
  result = verify_csharp_locally(
    tmp_path,
    executable_lookup=lambda _: None,
  )
  assert result.status == "not_verified_locally"
  assert result.sdk_available is False
  assert result.verified is False
  assert result.test_exit_codes == ()


def test_csharp_verification_requires_every_command_to_pass(tmp_path):
  class Result:
    def __init__(self, returncode):
      self.returncode = returncode

  codes = iter((0, 1))
  result = verify_csharp_locally(
    tmp_path,
    executable_lookup=lambda _: "/usr/bin/dotnet",
    runner=lambda *args, **kwargs: Result(next(codes)),
  )
  assert result.status == "failed"
  assert result.verified is False
  assert result.info_exit_code == 0
  assert result.test_exit_codes == (1,)
