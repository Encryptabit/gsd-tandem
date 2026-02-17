---
phase: 03-discussion-and-patches
plan: 01
subsystem: review-broker-messaging
tags: [messages, priority, notifications, asyncio, round-tracking]
requires:
  - 02-02 (tool handlers and get_proposal -- create_review is extended)
provides:
  - Messages table and Phase 3 schema migrations
  - Priority and CounterPatchStatus enums
  - infer_priority pure function for agent-based priority inference
  - NotificationBus for async review change signaling
  - add_message tool with turn alternation enforcement
  - get_discussion tool with round-based filtering
  - create_review round tracking and notification firing
affects:
  - 03-02 (counter-patch and priority-sort build on messages and notifications)
  - 04-xx (GSD integration will use notifications for long-poll)
tech-stack:
  added: []
  patterns:
    - asyncio.Event per-review notification bus
    - rowid-based ordering for deterministic turn enforcement
    - round tracking with counter-patch column clearing on revision
key-files:
  created:
    - tools/gsd-review-broker/src/gsd_review_broker/priority.py
    - tools/gsd-review-broker/src/gsd_review_broker/notifications.py
    - tools/gsd-review-broker/tests/test_priority.py
    - tools/gsd-review-broker/tests/test_notifications.py
    - tools/gsd-review-broker/tests/test_messages.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/models.py
    - tools/gsd-review-broker/src/gsd_review_broker/db.py
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py
    - tools/gsd-review-broker/tests/conftest.py
key-decisions:
  - Turn enforcement uses rowid ordering (not created_at) for deterministic behavior under fast inserts
  - Turn alternation is global across rounds (not reset per round) -- maintains conversation continuity
  - Priority is fixed at review creation time and not modified on revision
  - Notification fires outside write_lock (after COMMIT) to avoid holding lock during event signaling
duration: ~8 min
completed: 2026-02-17
---

# Phase 03 Plan 01: Discussion Foundation and Message Threading Summary

Messages table, Priority/CounterPatchStatus enums, infer_priority function, NotificationBus, add_message/get_discussion tools, and create_review round tracking with notification firing.

## Performance Metrics

- Duration: ~8 minutes
- Tests added: 36 (8 priority + 7 notification + 21 message)
- Total test count: 114 (all passing)

## Accomplishments

1. **Schema extensions**: Added messages table with review_id FK, sender_role check constraint, round, body, and metadata columns. Added 5 Phase 3 migrations (priority, current_round, counter_patch columns).

2. **Model enums**: Priority (critical/normal/low) and CounterPatchStatus (pending/accepted/rejected) StrEnums added to models.py.

3. **Priority inference**: Pure function infer_priority maps agent identity to priority level. Planner=critical (highest precedence), verify phase=low, default=normal. Case-insensitive matching.

4. **NotificationBus**: Dataclass wrapping per-review asyncio.Event instances. Supports notify (fire-and-forget), wait_for_change (with timeout), and cleanup. No-op notify when no waiter exists.

5. **add_message tool**: Inserts messages with strict turn alternation enforcement. Validates review state (claimed or changes_requested). Uses rowid-based ordering for deterministic last-message detection. Fires notification after commit.

6. **get_discussion tool**: Read-only retrieval of message thread. Supports optional round filtering. Parses metadata JSON with graceful fallback.

7. **create_review enhancements**: New reviews get priority inferred from agent identity and fire notification. Revisions increment current_round, clear counter-patch columns (counter_patch, counter_patch_affected_files, counter_patch_status), and fire notification.

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Schema, models, and utility modules | cda992e | models.py, db.py, priority.py, notifications.py, test_priority.py, test_notifications.py |
| 2 | Message threading tools and create_review round tracking | b7d716a | tools.py |
| 3 | Message threading and round tracking tests | 5a55641 | test_messages.py, tools.py (rowid fix) |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Turn enforcement ordering non-deterministic under fast inserts**

- **Found during:** Task 3 (test_full_history failing)
- **Issue:** `ORDER BY created_at DESC LIMIT 1` returns indeterminate row when multiple messages share the same second-resolution timestamp (common in fast test execution)
- **Fix:** Changed to `ORDER BY rowid DESC LIMIT 1` which is insertion-order deterministic in SQLite
- **Files modified:** tools/gsd-review-broker/src/gsd_review_broker/tools.py
- **Commit:** 5a55641

**2. [Rule 1 - Bug] Test assumed per-round turn reset**

- **Found during:** Task 3 (test_filter_by_round and test_messages_after_revision_have_new_round failing)
- **Issue:** Tests assumed turn alternation resets at round boundary, but turn enforcement is global across all rounds per the locked truth "flat chronological conversation per review"
- **Fix:** Adjusted tests to properly alternate sender_role across round boundaries
- **Files modified:** tools/gsd-review-broker/tests/test_messages.py
- **Commit:** 5a55641

## Issues Encountered

None beyond the deviations documented above.

## Decisions Made

| Decision | Context | Rationale |
|----------|---------|-----------|
| rowid-based turn ordering | created_at has second resolution, insufficient for fast sequential inserts | rowid is monotonically increasing per SQLite insert, guaranteeing insertion order |
| Global turn alternation | Plan says "flat chronological conversation" | Turn enforcement checks last message across all rounds, not per-round. Maintains conversation flow continuity. |
| Priority fixed at creation | Plan says "priority is fixed at submission time" | Revision does not recalculate priority -- it was set when the review was first created |
| Notify outside write_lock | Notification is a signal, not a DB operation | Avoids holding write_lock during asyncio.Event.set() calls |

## Next Phase Readiness

Plan 03-02 can proceed immediately. All prerequisites are in place:
- Messages table and round tracking operational
- NotificationBus integrated into AppContext and tools
- Priority column populated on new reviews
- Counter-patch columns exist (NULL by default, cleared on revision)
- 114 tests passing with zero regressions

## Self-Check: PASSED
