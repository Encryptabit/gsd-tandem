"""Tests for broker logfile configuration in server logging setup."""

from __future__ import annotations

import json
import logging
from pathlib import Path

from gsd_review_broker import server


def _reset_broker_logger_handlers() -> None:
    logger = logging.getLogger("gsd_review_broker")
    for handler in list(logger.handlers):
        logger.removeHandler(handler)
        handler.close()


def test_configure_logging_writes_structured_broker_log(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_broker_logger_handlers()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))

    server._configure_logging()
    logger = logging.getLogger("gsd_review_broker")
    token = server.caller_tag.set("broker-test")
    try:
        logger.info("broker log entry")
    finally:
        server.caller_tag.reset(token)
        _reset_broker_logger_handlers()

    log_path = tmp_path / "xdg" / "gsd-review-broker" / "broker-logs" / "broker.jsonl"
    assert log_path.exists()
    lines = [line for line in log_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    assert lines
    payload = json.loads(lines[-1])
    assert payload["message"] == "broker log entry"
    assert payload["caller_tag"] == "broker-test"
    assert payload["level"] == "info"


def test_configure_logging_rotates_broker_log(
    monkeypatch,
    tmp_path: Path,
) -> None:
    _reset_broker_logger_handlers()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "xdg"))
    monkeypatch.setenv("BROKER_LOG_MAX_BYTES", "1024")
    monkeypatch.setenv("BROKER_LOG_BACKUPS", "2")

    server._configure_logging()
    logger = logging.getLogger("gsd_review_broker")
    for idx in range(3):
        logger.info("x" * 900 + f"-{idx}")
    _reset_broker_logger_handlers()

    base = tmp_path / "xdg" / "gsd-review-broker" / "broker-logs" / "broker.jsonl"
    assert base.exists()
    assert Path(f"{base}.1").exists()
