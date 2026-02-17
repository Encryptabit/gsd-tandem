---
phase: "02"
plan: "02"
subsystem: "mcp-tools"
tags: ["mcp", "proposal", "diff-validation", "revision", "verdict", "tools"]
dependency-graph:
  requires: ["02-01"]
  provides: ["proposal-tool-surface", "diff-validation-on-submit-and-claim", "comment-verdict", "get-proposal", "revision-flow"]
  affects: ["03-01", "03-02", "04-01"]
tech-stack:
  added: []
  patterns: ["mock-validate_diff-in-tests", "read-only-tool-pattern", "strict-notes-enforcement", "auto-rejection-on-invalid-diff"]
key-files:
  created:
    - "tools/gsd-review-broker/tests/test_proposals.py"
  modified:
    - "tools/gsd-review-broker/src/gsd_review_broker/tools.py"
    - "tools/gsd-review-broker/src/gsd_review_broker/db.py"
    - "tools/gsd-review-broker/tests/test_db_schema.py"
    - "tools/gsd-review-broker/tests/test_tools.py"
    - "tools/gsd-review-broker/tests/conftest.py"
decisions:
  - id: "02-02-01"
    description: "Mock validate_diff in proposal tests rather than setting up real git repos"
  - id: "02-02-02"
    description: "Diff validation runs on submit/revision and re-runs inside write_lock on claim for defense in depth"
  - id: "02-02-03"
    description: "claim_review returns proposal metadata but NOT full diff text (use get_proposal for that)"
  - id: "02-02-04"
    description: "Comment verdict does not trigger state transition -- updates verdict_reason only"
  - id: "02-02-05"
    description: "Notes enforcement: non-whitespace notes required for changes_requested and comment, optional for approved"
  - id: "02-02-06"
    description: "Test constants (SAMPLE_DIFF etc.) defined locally in test_proposals.py to avoid conftest import issues"
  - id: "02-02-07"
    description: "Schema migrations ignore only duplicate-column errors; unrelated SQL failures are raised"
metrics:
  duration: "~6 minutes"
  completed: "2026-02-17"
---

# Phase 02 Plan 02: Extend Tool Handlers and Add get_proposal Summary

**Extended all MCP tool handlers for proposal content (description, diff, revision) and added get_proposal read-only tool with diff validation on submit and claim plus strict comment-verdict note rules**

## What Was Done

### Task 1: Extend tool handlers and add get_proposal
Extended tools.py with all Phase 2 tool surface changes:

1. **create_review** -- Added `description`, `diff`, `review_id` parameters. New review flow stores description/diff/affected_files. Revision flow (when `review_id` provided) validates changes_requested state, replaces content, clears claimed_by/verdict_reason, returns to pending. When `diff` is provided, the tool now runs `validate_diff(..., cwd=repo_root)` before any persistence and rejects invalid diffs.

2. **claim_review** -- Extended SELECT to fetch diff/intent/description/affected_files. When diff present, re-runs `validate_diff()` inside write_lock as a final guard. Invalid diff triggers auto-rejection to changes_requested with error details. Successful claim returns metadata (intent, description, affected_files, has_diff) but NOT the full diff text.

3. **submit_verdict** -- Added "comment" verdict type that records feedback without state transition. Enforces non-whitespace notes requirement for changes_requested and comment verdicts (whitespace-only is rejected). Approve remains notes-optional. Updated invalid verdict error message to list all three options.

4. **get_proposal** -- New read-only `@mcp.tool` that SELECTs full proposal content (id, status, intent, description, diff, affected_files). Parses affected_files JSON to list. No write_lock needed.

**Commit:** `19d7eba`

### Task 2: Proposal lifecycle tests and verdict extension tests
Added comprehensive test coverage:

- Extended **test_tools.py** with 6 verdict tests covering required notes plus whitespace-only rejection behavior.

- Created **test_proposals.py** with 17 tests across 5 test classes:
  - `TestCreateReviewWithProposal` (5 tests): description+diff, submission validation call/cwd, invalid diff rejection, description only, multi-file affected_files extraction
  - `TestRevisionFlow` (4 tests): content replacement, invalid revised diff rejection, wrong state fails, not found fails
  - `TestClaimWithDiffValidation` (3 tests): no diff succeeds, metadata returned, bad diff auto-rejects
  - `TestGetProposal` (3 tests): returns content, not found, without diff
  - `TestFullProposalLifecycle` (2 tests): submit-claim-approve-close, submit-reject-revise-approve

- Extended **test_db_schema.py** with migration hardening coverage to ensure non-duplicate SQL migration errors are not silently suppressed.

**Commit:** `e760d60`

## Decisions Made

| ID | Decision | Rationale |
|----|----------|-----------|
| 02-02-01 | Mock validate_diff in proposal tests | Avoids real git repo setup; validate_diff already tested in test_diff_utils.py |
| 02-02-02 | Diff validation on submit and claim | Submission catches bad proposals early; claim-time revalidation handles repo drift and preserves concurrency safety |
| 02-02-03 | claim_review omits full diff from response | Avoids MCP output size issues; reviewer uses get_proposal for full diff |
| 02-02-04 | Comment verdict has no state transition | Comments are feedback, not decisions; status stays claimed/in_review |
| 02-02-05 | Notes enforcement by verdict type | changes_requested and comment require non-whitespace context; approve is self-explanatory |
| 02-02-06 | Test constants in test_proposals.py | conftest.py is auto-loaded by pytest but not directly importable as module |
| 02-02-07 | Migration error handling tightened | Only duplicate-column errors are ignored; real SQL issues now surface immediately |

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Fixed existing test_close_review_after_changes_requested test**

- **Found during:** Task 1
- **Issue:** Existing test called submit_verdict with verdict="changes_requested" but no reason. New notes enforcement made this return an error instead of succeeding, breaking the test.
- **Fix:** Added `reason="Needs refactor"` to the submit_verdict call in the existing test.
- **Files modified:** tools/gsd-review-broker/tests/test_tools.py
- **Commit:** 19d7eba (included in Task 1 commit)

**2. [Rule 3 - Blocking] Moved test constants from conftest.py to test_proposals.py**

- **Found during:** Task 2
- **Issue:** Plan specified adding constants to conftest.py, but `from conftest import ...` fails because pytest's conftest is not a regular importable module.
- **Fix:** Defined SAMPLE_DIFF, SAMPLE_MULTI_FILE_DIFF, SAMPLE_DESCRIPTION as module-level constants directly in test_proposals.py. Kept conftest.py unchanged (fixtures only).
- **Files modified:** tools/gsd-review-broker/tests/test_proposals.py
- **Commit:** e760d60

## Task Commits

| Task | Commit | Description |
|------|--------|-------------|
| 1 | 19d7eba | feat(02-02): extend tool handlers and add get_proposal |
| 2 | e760d60 | test(02-02): proposal lifecycle tests and verdict extension tests |

## Test Results

```
78 passed in 0.61s
```

Breakdown: full suite currently passes at 78 tests after submission+claim validation and hardening updates.

## Next Phase Readiness

Phase 2 is now complete. All tool surface work is done:
- 7 MCP tools: create_review, list_reviews, claim_review, submit_verdict, get_review_status, close_review, get_proposal
- Full proposal lifecycle: submit with diff, validate on submit + claim, typed verdicts, revision, content retrieval
- 78 tests covering all functionality

Ready for Phase 3 (Watcher and Integration).

## Self-Check: PASSED
