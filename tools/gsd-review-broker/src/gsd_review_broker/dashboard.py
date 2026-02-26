"""Dashboard HTTP routes for serving the Astro-built static frontend and SSE endpoint."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from starlette.requests import Request
from starlette.responses import HTMLResponse, PlainTextResponse, Response, StreamingResponse

logger = logging.getLogger("gsd_review_broker")

# Resolve the dist/ directory once at module load time.
# From src/gsd_review_broker/ up to tools/gsd-review-broker/, then into dashboard/dist/.
DIST_DIR: Path = Path(__file__).resolve().parent.parent.parent / "dashboard" / "dist"

CONTENT_TYPES: dict[str, str] = {
    ".html": "text/html",
    ".css": "text/css",
    ".js": "application/javascript",
    ".svg": "image/svg+xml",
    ".json": "application/json",
    ".ico": "image/x-icon",
}

SSE_HEARTBEAT_INTERVAL: int = 15


def register_dashboard_routes(mcp: object) -> None:
    """Register all dashboard HTTP routes on the FastMCP server instance.

    Route registration order matters: the catch-all {path:path} route MUST be
    registered LAST, otherwise it intercepts /dashboard/events and other specific routes.
    """

    @mcp.custom_route("/dashboard/events", methods=["GET"])  # type: ignore[union-attr]
    async def dashboard_events(request: Request) -> Response:
        """SSE endpoint for real-time dashboard data push."""

        async def event_stream() -> asyncio.AsyncIterator[str]:
            logger.info("Dashboard SSE client connected")
            try:
                yield 'event: connected\ndata: {"status": "connected"}\n\n'
                while True:
                    await asyncio.sleep(SSE_HEARTBEAT_INTERVAL)
                    yield "event: heartbeat\ndata: {}\n\n"
            except asyncio.CancelledError:
                logger.info("Dashboard SSE client disconnected")
                return

        return StreamingResponse(
            event_stream(),
            media_type="text/event-stream",
            headers={
                "Cache-Control": "no-cache",
                "Connection": "keep-alive",
                "X-Accel-Buffering": "no",
            },
        )

    @mcp.custom_route("/dashboard", methods=["GET"])  # type: ignore[union-attr]
    async def dashboard_index(request: Request) -> Response:
        """Serve the built Astro index.html as the dashboard entry point."""
        index_path = DIST_DIR / "index.html"
        if not index_path.is_file():
            return PlainTextResponse(
                "Dashboard not built. Run 'npm run build' in dashboard/",
                status_code=503,
            )
        content = index_path.read_bytes()
        return HTMLResponse(content=content)

    @mcp.custom_route("/dashboard/{path:path}", methods=["GET"])  # type: ignore[union-attr]
    async def dashboard_static(request: Request) -> Response:
        """Serve static assets from the built dist/ directory."""
        asset_path_str: str = request.path_params["path"]
        asset_path = (DIST_DIR / asset_path_str).resolve()

        # Security: prevent path traversal outside dist directory.
        # Use Path.is_relative_to() for strict containment — avoids prefix
        # matching bugs where sibling dirs (e.g. dist-backup/) pass.
        dist_resolved = DIST_DIR.resolve()
        try:
            asset_path.relative_to(dist_resolved)
        except ValueError:
            return PlainTextResponse("Not found", status_code=404)

        if not asset_path.is_file():
            return PlainTextResponse("Not found", status_code=404)

        suffix = asset_path.suffix.lower()
        content_type = CONTENT_TYPES.get(suffix, "application/octet-stream")
        content = asset_path.read_bytes()
        return Response(content=content, media_type=content_type)
