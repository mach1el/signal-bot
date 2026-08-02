"""Non-authoritative shadow loading and base parity tests."""

from unittest.mock import patch

import pytest

from app.configuration.parity import compare_legacy_settings
from app.configuration.shadow_loader import load_shadow_configuration
from app.configuration.source_types import ConfigurationSourceBundle
from app.configuration.source_types import ShadowLoadStatus
from app.core.config import Settings


pytestmark = pytest.mark.no_database

_COMPLETE = {
  "TELEGRAM_BOT_TOKEN": "phase-2c-shadow-token",
  "SIGNAL_VIP_CHANNEL_ID": "-100123456789",
  "POSTGRES_PASSWORD": "phase-2c-postgres-sentinel",
  "CTRADER_ACCESS_TOKEN": "phase-2c-access-sentinel",
  "CTRADER_ACCOUNT_ID": "123456",
  "CTRADER_CLIENT_ID": "phase-2c-client-id",
  "CTRADER_CLIENT_SECRET": "phase-2c-client-secret-sentinel",
  "CTRADER_REFRESH_TOKEN": "phase-2c-refresh-sentinel",
}


def _shadow(environment=None):
  return load_shadow_configuration(ConfigurationSourceBundle(
    process_environment={**_COMPLETE, **(environment or {})},
  ))


def test_shadow_result_is_never_authoritative():
  result = _shadow()
  assert result.success
  assert result.authoritative is False
  assert result.status is ShadowLoadStatus.COMPLETE


def test_incomplete_required_inputs_do_not_create_config():
  result = load_shadow_configuration(ConfigurationSourceBundle())
  assert result.status is ShadowLoadStatus.INCOMPLETE_REQUIRED_INPUT
  assert result.config is None
  assert set(result.missing_required_paths) == {
    "bootstrap.ctrader.credentials.access_token",
    "bootstrap.ctrader.credentials.account_id",
    "bootstrap.ctrader.credentials.client_id",
    "bootstrap.ctrader.credentials.client_secret",
    "bootstrap.ctrader.credentials.refresh_token",
    "bootstrap.postgres.password",
    "bootstrap.telegram.bot_token",
    "delivery.telegram.telegram_channel_id",
  }


def test_validation_errors_do_not_echo_secret_values():
  secret = "must-never-be-reported"
  result = _shadow({
    "CTRADER_CLIENT_SECRET": secret,
    "AUTO_TRADE_CONTRACT_MODE": "legacy_v6",
  })
  assert result.status is ShadowLoadStatus.INVALID
  assert secret not in repr(result)
  assert secret not in " ".join(result.validation_errors)


def test_direct_conservative_base_parity_316_of_316():
  environment = dict(_COMPLETE)
  with patch.dict("os.environ", environment, clear=True):
    legacy = Settings(_env_file=None)
  result = _shadow()
  report = compare_legacy_settings(legacy, result)
  assert (report.equal_count, report.total_count) == (316, 316), [
    row for row in report.rows if row.status.value != "equal"
  ]
