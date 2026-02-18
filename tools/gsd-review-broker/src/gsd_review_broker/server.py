"""FastMCP server entry point for the GSD Review Broker."""

from __future__ import annotations

import logging
import os

from fastmcp import FastMCP

from gsd_review_broker.db import broker_lifespan

mcp = FastMCP(
    "gsd-review-broker",
    instructions=(
        "Review broker for GSD tandem pairing. "
        "Manages review lifecycle between proposer and reviewer."
    ),
    lifespan=broker_lifespan,
)

# Import tools to register them with @mcp.tool.
# This import MUST come AFTER mcp is created to avoid circular imports.
from gsd_review_broker import tools  # noqa: F401, E402


def _configure_logging() -> None:
    """Configure concise broker logs separate from HTTP access logs."""
    logger = logging.getLogger("gsd_review_broker")
    logger.setLevel(logging.INFO)
    logger.propagate = False

    if any(getattr(handler, "_gsd_broker_handler", False) for handler in logger.handlers):
        return

    handler = logging.StreamHandler()
    handler._gsd_broker_handler = True  # type: ignore[attr-defined]
    handler.setLevel(logging.INFO)
    handler.setFormatter(logging.Formatter("%(asctime)s [broker] %(message)s", "%H:%M:%S"))
    logger.addHandler(handler)


# Ensure broker logger is configured even when server is launched without calling main().
_configure_logging()


def main() -> None:
    """Run the broker server on port 8321.

    Set BROKER_HOST to override bind host.
    Default is 0.0.0.0 (all interfaces).
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
