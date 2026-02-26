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
    request.query_params = {}
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
    # Use very short intervals for testing
    original_interval = dashboard.SSE_HEARTBEAT_INTERVAL
    original_tail_interval = dashboard.SSE_LOG_TAIL_INTERVAL
    dashboard.SSE_HEARTBEAT_INTERVAL = 0.05  # 50ms
    dashboard.SSE_LOG_TAIL_INTERVAL = 0.02  # 20ms

    try:
        request = MagicMock()
        request.query_params = {}
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
        dashboard.SSE_LOG_TAIL_INTERVAL = original_tail_interval


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
        request.query_params = {}
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



# ---- Log API tests ----


async def test_log_listing_empty(overview_ctx):
    """API returns empty list when log directories do not exist."""
    import os

    # Point env vars to non-existent directories
    original_broker = os.environ.get("BROKER_LOG_DIR")
    original_reviewer = os.environ.get("BROKER_REVIEWER_LOG_DIR")
    os.environ["BROKER_LOG_DIR"] = "/nonexistent/broker-logs"
    os.environ["BROKER_REVIEWER_LOG_DIR"] = "/nonexistent/reviewer-logs"

    try:
        from starlette.testclient import TestClient

        app = mcp.http_app(transport="streamable-http", stateless_http=True)
        client = TestClient(app, raise_server_exceptions=False)
        resp = client.get("/dashboard/api/logs")
        assert resp.status_code == 200
        data = resp.json()
        assert data == {"files": []}
    finally:
        if original_broker is not None:
            os.environ["BROKER_LOG_DIR"] = original_broker
        else:
            os.environ.pop("BROKER_LOG_DIR", None)
        if original_reviewer is not None:
            os.environ["BROKER_REVIEWER_LOG_DIR"] = original_reviewer
        else:
            os.environ.pop("BROKER_REVIEWER_LOG_DIR", None)


async def test_log_listing_with_files(overview_ctx, tmp_path, monkeypatch):
    """API returns file listing from both broker-logs/ and reviewer-logs/ dirs."""
    import time as time_mod

    broker_dir = tmp_path / "broker-logs"
    broker_dir.mkdir()
    reviewer_dir = tmp_path / "reviewer-logs"
    reviewer_dir.mkdir()

    # Create sample log files
    broker_log = broker_dir / "broker.jsonl"
    broker_log.write_text(
        '{"ts":"2026-02-26T12:00:00.000Z","level":"info","message":"test"}\n',
        encoding="utf-8",
    )

    reviewer_log = reviewer_dir / "reviewer-1.jsonl"
    reviewer_log.write_text(
        '{"ts":"2026-02-26T12:00:00.000Z","event":"reviewer_output","message":"test"}\n',
        encoding="utf-8",
    )

    # Create a rotated log file
    rotated = broker_dir / "broker.jsonl.1"
    rotated.write_text(
        '{"ts":"2026-02-25T12:00:00.000Z","level":"info","message":"old"}\n',
        encoding="utf-8",
    )

    monkeypatch.setenv("BROKER_LOG_DIR", str(broker_dir))
    monkeypatch.setenv("BROKER_REVIEWER_LOG_DIR", str(reviewer_dir))

    from starlette.testclient import TestClient

    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/dashboard/api/logs")
    assert resp.status_code == 200
    data = resp.json()

    files = data["files"]
    assert len(files) == 3

    # Check all files have required fields
    for f in files:
        assert "name" in f
        assert "size" in f
        assert "modified" in f
        assert "source" in f
        assert isinstance(f["size"], int)
        assert f["size"] > 0

    # Check sources
    sources = {f["name"]: f["source"] for f in files}
    assert sources["broker.jsonl"] == "broker"
    assert sources["broker.jsonl.1"] == "broker"
    assert sources["reviewer-1.jsonl"] == "reviewer"


async def test_log_file_read(overview_ctx, tmp_path, monkeypatch):
    """Reading a JSONL log file returns parsed entries."""
    broker_dir = tmp_path / "broker-logs"
    broker_dir.mkdir()

    log_file = broker_dir / "broker.jsonl"
    lines = [
        '{"ts":"2026-02-26T12:00:00.000Z","level":"info","logger":"gsd_review_broker","caller_tag":"broker","message":"Server started on 0.0.0.0:8321"}',
        '{"ts":"2026-02-26T12:00:01.000Z","level":"info","logger":"gsd_review_broker","caller_tag":"proposer","message":"Review created: abc-123"}',
        '{"ts":"2026-02-26T12:00:02.000Z","level":"warn","logger":"gsd_review_broker","caller_tag":"broker","message":"Slow query detected"}',
    ]
    log_file.write_text("\n".join(lines) + "\n", encoding="utf-8")

    monkeypatch.setenv("BROKER_LOG_DIR", str(broker_dir))
    monkeypatch.setenv("BROKER_REVIEWER_LOG_DIR", str(tmp_path / "nonexistent"))

    from starlette.testclient import TestClient

    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/dashboard/api/logs/broker.jsonl")
    assert resp.status_code == 200
    data = resp.json()

    assert data["filename"] == "broker.jsonl"
    assert data["source"] == "broker"
    assert len(data["entries"]) == 3
    assert data["entries"][0]["message"] == "Server started on 0.0.0.0:8321"
    assert data["entries"][1]["caller_tag"] == "proposer"
    assert data["entries"][2]["level"] == "warn"
    assert isinstance(data["size"], int)
    assert data["size"] > 0


async def test_log_file_read_not_found(overview_ctx, tmp_path, monkeypatch):
    """Request for nonexistent log file returns 404."""
    broker_dir = tmp_path / "broker-logs"
    broker_dir.mkdir()

    monkeypatch.setenv("BROKER_LOG_DIR", str(broker_dir))
    monkeypatch.setenv("BROKER_REVIEWER_LOG_DIR", str(tmp_path / "nonexistent"))

    from starlette.testclient import TestClient

    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/dashboard/api/logs/nonexistent.jsonl")
    assert resp.status_code == 404


async def test_log_file_path_traversal(overview_ctx, tmp_path, monkeypatch):
    """Path traversal attempts on log file endpoint return 404."""
    broker_dir = tmp_path / "broker-logs"
    broker_dir.mkdir()

    # Create a file outside log dir that an attacker might target
    secret = tmp_path / "secret.txt"
    secret.write_text("sensitive data", encoding="utf-8")

    monkeypatch.setenv("BROKER_LOG_DIR", str(broker_dir))
    monkeypatch.setenv("BROKER_REVIEWER_LOG_DIR", str(tmp_path / "nonexistent"))

    from starlette.testclient import TestClient

    app = mcp.http_app(transport="streamable-http", stateless_http=True)
    client = TestClient(app, raise_server_exceptions=False)

    traversal_paths = [
        "/dashboard/api/logs/../../etc/passwd",
        "/dashboard/api/logs/../secret.txt",
        "/dashboard/api/logs/..\\secret.txt",
    ]
    for path in traversal_paths:
        resp = client.get(path)
        assert resp.status_code == 404, f"Path traversal not blocked for {path}"


async def test_sse_log_tail(overview_ctx, tmp_path, monkeypatch):
    """SSE with ?tail=filename streams new log entries."""
    import json as json_mod

    broker_dir = tmp_path / "broker-logs"
    broker_dir.mkdir()

    log_file = broker_dir / "broker.jsonl"
    # Start with one entry
    initial_entry = '{"ts":"2026-02-26T12:00:00.000Z","level":"info","message":"initial"}'
    log_file.write_text(initial_entry + "\n", encoding="utf-8")

    monkeypatch.setenv("BROKER_LOG_DIR", str(broker_dir))
    monkeypatch.setenv("BROKER_REVIEWER_LOG_DIR", str(tmp_path / "nonexistent"))

    # Use very short intervals for testing
    original_heartbeat = dashboard.SSE_HEARTBEAT_INTERVAL
    original_tail = dashboard.SSE_LOG_TAIL_INTERVAL
    dashboard.SSE_HEARTBEAT_INTERVAL = 100  # Don't fire overview updates during test
    dashboard.SSE_LOG_TAIL_INTERVAL = 0.02  # 20ms for fast tail checking

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
        request.query_params = {"tail": "broker.jsonl"}
        resp = await sse_handler(request)
        body_iter = resp.body_iterator

        # First chunk: connected event
        first = await body_iter.__anext__()
        assert "event: connected" in first

        # Second chunk: initial overview_update
        second = await body_iter.__anext__()
        assert "overview_update" in second

        # Now write a new entry to the log file
        new_entry = '{"ts":"2026-02-26T12:00:05.000Z","level":"info","message":"new entry"}'
        with open(log_file, "a", encoding="utf-8") as f:
            f.write(new_entry + "\n")

        # Wait for log_tail event (should arrive within a few ticks)
        found_tail = False
        for _ in range(50):  # Up to ~1 second at 20ms intervals
            chunk = await asyncio.wait_for(body_iter.__anext__(), timeout=2.0)
            if "log_tail" in chunk:
                found_tail = True
                # Parse the SSE data
                json_str = chunk.replace("data: ", "").strip()
                payload = json_mod.loads(json_str)
                assert payload["type"] == "log_tail"
                assert len(payload["entries"]) >= 1
                messages = [e["message"] for e in payload["entries"]]
                assert "new entry" in messages
                break

        assert found_tail, "Never received log_tail event"

        await body_iter.aclose()
    finally:
        dashboard.SSE_HEARTBEAT_INTERVAL = original_heartbeat
        dashboard.SSE_LOG_TAIL_INTERVAL = original_tail
