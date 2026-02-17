---
phase: 05-observability-and-validation
plan: 01
subsystem: observability
tags: [audit, sqlite, mcp-tools, iso8601, activity-feed, stats, timeline]

# Dependency graph
requires:
  - phase: 04-gsd-workflow-integration
    provides: category field, all state-changing tool handlers
provides:
  - audit_events table with append-only event recording
  - record_event helper for atomic audit insertion
  - AuditEventType StrEnum with 10 event types
  - get_activity_feed MCP tool with message previews and filtering
  - get_audit_log MCP tool for event history
  - get_review_stats MCP tool with counts, rates, timing metrics
  - get_review_timeline MCP tool for chronological event sequence
affects: [05-02 (E2E validation tests will exercise observability tools)]

# Tech tracking
tech-stack:
  added: []
  patterns:
    - "Audit event recording inside BEGIN IMMEDIATE...COMMIT transactions"
    - "Correlated subqueries for activity feed with message previews"
    - "LEAD() window function for average time-in-state calculation"
    - "ISO 8601 timestamp formatting via strftime('%Y-%m-%dT%H:%M:%fZ', col)"
    - "json_extract for querying JSON metadata in audit events"

key-files:
  created:
    - tools/gsd-review-broker/src/gsd_review_broker/audit.py
    - tools/gsd-review-broker/tests/test_audit.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/db.py
    - tools/gsd-review-broker/src/gsd_review_broker/models.py
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py

key-decisions:
  - "Audit events use string values directly, not AuditEventType enum references, since the column is TEXT"
  - "Activity feed uses correlated subqueries (not JOINs) for simplicity at small data volumes"
  - "Approval rate computed from audit_events verdict_submitted events using json_extract, not from reviews table status"
  - "Average time-in-state uses LEAD() window function over consecutive audit events"
  - "All new tool output uses ISO 8601 timestamps; existing tools unchanged for backward compatibility"

patterns-established:
  - "record_event(db, review_id, event_type, ...) called before COMMIT in every write handler"
  - "New observability tools return structured dicts with count field alongside data arrays"
  - "ISO 8601 formatting at SQL query level via strftime for output, not Python post-processing"

# Metrics
duration: 6min
completed: 2026-02-17
---

# Phase 5 Plan 01: Audit and Observability Tools Summary

**Append-only audit_events table with record_event helper, 10 audit wiring points across all handlers, and 4 new MCP tools (activity feed, audit log, stats with time-in-state, review timeline)**

## Performance

- **Duration:** 6 min
- **Started:** 2026-02-17T22:08:52Z
- **Completed:** 2026-02-17T22:14:56Z
- **Tasks:** 2
- **Files modified:** 5

## Accomplishments
- Audit infrastructure: audit_events table, AuditEventType enum, record_event helper with 4 passing tests
- Wired audit events into all 10 handler paths covering 11 state-changing operations (create new/revision, claim success/auto-reject, verdict comment/standard, close, accept/reject counter-patch, add message)
- 4 new MCP tools: get_activity_feed (with message previews, status/category filtering), get_audit_log (per-review or all events), get_review_stats (counts, approval rate, timing metrics, avg time-in-state), get_review_timeline (chronological event sequence)
- All 151 tests pass (145 existing + 4 new audit tests + 2 schema tests)

## Task Commits

Each task was committed atomically:

1. **Task 1: Audit infrastructure** - `ff6648a` (feat)
2. **Task 2: Wire audit + add observability tools** - `fd6b0b9` (feat)

## Files Created/Modified
- `tools/gsd-review-broker/src/gsd_review_broker/audit.py` - record_event async helper for atomic audit insertion
- `tools/gsd-review-broker/tests/test_audit.py` - 4 tests for record_event (basic, minimal, ISO 8601, sequential IDs)
- `tools/gsd-review-broker/src/gsd_review_broker/db.py` - Phase 5 schema migration (audit_events table + 2 indexes)
- `tools/gsd-review-broker/src/gsd_review_broker/models.py` - AuditEventType StrEnum with 10 event types
- `tools/gsd-review-broker/src/gsd_review_broker/tools.py` - 10 record_event calls in handlers + 4 new MCP tools

## Decisions Made
- Used string values directly for event_type parameter (not enum references) since the audit_events column is TEXT -- keeps handler code simpler
- Activity feed uses correlated subqueries rather than JOINs with GROUP BY -- simpler SQL for small data volumes
- Approval rate computed from audit_events (verdict_submitted events with json_extract) rather than reviews table status -- more accurate since reviews change status over time
- Average time-in-state uses SQLite LEAD() window function over consecutive audit events, excluding open-ended durations (no next event)
- ISO 8601 timestamps applied only to new tool output; existing tools unchanged for backward compatibility

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Audit infrastructure complete and wired into all handlers
- 4 observability tools available for E2E validation testing in Plan 05-02
- All 151 tests pass; existing test behavior unaffected by audit wiring (audit rows added to DB but not queried by existing tests)
- Pre-Phase 5 reviews will have no audit trail (expected; stats tools return null for metrics that cannot be computed)

## Self-Check: PASSED

---
*Phase: 05-observability-and-validation*
*Completed: 2026-02-17*
