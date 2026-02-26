---
phase: 08-dashboard-shell-and-infrastructure
plan: 02
subsystem: ui
tags: [python, starlette, sse, static-file-serving, fastmcp-custom-routes, dashboard]

# Dependency graph
requires:
  - phase: 08-01
    provides: "Astro-built static files in dashboard/dist/ (index.html, CSS)"
provides:
  - "Python dashboard module serving Astro static files at /dashboard"
  - "SSE endpoint at /dashboard/events with heartbeat keepalive"
  - "Path traversal prevention on static file serving"
  - "register_dashboard_routes() wired into server.py"
  - "Comprehensive dashboard route tests (9 tests)"
affects: [09, 10, 11, 12]

# Tech tracking
tech-stack:
  added: []
  patterns: [fastmcp-custom-route-registration, sse-async-generator, starlette-response-types]

key-files:
  created:
    - tools/gsd-review-broker/src/gsd_review_broker/dashboard.py
    - tools/gsd-review-broker/tests/test_dashboard.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/server.py

key-decisions:
  - "Direct async generator for SSE stream with asyncio.sleep heartbeat (no sse-starlette wrapper needed)"
  - "Synchronous file reads for static assets (small files, no async I/O overhead)"
  - "Route registration order: /dashboard/events first, /dashboard second, /dashboard/{path:path} last to prevent catch-all interception"
  - "Test SSE via direct handler invocation and body_iterator consumption (avoids TestClient stream buffering issues)"

patterns-established:
  - "register_*_routes(mcp) pattern for modular custom HTTP route registration"
  - "Path traversal prevention via .resolve() + startswith() on dist directory"
  - "SSE async generator pattern: connected event, then heartbeat loop with CancelledError handling"
  - "Direct route handler testing for streaming endpoints (bypassing HTTP client buffering)"

requirements-completed: [DASH-01]

# Metrics
duration: 10min
completed: 2026-02-26
---

# Phase 8 Plan 2: Dashboard Static Serving Summary

**Python dashboard module serving Astro-built static files at /dashboard with SSE heartbeat endpoint and path traversal prevention**

## Performance

- **Duration:** 10 min
- **Started:** 2026-02-26T11:39:58Z
- **Completed:** 2026-02-26T11:50:03Z
- **Tasks:** 2
- **Files modified:** 3

## Accomplishments

- Dashboard module with three routes: index page (200 HTML), static assets (MIME-typed), SSE endpoint (text/event-stream)
- Path traversal prevention blocking directory escape attacks on static file serving
- SSE endpoint with connected event and 15-second heartbeat keepalive for client connection monitoring
- Comprehensive test suite: 8 passed, 1 skipped (no JS in dist), covering all route behaviors including security

## Task Commits

Each task was committed atomically:

1. **Task 1: Create dashboard.py module with static file serving and SSE endpoint** - `e758772` (feat)
2. **Task 2: Create dashboard route tests** - `8ab3a6f` (test)

## Files Created/Modified

- `tools/gsd-review-broker/src/gsd_review_broker/dashboard.py` - HTTP route handlers for static file serving and SSE endpoint (91 lines)
- `tools/gsd-review-broker/src/gsd_review_broker/server.py` - Added dashboard import and route registration call
- `tools/gsd-review-broker/tests/test_dashboard.py` - Comprehensive tests for all dashboard routes (164 lines)

## Decisions Made

- **Direct async generator for SSE:** Used a plain async generator yielding SSE-formatted strings rather than the sse-starlette library. The heartbeat-only use case is simple enough that a wrapper adds complexity without benefit.
- **Synchronous file reads:** Static assets are small (CSS, JS bundles), so synchronous `read_bytes()` avoids the overhead of async file I/O while keeping the code simple.
- **Route registration order:** Registered `/dashboard/events` before `/dashboard/{path:path}` because FastMCP processes custom routes in registration order. The catch-all path parameter would intercept the SSE endpoint if registered first.
- **Direct handler testing for SSE:** The TestClient and httpx ASGITransport both buffer streaming responses, making it impossible to read individual SSE events. Testing the handler directly and consuming the body_iterator async generator provides reliable, fast assertions without timeouts.

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered

- **venv corruption on Windows:** The `.venv/lib64` symlink caused `uv run` failures. Fixed by recreating the venv (`rm -rf .venv && uv venv && uv pip install -e ".[dev]"`). This is a known Windows issue documented in project memory.
- **SSE test buffering:** Both Starlette TestClient and httpx ASGITransport buffer streaming responses, making stream-based assertions impossible. Solved by testing the route handler directly and reading from the StreamingResponse's body_iterator async generator.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Dashboard fully accessible at `http://127.0.0.1:8321/dashboard` when broker is running
- SSE endpoint at `/dashboard/events` ready for real data events in phases 9-12
- Phase 9 (Overview tab) can add Astro components and push stats via SSE
- Phases 10-12 (Logs, Reviews, Pool) plug into the existing tab panel sections

## Self-Check: PASSED

All 3 created/modified files verified on disk. Both task commits (e758772, 8ab3a6f) verified in git log.

---
*Phase: 08-dashboard-shell-and-infrastructure*
*Completed: 2026-02-26*
