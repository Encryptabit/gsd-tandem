"""Dashboard HTTP routes for serving the Astro-built static frontend and SSE endpoint."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
from pathlib import Path

import aiosqlite
from starlette.requests import Request
from starlette.responses import HTMLResponse, JSONResponse, PlainTextResponse, Response, StreamingResponse

from gsd_review_broker import __version__
from gsd_review_broker.db import AppContext

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

# Module-level AppContext, set by broker_lifespan via set_app_context().
_app_ctx: AppContext | None = None

# Server start time for uptime calculation.
_start_time: float = time.monotonic()


def set_app_context(ctx: AppContext) -> None:
    """Store the AppContext for dashboard route handlers to access."""
    global _app_ctx
    _app_ctx = ctx


async def _query_review_stats(db: aiosqlite.Connection, project: str | None = None) -> dict:
    """Query aggregate review statistics from the database.

    Replicates the essential queries from get_review_stats in tools.py
    without requiring an MCP Context.
    """
    review_where_clause = "WHERE project = ?" if project is not None else ""
    review_where_params: tuple[str, ...] = (project,) if project is not None else ()

    # Status counts
    cursor = await db.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END), 0) AS claimed,
            COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) AS approved,
            COALESCE(
                SUM(CASE WHEN status = 'changes_requested' THEN 1 ELSE 0 END),
                0
            ) AS changes_requested,
            COALESCE(SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END), 0) AS closed
        FROM reviews
        {review_where_clause}
    """,
        review_where_params,
    )
    counts = dict(await cursor.fetchone())

    # Category breakdown
    cursor = await db.execute(
        f"""
        SELECT COALESCE(category, 'uncategorized') AS cat, COUNT(*) AS cnt
        FROM reviews
        {review_where_clause}
        GROUP BY cat
    """,
        review_where_params,
    )
    by_category = {row["cat"]: row["cnt"] for row in await cursor.fetchall()}

    # Approval rate
    approval_rate = None
    verdict_where_clause = (
        "WHERE ae.event_type = 'verdict_submitted' "
        "AND json_extract(ae.metadata, '$.verdict') = 'approved'"
    )
    verdict_where_params_list: list[str] = []
    if project is not None:
        verdict_where_clause += " AND r.project = ?"
        verdict_where_params_list.append(project)

    cursor = await db.execute(
        f"""
        SELECT COUNT(DISTINCT ae.review_id)
        FROM audit_events ae
        JOIN reviews r ON r.id = ae.review_id
        {verdict_where_clause}
    """,
        verdict_where_params_list,
    )
    approved_verdicts = (await cursor.fetchone())[0]

    total_verdict_where_clause = "WHERE ae.event_type = 'verdict_submitted'"
    total_verdict_where_params_list: list[str] = []
    if project is not None:
        total_verdict_where_clause += " AND r.project = ?"
        total_verdict_where_params_list.append(project)

    cursor = await db.execute(
        f"""
        SELECT COUNT(DISTINCT ae.review_id)
        FROM audit_events ae
        JOIN reviews r ON r.id = ae.review_id
        {total_verdict_where_clause}
    """,
        total_verdict_where_params_list,
    )
    total_verdicts = (await cursor.fetchone())[0]
    if total_verdicts > 0:
        approval_rate = round(100.0 * approved_verdicts / total_verdicts, 1)

    # Average time-to-verdict
    to_verdict_project_clause = "AND r.project = ?" if project is not None else ""
    to_verdict_params: tuple[str, ...] = (project,) if project is not None else ()
    cursor = await db.execute(
        f"""
        SELECT AVG(
            (julianday(ae.created_at) - julianday(r.created_at)) * 86400
        ) AS avg_seconds
        FROM reviews r
        JOIN audit_events ae ON ae.review_id = r.id
            AND ae.event_type = 'verdict_submitted'
        WHERE ae.id = (
            SELECT MIN(ae2.id) FROM audit_events ae2
            WHERE ae2.review_id = r.id AND ae2.event_type = 'verdict_submitted'
        )
        {to_verdict_project_clause}
    """,
        to_verdict_params,
    )
    avg_to_verdict = (await cursor.fetchone())[0]

    # Average review duration (created to closed)
    review_duration_project_clause = "WHERE r.project = ?" if project is not None else ""
    review_duration_params: tuple[str, ...] = (project,) if project is not None else ()
    cursor = await db.execute(
        f"""
        SELECT AVG(
            (julianday(ae.created_at) - julianday(r.created_at)) * 86400
        ) AS avg_seconds
        FROM reviews r
        JOIN audit_events ae ON ae.review_id = r.id
            AND ae.event_type = 'review_closed'
        {review_duration_project_clause}
    """,
        review_duration_params,
    )
    avg_duration = (await cursor.fetchone())[0]

    return {
        "total_reviews": counts["total"],
        "by_status": {
            "pending": counts["pending"],
            "claimed": counts["claimed"],
            "approved": counts["approved"],
            "changes_requested": counts["changes_requested"],
            "closed": counts["closed"],
        },
        "by_category": by_category,
        "approval_rate_pct": approval_rate,
        "avg_time_to_verdict_seconds": round(avg_to_verdict, 1) if avg_to_verdict else None,
        "avg_review_duration_seconds": round(avg_duration, 1) if avg_duration else None,
    }


async def _query_reviewers(ctx: AppContext) -> dict:
    """Query reviewer pool information from the database.

    Replicates the essential query from list_reviewers in tools.py
    without requiring an MCP Context.
    """
    pool = ctx.pool
    if pool is None:
        return {
            "pool_active": False,
            "session_token": None,
            "pool_size": 0,
            "reviewers": [],
        }

    cursor = await ctx.db.execute(
        """SELECT id, display_name, status, pid, spawned_at, last_active_at,
                  reviews_completed, total_review_seconds, approvals, rejections
           FROM reviewers
           WHERE session_token = ?
           ORDER BY spawned_at ASC""",
        (pool.session_token,),
    )
    rows = await cursor.fetchall()

    # Get current review assignments for claimed reviews
    claimed_cursor = await ctx.db.execute(
        "SELECT id, claimed_by FROM reviews WHERE status = 'claimed'"
    )
    claimed_rows = await claimed_cursor.fetchall()
    current_reviews: dict[str, str] = {}
    for row in claimed_rows:
        if row["claimed_by"]:
            current_reviews[row["claimed_by"]] = row["id"]

    reviewers = [
        {
            "id": row["id"],
            "display_name": row["display_name"],
            "status": row["status"],
            "pid": row["pid"],
            "spawned_at": row["spawned_at"],
            "last_active_at": row["last_active_at"],
            "reviews_completed": row["reviews_completed"],
            "total_review_seconds": row["total_review_seconds"],
            "approvals": row["approvals"],
            "rejections": row["rejections"],
            "current_review": current_reviews.get(row["id"]),
        }
        for row in rows
    ]

    return {
        "pool_active": True,
        "session_token": pool.session_token,
        "pool_size": pool.active_count,
        "reviewers": reviewers,
    }


def _read_broker_config(repo_root: str | None) -> dict:
    """Read broker config from .planning/config.json."""
    config_path_env = os.environ.get("BROKER_CONFIG_PATH")
    if config_path_env:
        config_path = Path(config_path_env).expanduser()
    else:
        base = Path(repo_root) if repo_root is not None else Path.cwd()
        config_path = base / ".planning" / "config.json"

    try:
        payload = json.loads(config_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError):
        return {}

    pool_section = payload.get("reviewer_pool", {})
    review_section = payload.get("review", {})

    return {
        "mode": payload.get("mode"),
        "model_profile": payload.get("model_profile"),
        "review_granularity": payload.get("review_granularity"),
        "execution_mode": payload.get("execution_mode"),
        "review_enabled": review_section.get("enabled", False),
        "pool_enabled": pool_section is not None and bool(pool_section),
        "max_pool_size": pool_section.get("max_pool_size") if isinstance(pool_section, dict) else None,
    }


async def _build_overview_data() -> dict:
    """Build the full overview data payload for API and SSE."""
    ctx = _app_ctx
    host = os.environ.get("BROKER_HOST", "0.0.0.0")
    port = os.environ.get("BROKER_PORT", "8321")

    broker_section = {
        "version": __version__,
        "uptime_seconds": round(time.monotonic() - _start_time, 1),
        "address": f"{host}:{port}",
        "config": _read_broker_config(ctx.repo_root if ctx else None),
    }

    if ctx is not None:
        stats_section = await _query_review_stats(ctx.db)
        reviewers_section = await _query_reviewers(ctx)
    else:
        stats_section = {
            "total_reviews": 0,
            "by_status": {
                "pending": 0, "claimed": 0, "approved": 0,
                "changes_requested": 0, "closed": 0,
            },
            "by_category": {},
            "approval_rate_pct": None,
            "avg_time_to_verdict_seconds": None,
            "avg_review_duration_seconds": None,
        }
        reviewers_section = {
            "pool_active": False,
            "session_token": None,
            "pool_size": 0,
            "reviewers": [],
        }

    return {
        "broker": broker_section,
        "stats": stats_section,
        "reviewers": reviewers_section,
    }


def register_dashboard_routes(mcp: object) -> None:
    """Register all dashboard HTTP routes on the FastMCP server instance.

    Route registration order matters: the catch-all {path:path} route MUST be
    registered LAST, otherwise it intercepts /dashboard/events and other specific routes.

    Order: /dashboard/events, /dashboard/api/overview, /dashboard, /dashboard/{path:path}
    """

    @mcp.custom_route("/dashboard/events", methods=["GET"])  # type: ignore[union-attr]
    async def dashboard_events(request: Request) -> Response:
        """SSE endpoint for real-time dashboard data push."""

        async def event_stream() -> asyncio.AsyncIterator[str]:
            logger.info("Dashboard SSE client connected")
            try:
                yield 'event: connected\ndata: {"status": "connected"}\n\n'

                # Push initial overview data immediately after connection
                try:
                    overview_data = await _build_overview_data()
                    overview_data["type"] = "overview_update"
                    yield f"data: {json.dumps(overview_data)}\n\n"
                except Exception:
                    logger.exception("Failed to build initial overview data for SSE")

                while True:
                    await asyncio.sleep(SSE_HEARTBEAT_INTERVAL)
                    # Push overview update on each heartbeat interval
                    try:
                        overview_data = await _build_overview_data()
                        overview_data["type"] = "overview_update"
                        yield f"data: {json.dumps(overview_data)}\n\n"
                    except Exception:
                        logger.exception("Failed to build overview data for SSE")
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

    @mcp.custom_route("/dashboard/api/overview", methods=["GET"])  # type: ignore[union-attr]
    async def dashboard_overview_api(request: Request) -> Response:
        """JSON API endpoint returning broker status, review stats, and reviewer list."""
        data = await _build_overview_data()
        return JSONResponse(data)

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
        # Use Path.is_relative_to() for strict containment -- avoids prefix
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
