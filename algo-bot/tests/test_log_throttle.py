import logging

from app.core import log_throttle as lt


def test_log_at_most_first_info_then_debug_then_info_again(caplog, monkeypatch):
  lt.reset_log_throttle()
  clock = {"t": 1000.0}
  monkeypatch.setattr(lt.time, "monotonic", lambda: clock["t"])

  logger = logging.getLogger("test.log_throttle")
  with caplog.at_level(logging.DEBUG, logger="test.log_throttle"):
    assert lt.log_at_most(logger, "k1", "hello %s", "a", interval_s=300) is True
    assert lt.log_at_most(logger, "k1", "hello %s", "b", interval_s=300) is False
    clock["t"] = 1300.0
    assert lt.log_at_most(logger, "k1", "hello %s", "c", interval_s=300) is True

  levels = [r.levelno for r in caplog.records if r.name == "test.log_throttle"]
  messages = [r.getMessage() for r in caplog.records if r.name == "test.log_throttle"]
  assert levels == [logging.INFO, logging.DEBUG, logging.INFO]
  assert messages == ["hello a", "hello b", "hello c"]


def test_log_at_most_independent_keys(caplog, monkeypatch):
  lt.reset_log_throttle()
  monkeypatch.setattr(lt.time, "monotonic", lambda: 50.0)
  logger = logging.getLogger("test.log_throttle.keys")
  with caplog.at_level(logging.INFO, logger="test.log_throttle.keys"):
    assert lt.log_at_most(logger, "a", "one") is True
    assert lt.log_at_most(logger, "b", "two") is True
  assert [r.getMessage() for r in caplog.records] == ["one", "two"]
