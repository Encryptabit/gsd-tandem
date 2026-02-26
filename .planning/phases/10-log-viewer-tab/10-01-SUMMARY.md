---
phase: 10-log-viewer-tab
plan: 01
subsystem: api
tags: [jsonl, sse, log-viewer, streaming, starlette]

# Dependency graph
requires:
  - phase: 08-dashboard-shell-and-infrastructure
    provides: Dashboard static serving, SSE endpoint, route registration pattern
  - phase: 09-overview-tab
    provides: Overview API and SSE patterns, AppContext setup, query helpers
provides:
  - Log listing API endpoint returning JSONL files from broker-logs/ and reviewer-logs/
  - Log file reading API endpoint with path traversal protection
  - SSE log tail streaming for real-time log entry delivery
affects: [10-log-viewer-tab]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "_resolve_log_file() with Path.relative_to() containment check for log security"
    - "SSE dual-interval loop: 2s log tail + 15s overview heartbeat via tick counter"
    - "monkeypatch.setenv for BROKER_LOG_DIR/BROKER_REVIEWER_LOG_DIR in log tests"

key-files:
  created: []
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/dashboard.py
    - tools/gsd-review-broker/tests/test_dashboard.py

key-decisions:
  - "Duplicated _default_user_config_dir() in dashboard.py to avoid cross-module imports (same pattern as _query_review_stats)"
  - "SSE loop restructured to tick-based dual-interval: sleep SSE_LOG_TAIL_INTERVAL per iteration, fire overview_update when tick accumulates to SSE_HEARTBEAT_INTERVAL"
  - "Log tail re-resolves filename on each tick so files that appear after SSE connection start get picked up"

patterns-established:
  - "Log file resolution: _resolve_log_file() tries broker-logs/ then reviewer-logs/ with Path.relative_to() security"
  - "SSE ?tail query parameter for subscribing to real-time log updates"
  - "File rotation detection via size comparison (shrink = rotation, reset position to 0)"

requirements-completed: [LOGS-01, LOGS-02]

# Metrics
duration: 11min
completed: 2026-02-26
---

# Phase 10 Plan 01: Log API Backend Summary

**JSONL log listing, file reading, and SSE live tail endpoints with path traversal protection and 6 new tests**

## Performance

- **Duration:** 11 min
- **Started:** 2026-02-26T19:59:51Z
- **Completed:** 2026-02-26T20:11:20Z
- **Tasks:** 2
- **Files modified:** 2

## Accomplishments
- Three new API routes: /dashboard/api/logs (listing), /dashboard/api/logs/{filename} (reading), SSE ?tail= (live streaming)
- Path traversal protection on log file endpoints using Path.relative_to() containment
- SSE dual-interval architecture: 2-second log tail polling alongside 15-second overview heartbeat
- File rotation handling: detects file size shrink and resets read position
- 6 new tests covering listing, reading, 404, path traversal, and SSE log tail

## Task Commits

Each task was committed atomically:

1. **Task 1: Add log listing and log file reading API endpoints** - `442cf0e` (feat)
2. **Task 2: Add SSE log tail streaming and log endpoint tests** - `94590b2` (feat)

## Files Created/Modified
- `tools/gsd-review-broker/src/gsd_review_broker/dashboard.py` - Added _default_user_config_dir, _resolve_broker_log_dir, _resolve_reviewer_log_dir, _list_log_files, _resolve_log_file helpers; GET /dashboard/api/logs and /dashboard/api/logs/{filename} endpoints; SSE ?tail= log tail streaming
- `tools/gsd-review-broker/tests/test_dashboard.py` - Added 6 new log tests; fixed 3 existing SSE tests to provide proper query_params mock

## Decisions Made
- Duplicated _default_user_config_dir() in dashboard.py rather than importing from server.py/pool.py, following the established pattern of self-contained dashboard helpers
- Restructured SSE loop from simple sleep(heartbeat) to tick-based dual interval (2s tail check, 15s overview), keeping overview_update behavior unchanged
- Used {filename:path} Starlette route parameter to handle filenames containing dots (e.g., broker.jsonl.1)

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed SSE MagicMock query_params causing test failures**
- **Found during:** Task 2 (SSE tests)
- **Issue:** Existing SSE tests used bare MagicMock() for request, causing request.query_params.get("tail") to return a truthy MagicMock instead of None, which triggered tail resolution logic on garbage input
- **Fix:** Added `request.query_params = {}` to 3 existing SSE test functions
- **Files modified:** tools/gsd-review-broker/tests/test_dashboard.py
- **Verification:** All 16 existing tests pass
- **Committed in:** 94590b2 (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Necessary fix for test compatibility with new ?tail parameter. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Log API backend complete, ready for frontend log viewer tab (10-02-PLAN.md)
- SSE log tail streaming operational for frontend subscription
- All 21 dashboard tests pass (15 existing + 6 new)

---
*Phase: 10-log-viewer-tab*
*Completed: 2026-02-26*
