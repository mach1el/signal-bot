"""Non-authoritative dual-loader rehearsal and readiness gate tests."""

from dataclasses import replace
import inspect

import pytest

from app.configuration.activation_rehearsal import VerificationEvidence
from app.configuration.activation_rehearsal import run_activation_rehearsal
from app.configuration.authority import ConfigurationAuthority
from app.configuration.readiness import ActivationBlockerCode
from app.configuration.readiness import ActivationEvidence
from app.configuration.readiness import evaluate_activation_readiness
from app.configuration.readiness import issue_rehearsal_permit
from app.configuration.source_types import ConfigurationSourceBundle
from app.core import config as active_config
from tests.test_config_shadow_parity import _fixtures
from tests.test_config_shadow_parity import _legacy


pytestmark = pytest.mark.no_database

_ROOT = __import__("pathlib").Path(__file__).parents[2]


def _passing_evidence():
  return ActivationEvidence(**{
    name: True for name in ActivationEvidence.__dataclass_fields__
  })


def _rehearsal(verification=VerificationEvidence()):
  environment = _fixtures()["direct_conservative"]
  return run_activation_rehearsal(
    source_bundle=ConfigurationSourceBundle(process_environment=environment),
    legacy_settings=_legacy(environment),
    active_global_settings=active_config.settings,
    repository_root=_ROOT,
    verification=verification,
  )


def test_readiness_requires_blocking_evidence():
  passing = _passing_evidence()
  assert evaluate_activation_readiness(passing).ready
  informational = {"python_behavior_tests_passed", "csharp_tests_verified"}
  for name in set(ActivationEvidence.__dataclass_fields__) - informational:
    result = evaluate_activation_readiness(replace(passing, **{name: False}))
    assert not result.ready, name
    assert result.blockers, name


def test_current_readiness_is_ready_with_informational_warnings():
  result = _rehearsal(VerificationEvidence(
    python_configuration_tests_passed=True,
    python_behavior_baseline_not_worsened=True,
  ))
  assert result.success
  assert result.readiness.ready is True


def test_readiness_does_not_require_full_behavior_suite_green():
  evidence = replace(_passing_evidence(), python_behavior_tests_passed=False)
  readiness = evaluate_activation_readiness(evidence)
  assert readiness.ready
  assert "PYTHON_BEHAVIOR_TESTS_NOT_GREEN" in readiness.warnings


def test_readiness_does_not_require_csharp_tests():
  evidence = replace(_passing_evidence(), csharp_tests_verified=False)
  readiness = evaluate_activation_readiness(evidence)
  assert readiness.ready
  assert "CSHARP_TESTS_NOT_RUN" in readiness.warnings


def test_readiness_requires_configuration_tests():
  evidence = replace(_passing_evidence(), python_configuration_tests_passed=False)
  assert ActivationBlockerCode.CONFIGURATION_TESTS_FAILED in evaluate_activation_readiness(evidence).blockers


def test_readiness_requires_baseline_not_worsened():
  evidence = replace(_passing_evidence(), python_behavior_baseline_not_worsened=False)
  assert ActivationBlockerCode.PYTHON_BEHAVIOR_BASELINE_WORSENED in evaluate_activation_readiness(evidence).blockers


def test_readiness_requires_rollback():
  evidence = replace(_passing_evidence(), rollback_rehearsal_passed=False)
  assert ActivationBlockerCode.ROLLBACK_REHEARSAL_FAILED in evaluate_activation_readiness(evidence).blockers


def test_readiness_reports_nonblocking_warnings():
  evidence = replace(
    _passing_evidence(),
    python_behavior_tests_passed=False,
    csharp_tests_verified=False,
  )
  readiness = evaluate_activation_readiness(evidence)
  assert readiness.ready
  assert readiness.warnings == (
    "PYTHON_BEHAVIOR_TESTS_NOT_GREEN",
    "CSHARP_TESTS_NOT_RUN",
  )


def test_passing_synthetic_evidence_can_issue_permit():
  permit = issue_rehearsal_permit(_passing_evidence())
  assert permit.authority is ConfigurationAuthority.CANONICAL_REHEARSAL
  assert permit.production_activation_allowed is False


def test_no_force_activation_bypass_exists():
  parameters = inspect.signature(issue_rehearsal_permit).parameters
  assert set(parameters) == {"evidence"}


def test_dual_loader_rehearsal_compares_all_fields():
  result = _rehearsal()
  assert (result.direct_parity_equal, result.direct_parity_total) == (316, 316)
  assert (result.derived_parity_equal, result.derived_parity_total) == (4, 4)
  assert (result.facade_parity_equal, result.facade_parity_total) == (320, 320)
  assert result.unsupported_usage_count == 0
  assert result.success


def test_activation_rehearsal_is_non_authoritative():
  result = _rehearsal()
  assert result.authoritative is False
  assert result.facade is not None


def test_activation_rehearsal_does_not_mutate_global_state():
  original = active_config.settings
  result = _rehearsal()
  assert result.rollback_result.global_state_untouched
  assert active_config.settings is original
