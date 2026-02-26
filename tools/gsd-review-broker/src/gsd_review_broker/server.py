"""FastMCP server entry point for the GSD Review Broker."""

from __future__ import annotations

import contextvars
import json
import logging
import os
import sys
from datetime import UTC, datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path

from fastmcp import FastMCP

from gsd_review_broker.db import broker_lifespan

USER_CONFIG_DIRNAME = "gsd-review-broker"
BROKER_LOG_DIR_ENV_VAR = "BROKER_LOG_DIR"
BROKER_LOG_MAX_BYTES_ENV_VAR = "BROKER_LOG_MAX_BYTES"
BROKER_LOG_BACKUPS_ENV_VAR = "BROKER_LOG_BACKUPS"
DEFAULT_BROKER_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_BROKER_LOG_BACKUPS = 5

mcp = FastMCP(
    "gsd-review-broker",
    instructions=(
        "Review broker for GSD tandem pairing. "
        "Manages review lifecycle between proposer and reviewer."
    ),
    lifespan=broker_lifespan,
)

# ContextVar holding the caller identity for log lines.
# Default "broker" is used for internal/system actions.
caller_tag: contextvars.ContextVar[str] = contextvars.ContextVar("caller_tag", default="broker")

# Import tools to register them with @mcp.tool.
# This import MUST come AFTER mcp is created to avoid circular imports.
from gsd_review_broker import tools  # noqa: F401, E402


class _CallerFormatter(logging.Formatter):
    """Log formatter that injects the caller_tag ContextVar into each record."""

    def format(self, record: logging.LogRecord) -> str:
        record.caller_tag = caller_tag.get("broker")  # type: ignore[attr-defined]
        return super().format(record)


class _JsonFormatter(logging.Formatter):
    """Structured JSON formatter for broker logfile events."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, object] = {
            "ts": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
            "level": record.levelname.lower(),
            "logger": record.name,
            "caller_tag": getattr(record, "caller_tag", caller_tag.get("broker")),
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, separators=(",", ":"))


def _default_user_config_dir() -> Path:
    """Resolve a cross-platform user config directory for broker state."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / USER_CONFIG_DIRNAME

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata).expanduser() / USER_CONFIG_DIRNAME
        return Path.home() / "AppData" / "Roaming" / USER_CONFIG_DIRNAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / USER_CONFIG_DIRNAME

    return Path.home() / ".config" / USER_CONFIG_DIRNAME


def _resolve_broker_log_dir() -> Path:
    override = os.environ.get(BROKER_LOG_DIR_ENV_VAR)
    if override:
        return Path(override).expanduser()
    return _default_user_config_dir() / "broker-logs"


def _read_positive_int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        return default
    if value < minimum:
        return default
    return value


def _configure_logging() -> None:
    """Configure concise broker logs with stream and structured rotating logfile handlers."""
    logger = logging.getLogger("gsd_review_broker")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    has_stream_handler = any(
        getattr(handler, "_gsd_broker_stream_handler", False)
        for handler in logger.handlers
    )
    if not has_stream_handler:
        handler = logging.StreamHandler()
        handler._gsd_broker_stream_handler = True  # type: ignore[attr-defined]
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            _CallerFormatter(
                "%(asctime)s [%(caller_tag)s] %(message)s",
                "%H:%M:%S",
            )
        )
        logger.addHandler(handler)

    if not any(getattr(handler, "_gsd_broker_file_handler", False) for handler in logger.handlers):
        log_dir = _resolve_broker_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        log_max_bytes = _read_positive_int_env(
            BROKER_LOG_MAX_BYTES_ENV_VAR,
            DEFAULT_BROKER_LOG_MAX_BYTES,
            1024,
        )
        log_backups = _read_positive_int_env(
            BROKER_LOG_BACKUPS_ENV_VAR,
            DEFAULT_BROKER_LOG_BACKUPS,
            1,
        )
        file_handler = RotatingFileHandler(
            log_dir / "broker.jsonl",
            maxBytes=log_max_bytes,
            backupCount=log_backups,
            encoding="utf-8",
        )
        file_handler._gsd_broker_file_handler = True  # type: ignore[attr-defined]
        file_handler.setLevel(logging.INFO)
        file_handler.setFormatter(_JsonFormatter())
        logger.addHandler(file_handler)


# Ensure broker logger is configured even when server is launched without calling main().
_configure_logging()


def main() -> None:
    """Run the broker server on port 8321.

    Set BROKER_HOST to override bind host.
    Default is 0.0.0.0 (all interfaces).

    Storage:
    - Default DB path is user-scoped config dir:
      Linux: ~/.config/gsd-review-broker/codex_review_broker.sqlite3
      macOS: ~/Library/Application Support/gsd-review-broker/codex_review_broker.sqlite3
      Windows: %APPDATA%/gsd-review-broker/codex_review_broker.sqlite3
    - Set BROKER_DB_PATH to override with an explicit SQLite file path.
    """
    _configure_logging()
    host = os.environ.get("BROKER_HOST", "0.0.0.0")
    uvicorn_log_level = os.environ.get("BROKER_UVICORN_LOG_LEVEL", "warning")
    mcp.run(
        transport="streamable-http",
        host=host,
        port=8321,
        log_level=uvicorn_log_level,
        # Avoid sticky session failures after broker restarts.
        stateless_http=True,
    )


if __name__ == "__main__":
    main()
