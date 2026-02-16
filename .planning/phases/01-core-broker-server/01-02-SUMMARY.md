---
phase: 01-core-broker-server
plan: 02
subsystem: broker-tools
tags: [mcp, tools, state-machine, sqlite, lifecycle, testing]
requires: [01-01]
provides: [review-lifecycle-tools, state-machine-tests, tool-integration-tests]
affects: [01-03, 02-01, 02-02]
tech-stack:
  added: []
  patterns: [begin-immediate-transactions, dict-return-error-pattern, mockcontext-testing]
key-files:
  created:
    - tools/gsd-review-broker/tests/test_state_machine.py
    - tools/gsd-review-broker/tests/test_tools.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py
key-decisions:
  - "Tool handlers access decorated .fn attribute for direct testing via MockContext"
  - "Error cases return {error: message} dicts instead of raising exceptions for agent-friendly responses"
  - "All database writes wrapped in BEGIN IMMEDIATE / COMMIT / ROLLBACK for WAL-mode safety"
patterns-established:
  - "MockContext dataclass pattern for testing @mcp.tool decorated functions"
  - "BEGIN IMMEDIATE transaction pattern for all write operations"
  - "Dict-based error returns for invalid state transitions and not-found cases"
duration: ~4 minutes
completed: 2026-02-16
---

# Phase 1 Plan 2: MCP Tool Handlers and Test Suite Summary

5 MCP tool handlers (create_review, list_reviews, claim_review, submit_verdict, close_review) implementing the full review lifecycle with BEGIN IMMEDIATE transactions, validated state machine transitions, and 38 passing tests covering unit + integration + end-to-end lifecycle.

## Performance

- **Start:** 2026-02-16T22:43:32Z
- **End:** 2026-02-16T22:47:59Z
- **Duration:** ~4 minutes
- **Tasks:** 2/2 completed
- **Tests:** 38 passing (18 state machine + 20 tool integration)

## Accomplishments

1. **5 MCP tool handlers implemented** -- create_review, list_reviews, claim_review, submit_verdict, close_review all registered on the FastMCP server via @mcp.tool decorator
2. **Full transaction safety** -- Every database write uses BEGIN IMMEDIATE / COMMIT / ROLLBACK pattern for WAL-mode concurrent access safety
3. **State machine enforcement** -- validate_transition called before every status change; invalid transitions return {"error": "..."} instead of crashing
4. **Comprehensive test suite** -- 18 state machine tests (9 valid paths, 7 invalid paths, 2 coverage checks) + 20 tool integration tests (4 create, 3 list, 3 claim, 5 verdict, 4 close, 1 full lifecycle)
5. **Agent-friendly error handling** -- All error cases return dicts with "error" key rather than raising exceptions, providing clear messages for AI agent consumers

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Implement 5 MCP tool handlers | b939ec6 | tools/gsd-review-broker/src/gsd_review_broker/tools.py |
| 2 | Write state machine and tool tests | e32dbb5 | tests/test_state_machine.py, tests/test_tools.py |

## Files Created/Modified

### Created
- `tools/gsd-review-broker/tests/test_state_machine.py` (82 lines) -- 18 unit tests for state machine transitions
- `tools/gsd-review-broker/tests/test_tools.py` (293 lines) -- 20 integration tests for tool handlers

### Modified
- `tools/gsd-review-broker/src/gsd_review_broker/tools.py` (177 lines) -- Replaced placeholder with 5 full tool implementations

## Decisions Made

1. **MockContext pattern for testing** -- Created a minimal MockContext dataclass that provides lifespan_context to access the db fixture, allowing direct calls to `tool_function.fn()` without spinning up a full FastMCP server
2. **Dict error returns over exceptions** -- Tool handlers return `{"error": "message"}` for all expected error conditions (not found, invalid transition, invalid verdict) rather than raising, making responses predictable for AI agent consumers
3. **BEGIN IMMEDIATE for all writes** -- Even single-row UPDATEs use explicit BEGIN IMMEDIATE / COMMIT / ROLLBACK to maintain consistency under concurrent access with WAL mode

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None. All tools imported cleanly, all tests passed on first run after ruff lint fixes (4 line-length issues fixed before Task 1 commit).

## Next Phase Readiness

**Plan 01-03 (poll tool, .mcp.json, live connectivity)** is unblocked:
- All 5 lifecycle tools are registered and working
- The server starts cleanly with all tools available
- Test infrastructure (MockContext, conftest fixtures) is proven and reusable
- No blockers or concerns for the next plan

## Self-Check: PASSED
