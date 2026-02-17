---
phase: 03-discussion-and-patches
plan: 02
subsystem: review-broker-counter-patches
tags: [counter-patch, accept, reject, priority-sort, notification-polling, lifecycle]
requires:
  - 03-01 (messages, priority, notifications, round tracking, counter-patch columns)
provides:
  - Counter-patch submission via submit_verdict (changes_requested/comment)
  - accept_counter_patch tool (re-validates, replaces active diff)
  - reject_counter_patch tool (clears counter-patch columns)
  - list_reviews priority-based sorting (critical > normal > low)
  - get_review_status notification-enhanced polling (wait parameter)
  - Comprehensive counter-patch lifecycle tests (18 tests)
affects:
  - 04-xx (GSD integration will use accept/reject tools and priority sorting)
  - 04-xx (Long-poll via get_review_status wait=True reduces latency)
tech-stack:
  added: []
  patterns:
    - Counter-patch validation before storage and re-validation on accept
    - COALESCE-based priority sort in SQL ORDER BY
    - Monkeypatch-based timeout simulation for notification polling tests
key-files:
  created:
    - tools/gsd-review-broker/tests/test_counter_patch.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py
key-decisions:
  - Counter-patches restricted to changes_requested and comment verdicts only (not approved)
  - Re-validation on accept prevents stale counter-patches from replacing active diff
  - Stale counter-patch accept returns error without modifying review state
  - Reject sets counter_patch_status to 'rejected' and NULLs patch content columns
  - Revision clears all counter-patch columns (patch, affected_files, status)
duration: ~5 min
completed: 2026-02-17
---

# Phase 03 Plan 02: Counter-patch Lifecycle, Priority Sort, and Notification Polling Summary

Counter-patch submission/acceptance/rejection lifecycle with diff re-validation, priority-sorted list_reviews, notification-driven get_review_status polling, and 18 comprehensive tests covering all paths.

## Performance Metrics

- Duration: ~5 minutes
- Tests added: 18 counter-patch lifecycle tests
- Total test count: 132 (all passing, zero regressions)

## Accomplishments

1. **Counter-patch in submit_verdict**: Reviewer can attach a counter-patch diff to changes_requested or comment verdicts. The diff is validated via validate_diff before storage. Stored with counter_patch_status='pending' for proposer action. Rejected on approved verdicts.

2. **accept_counter_patch tool**: Proposer accepts a pending counter-patch. Re-validates the diff against the working tree (catches stale patches). On success: replaces active diff and affected_files, sets counter_patch_status='accepted', clears counter_patch content. On stale: returns error without modifying state. Fires notification after commit.

3. **reject_counter_patch tool**: Proposer rejects a pending counter-patch. Sets counter_patch_status='rejected', NULLs counter_patch and counter_patch_affected_files. Fires notification after commit.

4. **list_reviews priority sorting**: ORDER BY uses CASE COALESCE(priority, 'normal') to sort critical first, then normal, then low. Within same priority tier, sorts by created_at ASC.

5. **get_review_status notification polling**: New wait parameter (default False). When wait=True, calls notifications.wait_for_change with 25s timeout before querying DB. Response includes priority and current_round fields.

6. **Revision clears counter-patch**: create_review revision flow NULLs counter_patch, counter_patch_affected_files, and counter_patch_status columns alongside incrementing current_round.

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Counter-patch in submit_verdict, accept/reject tools, priority sort, notification polling | 845b46b | tools.py |
| 2 | Counter-patch lifecycle, priority sort, and notification polling tests | dd1aa11 | test_counter_patch.py |

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None.

## Decisions Made

| Decision | Context | Rationale |
|----------|---------|-----------|
| Counter-patches only on changes_requested/comment | Approved verdicts are terminal positive feedback | No reason to suggest alternative code when approving |
| Re-validate on accept (not just submit) | Working tree may change between verdict and accept | Prevents applying a diff that no longer applies cleanly |
| Stale accept is error, not state change | Proposer should explicitly reject stale patches | Avoids silent data corruption; proposer retains choice |
| Reject NULLs content but keeps status='rejected' | Audit trail of rejection | Status column preserves history while freeing content storage |

## Next Phase Readiness

Phase 3 is complete. All Phase 3 features are operational:
- Message threading with turn alternation (03-01)
- Priority inference and notification bus (03-01)
- Counter-patch lifecycle: submit, accept, reject (03-02)
- Priority-sorted list_reviews (03-02)
- Notification-enhanced polling (03-02)
- 132 tests passing with zero regressions

Phase 4 (GSD Integration) can proceed.

## Self-Check: PASSED
