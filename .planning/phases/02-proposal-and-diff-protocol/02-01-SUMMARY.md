---
phase: 02-proposal-and-diff-protocol
plan: 01
subsystem: database, api
tags: [sqlite, unidiff, schema-migration, git-apply, asyncio-subprocess, pydantic]

# Dependency graph
requires:
  - phase: 01-core-broker-server
    provides: SQLite schema V1, AppContext, broker_lifespan, Review model, test infrastructure
provides:
  - Schema V2 with description, diff, affected_files columns and migration support
  - diff_utils module with validate_diff and extract_affected_files
  - repo_root discovery cached in AppContext
  - Updated Review model with proposal content fields
affects: [02-proposal-and-diff-protocol plan 02, phase 03]

# Tech tracking
tech-stack:
  added: [unidiff>=0.7.5]
  patterns: [SCHEMA_MIGRATIONS list with duplicate-column-only handling for idempotent ALTER TABLE, async subprocess for git CLI operations, discover_repo_root at lifespan startup]

key-files:
  created:
    - tools/gsd-review-broker/src/gsd_review_broker/diff_utils.py
    - tools/gsd-review-broker/tests/test_diff_utils.py
    - tools/gsd-review-broker/tests/test_db_schema.py
  modified:
    - tools/gsd-review-broker/pyproject.toml
    - tools/gsd-review-broker/uv.lock
    - tools/gsd-review-broker/src/gsd_review_broker/db.py
    - tools/gsd-review-broker/src/gsd_review_broker/models.py

key-decisions:
  - "SCHEMA_MIGRATIONS as a list of ALTER TABLE statements with duplicate-column-only handling for idempotent Phase 1 -> Phase 2 migration"
  - "discover_repo_root uses git rev-parse --show-toplevel via asyncio subprocess, cached once in AppContext at startup"
  - "validate_diff delegates to git apply --check via async subprocess with stdin pipe"
  - "extract_affected_files uses unidiff.PatchSet with graceful fallback to empty JSON array on parse failure"

patterns-established:
  - "Schema migration pattern: SCHEMA_MIGRATIONS list iterated in ensure_schema with duplicate-column-only skip"
  - "Async subprocess pattern: asyncio.create_subprocess_exec for git CLI operations with communicate()"

# Metrics
duration: 5min
completed: 2026-02-17
---

# Phase 2 Plan 01: Data Layer and Diff Utilities Summary

**Schema V2 with description/diff/affected_files columns, unidiff-based diff parser, async git apply validation, and repo root discovery cached at startup**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-17T02:37:15Z
- **Completed:** 2026-02-17T02:42:12Z
- **Tasks:** 2
- **Files modified:** 7

## Accomplishments
- Extended SQLite schema with 3 new columns (description, diff, affected_files) and idempotent migration for existing Phase 1 databases
- Created diff_utils.py with validate_diff (async git apply --check) and extract_affected_files (unidiff PatchSet parser)
- Added repo_root discovery to AppContext via git rev-parse --show-toplevel at server startup
- Full test coverage: 10 new tests (9 diff utility + 1 migration safety), all 54 tests pass

## Task Commits

Each task was committed atomically:

1. **Task 1: Schema evolution, diff_utils module, model updates, and repo root discovery** - `fed5135` (feat)
2. **Task 2: Diff utilities and schema migration safety tests** - `97744f9` (test)

**Plan metadata:** `e7c08df` (docs: complete plan)

## Files Created/Modified
- `tools/gsd-review-broker/src/gsd_review_broker/diff_utils.py` - validate_diff and extract_affected_files functions
- `tools/gsd-review-broker/src/gsd_review_broker/db.py` - Schema V2 with SCHEMA_MIGRATIONS, discover_repo_root, AppContext.repo_root
- `tools/gsd-review-broker/src/gsd_review_broker/models.py` - Review model with description, diff, affected_files fields
- `tools/gsd-review-broker/pyproject.toml` - Added unidiff dependency
- `tools/gsd-review-broker/uv.lock` - Updated lockfile
- `tools/gsd-review-broker/tests/test_diff_utils.py` - 9 tests for diff utility functions
- `tools/gsd-review-broker/tests/test_db_schema.py` - Migration safety test for Phase 1 -> Phase 2

## Decisions Made
- SCHEMA_MIGRATIONS as a list of ALTER TABLE statements with duplicate-column-only handling for idempotent Phase 1 -> Phase 2 migration
- discover_repo_root uses git rev-parse --show-toplevel via asyncio subprocess, cached once in AppContext at startup
- validate_diff delegates to git apply --check via async subprocess with stdin pipe
- extract_affected_files uses unidiff.PatchSet with graceful fallback to empty JSON array on parse failure

## Deviations from Plan

None - plan executed exactly as written.

## Issues Encountered
- MODIFY_DIFF test data had incorrect hunk header (`@@ -1,3` for a 2-line context), causing unidiff parse error. Fixed by correcting the hunk header to `@@ -1,2 +1,2 @@`.

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness
- Schema V2 and diff_utils module ready for Plan 02 (tool extensions for create_review with proposals)
- repo_root cached in AppContext ready for diff validation in tool handlers
- All existing tests pass with zero regressions

## Self-Check: PASSED

---
*Phase: 02-proposal-and-diff-protocol*
*Completed: 2026-02-17*
