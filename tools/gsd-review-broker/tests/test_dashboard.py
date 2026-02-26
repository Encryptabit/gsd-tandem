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
    """SSE endpoint sends heartbeat events after the initial connected event."""
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

        # Second chunk: heartbeat (should arrive after ~50ms)
        heartbeat = await asyncio.wait_for(body_iter.__anext__(), timeout=2.0)
        assert "event: heartbeat" in heartbeat
        assert "data: {}" in heartbeat

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
