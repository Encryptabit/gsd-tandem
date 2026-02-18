---
phase: 06-review-gate-enforcement
plan: 01
subsystem: review-broker, workflow
tags: [skip-validation, orchestrator-review-gate, tandem, diff-validation]

# Dependency graph
requires:
  - phase: 05-observability-and-validation
    provides: audit events, observability tools
  - phase: 04-gsd-workflow-integration
    provides: tandem config, review gate sections in executor and workflow
provides:
  - skip_diff_validation on create_review and claim_review for post-commit diffs
  - Orchestrator-mediated review gate (executor no longer touches broker)
  - CLAUDE.md tandem review rule for ad-hoc changes
affects: [gsd-executor, execute-phase-workflow, review-broker-tools]

# Tech tracking
tech-stack:
  added: []
  patterns: [orchestrator-mediated review, skip-validation for post-commit diffs]

key-files:
  created:
    - tools/gsd-review-broker/tests/test_skip_validation.py
    - CLAUDE.md
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/db.py
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py
    - agents/gsd-executor.md
    - get-shit-done/workflows/execute-phase.md

key-decisions:
  - "Orchestrator-mediated review: only execute-phase workflow interacts with broker, executor subagents focus on execution"
  - "skip_diff_validation persisted as INTEGER column (0/1) on reviews table, respected by both create_review and claim_review"
  - "Wave parallelism forced off when tandem_enabled=true to ensure clean per-plan diffs"
  - "CLAUDE.md removed from .gitignore to share tandem review rule with team"

patterns-established:
  - "Post-commit diffs use skip_diff_validation=true since changes are already applied"
  - "Orchestrator captures PLAN_START_REF before spawning executor, diffs after completion"

requirements-completed: []

# Metrics
duration: 9min
completed: 2026-02-18
---

# Phase 6 Plan 1: Review Gate Enforcement Summary

**Orchestrator-mediated review gate with skip_diff_validation for post-commit diffs, removing dead broker code from executor**

## Performance

- **Duration:** 9 min
- **Started:** 2026-02-18T06:39:06Z
- **Completed:** 2026-02-18T06:48:00Z
- **Tasks:** 4
- **Files modified:** 7

## Accomplishments
- Added skip_diff_validation column + parameter to create_review and claim_review, enabling post-commit diffs to bypass git apply --check
- Removed all tandem/broker sections from executor agent (171 lines of dead code), replaced with single note about orchestrator-mediated review
- Wired orchestrator review gate in execute-phase workflow: tandem config loading, parallelism override, per-plan diff capture + broker submission + verdict polling
- Created CLAUDE.md with tandem review instructions for ad-hoc changes outside GSD workflow

## Task Commits

Each task was committed atomically:

1. **Task 1: Persist skip_diff_validation and update create_review + claim_review** - `ffb7adb` (feat)
2. **Task 2: Simplify executor agent -- remove broker access** - `6aca547` (refactor)
3. **Task 3: Wire orchestrator review gate in execute-phase workflow** - `bc28bcd` (feat)
4. **Task 4: Add CLAUDE.md tandem rule** - `e8f57c4` (feat)

## Files Created/Modified
- `tools/gsd-review-broker/src/gsd_review_broker/db.py` - Added skip_diff_validation column migration
- `tools/gsd-review-broker/src/gsd_review_broker/tools.py` - create_review accepts skip_diff_validation, claim_review respects persisted flag
- `tools/gsd-review-broker/tests/test_skip_validation.py` - 10 tests covering create, claim, and revision with skip flag
- `agents/gsd-executor.md` - Removed broker tools from frontmatter, replaced 5 tandem sections with single note
- `get-shit-done/workflows/execute-phase.md` - Added tandem config loading, parallelism override, per-plan review gate
- `CLAUDE.md` - Tandem review instructions for ad-hoc changes
- `.gitignore` - Removed CLAUDE.md exclusion

## Decisions Made
- Orchestrator-mediated review: executor subagents never call mcp__gsdreview__* -- the execute-phase workflow handles all broker interaction after plan execution completes
- skip_diff_validation stored as INTEGER (0/1) with NOT NULL DEFAULT 0 -- backwards compatible with existing reviews
- Wave parallelism forced off when tandem_enabled=true to ensure git diff PLAN_START_REF..HEAD isolates exactly one plan's changes
- Removed CLAUDE.md from .gitignore so tandem review rule is version-controlled and shared

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 3 - Blocking] Removed CLAUDE.md from .gitignore**
- **Found during:** Task 4 (Add CLAUDE.md tandem rule)
- **Issue:** CLAUDE.md was listed in .gitignore, preventing the required artifact from being committed
- **Fix:** Removed the CLAUDE.md line from .gitignore
- **Files modified:** .gitignore
- **Verification:** git add CLAUDE.md succeeds without -f flag
- **Committed in:** e8f57c4 (Task 4 commit)

---

**Total deviations:** 1 auto-fixed (1 blocking)
**Impact on plan:** Necessary to fulfill the plan's requirement of a committed CLAUDE.md. No scope creep.

## Issues Encountered
None

## User Setup Required
None - no external service configuration required.

## Next Phase Readiness
- Review broker now supports post-commit diffs with skip_diff_validation
- Orchestrator workflow wired to submit diffs and poll for verdicts when tandem_enabled=true
- Executor is simplified -- no broker dependency, no dead tandem code
- 219 tests passing (209 existing + 10 new)

## Self-Check: PASSED

All artifacts verified:
- [x] `tools/gsd-review-broker/tests/test_skip_validation.py` - FOUND
- [x] `CLAUDE.md` - FOUND
- [x] Commit `ffb7adb` (Task 1) - FOUND
- [x] Commit `6aca547` (Task 2) - FOUND
- [x] Commit `bc28bcd` (Task 3) - FOUND
- [x] Commit `e8f57c4` (Task 4) - FOUND
- [x] 219 tests passing

---
*Phase: 06-review-gate-enforcement*
*Completed: 2026-02-18*
