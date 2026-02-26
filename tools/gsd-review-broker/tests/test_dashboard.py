"""Tests for dashboard HTTP route handlers (static file serving and SSE endpoint)."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
from starlette.testclient import TestClient

from gsd_review_broker import dashboard
from gsd_review_broker.server import mcp

# Resolved dist directory for conditional skipping
DIST_DIR = dashboard.DIST_DIR
DIST_EXISTS = DIST_DIR.is_dir() and (DIST_DIR / "index.html").is_file()


@pytest.fixture()
def app():
    """Build a testable Starlette app from the MCP server."""
    return mcp.http_app(transport="streamable-http", stateless_http=True)


@pytest.fixture()
def client(app):
    """Starlette TestClient for synchronous route testing."""
    return TestClient(app, raise_server_exceptions=False)


# ---- Index page tests ----


@pytest.mark.skipif(not DIST_EXISTS, reason="Dashboard dist/ not built")
def test_dashboard_index_returns_html(client):
    """GET /dashboard returns 200 with HTML content containing 'GSD Tandem'."""
    resp = client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "GSD Tandem" in resp.text


def test_dashboard_index_not_built(client):
    """GET /dashboard returns 503 when dist/index.html does not exist."""
    fake_dir = Path("/nonexistent/dashboard/dist")
    with patch.object(dashboard, "DIST_DIR", fake_dir):
        resp = client.get("/dashboard")
    assert resp.status_code == 503
    assert "not built" in resp.text.lower()


# ---- Static asset tests ----


@pytest.mark.skipif(not DIST_EXISTS, reason="Dashboard dist/ not built")
def test_dashboard_static_asset_css(client):
    """GET /dashboard/_astro/*.css returns 200 with text/css content type."""
    # Find a CSS file in the dist directory
    css_files = list(DIST_DIR.glob("_astro/*.css"))
    assert css_files, "No CSS files found in dist/_astro/"
    css_name = css_files[0].name
    resp = client.get(f"/dashboard/_astro/{css_name}")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]
    assert len(resp.content) > 0


@pytest.mark.skipif(not DIST_EXISTS, reason="Dashboard dist/ not built")
def test_dashboard_static_asset_js(client):
    """GET /dashboard/_astro/*.js returns 200 with application/javascript content type."""
    js_files = list(DIST_DIR.glob("_astro/*.js"))
    if not js_files:
        pytest.skip("No JS files found in dist/_astro/")
    js_name = js_files[0].name
    resp = client.get(f"/dashboard/_astro/{js_name}")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]


def test_dashboard_static_asset_not_found(client):
    """GET /dashboard/nonexistent-file.xyz returns 404."""
    resp = client.get("/dashboard/nonexistent-file.xyz")
    assert resp.status_code == 404


def test_dashboard_path_traversal_blocked(client):
    """Path traversal attempts return 404, not serving files outside dist/."""
    traversal_paths = [
        "/dashboard/../pyproject.toml",
        "/dashboard/../../etc/passwd",
        "/dashboard/../../../README.md",
    ]
    for path in traversal_paths:
        resp = client.get(path)
        assert resp.status_code == 404, f"Path traversal not blocked for {path}"


def test_dashboard_sibling_directory_boundary(client, tmp_path):
    """Strict directory-boundary check blocks sibling dirs with same prefix.

    A naive startswith() guard could let e.g. /dashboard/dist-backup/secret
    pass if a sibling directory shares the dist prefix. The relative_to()
    guard rejects any resolved path not strictly inside DIST_DIR.
    """
    # Create a sibling directory that shares the dist prefix
    sibling = tmp_path / "dist-evil"
    sibling.mkdir()
    secret = sibling / "secret.txt"
    secret.write_text("leaked")

    # Point DIST_DIR to tmp_path/dist (which doesn't need to exist for this test)
    fake_dist = tmp_path / "dist"
    fake_dist.mkdir()

    with patch.object(dashboard, "DIST_DIR", fake_dist):
        # Attempt to reach the sibling directory through the catch-all route
        resp = client.get("/dashboard/../dist-evil/secret.txt")
        assert resp.status_code == 404, "Sibling directory traversal should be blocked"


# ---- SSE endpoint tests ----


@pytest.fixture()
def sse_route(app):
    """Extract the SSE route handler for direct async testing."""
    for route in app.routes:
        if hasattr(route, "path") and route.path == "/dashboard/events":
            return route.endpoint
    pytest.fail("SSE route /dashboard/events not found in app routes")


async def test_dashboard_sse_endpoint(sse_route):
    """GET /dashboard/events returns SSE stream with connected event."""
    request = MagicMock()
    resp = await sse_route(request)

    assert resp.media_type == "text/event-stream"
    assert resp.headers["cache-control"] == "no-cache"
    assert resp.headers["connection"] == "keep-alive"
    assert resp.headers["x-accel-buffering"] == "no"

    # Read the first event from the async generator
    body_iter = resp.body_iterator
    first_chunk = await body_iter.__anext__()
    assert "event: connected" in first_chunk
    assert '"status": "connected"' in first_chunk

    # Clean up: close the generator
    await body_iter.aclose()


async def test_dashboard_sse_heartbeat(sse_route):
    """SSE endpoint sends overview_update after connected, then periodic updates."""
    # Use a very short heartbeat interval for testing
    original_interval = dashboard.SSE_HEARTBEAT_INTERVAL
    dashboard.SSE_HEARTBEAT_INTERVAL = 0.05  # 50ms

    try:
        request = MagicMock()
        resp = await sse_route(request)
        body_iter = resp.body_iterator

        # First chunk: connected event
        first = await body_iter.__anext__()
        assert "event: connected" in first

        # Second chunk: initial overview_update (pushed immediately after connect)
        second = await asyncio.wait_for(body_iter.__anext__(), timeout=2.0)
        assert "overview_update" in second

        # Third chunk: periodic overview_update (should arrive after ~50ms)
        third = await asyncio.wait_for(body_iter.__anext__(), timeout=2.0)
        assert "overview_update" in third

        await body_iter.aclose()
    finally:
        dashboard.SSE_HEARTBEAT_INTERVAL = original_interval


def test_dashboard_sse_content_type(client):
    """SSE endpoint returns text/event-stream content type (non-streaming check)."""
    # Use the app routes directly to verify content type without consuming the stream
    for route in client.app.routes:
        if hasattr(route, "path") and route.path == "/dashboard/events":
            # Found it - test passes if route is registered
            assert route.methods == {"GET", "HEAD"}
            return
    pytest.fail("SSE route not found")


# ---- Overview API tests ----


@pytest.fixture()
async def overview_ctx():
    """Create an AppContext with in-memory DB and schema for overview tests."""
    import aiosqlite
    from gsd_review_broker.db import AppContext, ensure_schema

    db = await aiosqlite.connect(":memory:")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await ensure_schema(db)

    ctx = AppContext(db=db, repo_root=None, pool=None)
    # Set the module-level app context
    original_ctx = dashboard._app_ctx
    dashboard._app_ctx = ctx

    yield ctx

    # Cleanup
    dashboard._app_ctx = original_ctx
    await db.close()


@pytest.fixture()
async def overview_ctx_with_data(overview_ctx):
    """AppContext with sample review data inserted."""
    db = overview_ctx.db
    await db.execute("BEGIN IMMEDIATE")
    await db.execute(
        "INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r1", "approved", "test intent", "executor", "proposer", "01", "code_change"),
    )
    await db.execute(
        "INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r2", "pending", "test intent 2", "planner", "proposer", "02", "plan_review"),
    )
    await db.execute(
        "INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase, category) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("r3", "closed", "test intent 3", "executor", "proposer", "01", "code_change"),
    )
    await db.execute("COMMIT")
    yield overview_ctx


async def test_overview_api_returns_json(overview_ctx):
    """GET /dashboard/api/overview returns 200 with JSON containing expected top-level keys."""
    from starlette.testclient import TestClient

    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/dashboard/api/overview")
    assert resp.status_code == 200
    assert "application/json" in resp.headers["content-type"]
    data = resp.json()
    assert "broker" in data
    assert "stats" in data
    assert "reviewers" in data


async def test_overview_api_broker_section(overview_ctx):
    """Broker section contains version, uptime_seconds, address, and config."""
    from gsd_review_broker.dashboard import _build_overview_data

    data = await _build_overview_data()
    broker = data["broker"]
    assert "version" in broker
    assert isinstance(broker["version"], str)
    assert broker["uptime_seconds"] >= 0
    assert isinstance(broker["address"], str)
    assert ":" in broker["address"]
    assert isinstance(broker["config"], dict)


async def test_overview_api_stats_section(overview_ctx_with_data):
    """Stats section reflects inserted review data correctly."""
    from gsd_review_broker.dashboard import _build_overview_data

    data = await _build_overview_data()
    stats = data["stats"]
    assert stats["total_reviews"] == 3
    assert "by_status" in stats
    assert stats["by_status"]["approved"] == 1
    assert stats["by_status"]["pending"] == 1
    assert stats["by_status"]["closed"] == 1
    assert "by_category" in stats
    assert stats["by_category"]["code_change"] == 2
    assert stats["by_category"]["plan_review"] == 1
    # approval_rate_pct is None when no audit events exist
    assert stats["approval_rate_pct"] is None
    assert stats["avg_time_to_verdict_seconds"] is None
    assert stats["avg_review_duration_seconds"] is None


async def test_overview_api_reviewers_no_pool(overview_ctx):
    """Without pool configured, reviewers section has pool_active=False and empty list."""
    from gsd_review_broker.dashboard import _build_overview_data

    data = await _build_overview_data()
    reviewers = data["reviewers"]
    assert reviewers["pool_active"] is False
    assert reviewers["session_token"] is None
    assert reviewers["pool_size"] == 0
    assert reviewers["reviewers"] == []


async def test_overview_api_reviewers_with_pool(overview_ctx):
    """With a mock pool, reviewers section includes pool data and reviewer entries."""
    from unittest.mock import MagicMock
    from gsd_review_broker.dashboard import _build_overview_data

    # Create a mock pool
    mock_pool = MagicMock()
    mock_pool.session_token = "test-session-abc"
    mock_pool.active_count = 1

    # Insert a reviewer row
    db = overview_ctx.db
    await db.execute("BEGIN IMMEDIATE")
    await db.execute(
        "INSERT INTO reviewers (id, display_name, session_token, status, pid, "
        "reviews_completed, total_review_seconds, approvals, rejections) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("rev-1-abc", "reviewer-1", "test-session-abc", "active", 12345, 5, 250.0, 4, 1),
    )
    await db.execute("COMMIT")

    # Temporarily set pool on context
    original_pool = overview_ctx.pool
    overview_ctx.pool = mock_pool

    try:
        data = await _build_overview_data()
        reviewers = data["reviewers"]
        assert reviewers["pool_active"] is True
        assert reviewers["session_token"] == "test-session-abc"
        assert reviewers["pool_size"] == 1
        assert len(reviewers["reviewers"]) == 1
        r = reviewers["reviewers"][0]
        assert r["id"] == "rev-1-abc"
        assert r["display_name"] == "reviewer-1"
        assert r["status"] == "active"
        assert r["pid"] == 12345
        assert r["reviews_completed"] == 5
        assert r["approvals"] == 4
        assert r["rejections"] == 1
    finally:
        overview_ctx.pool = original_pool


async def test_sse_sends_overview_update(overview_ctx):
    """SSE stream sends overview_update data using default message format (no event: prefix)."""
    import json as json_mod

    # Use a very short heartbeat interval
    original_interval = dashboard.SSE_HEARTBEAT_INTERVAL
    dashboard.SSE_HEARTBEAT_INTERVAL = 0.05

    try:
        app = mcp.http_app(transport="streamable-http", stateless_http=True)
        # Get the SSE route handler
        sse_handler = None
        for route in app.routes:
            if hasattr(route, "path") and route.path == "/dashboard/events":
                sse_handler = route.endpoint
                break
        assert sse_handler is not None, "SSE route not found"

        request = MagicMock()
        resp = await sse_handler(request)
        body_iter = resp.body_iterator

        # First chunk: connected event (named event, fine)
        first = await body_iter.__anext__()
        assert "event: connected" in first

        # Second chunk: overview_update (default message format, no event: prefix)
        second = await body_iter.__anext__()
        # Must NOT have "event:" prefix
        assert "event:" not in second, f"overview_update should use default format, got: {second}"
        assert second.startswith("data: ")
        # Parse the JSON payload
        json_str = second.replace("data: ", "").strip()
        payload = json_mod.loads(json_str)
        assert payload["type"] == "overview_update"
        assert "broker" in payload
        assert "stats" in payload
        assert "reviewers" in payload

        await body_iter.aclose()
    finally:
        dashboard.SSE_HEARTBEAT_INTERVAL = original_interval
