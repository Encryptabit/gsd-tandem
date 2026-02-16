"""FastMCP server entry point for the GSD Review Broker."""

from __future__ import annotations

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


def main() -> None:
    """Run the broker server on localhost:8321."""
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8321,
    )


if __name__ == "__main__":
    main()
