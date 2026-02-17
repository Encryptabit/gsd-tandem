---
phase: 01-core-broker-server
plan: 03
subsystem: broker-polling
tags: [mcp, polling, mcp-json, concurrency, atomicity, sqlite, testing]
requires: [01-01, 01-02]
provides:
  - get_review_status polling tool (6th MCP tool, read-only)
  - .mcp.json configuration for Claude Code MCP connectivity
  - Polling test suite (5 tests)
  - Write mutex for concurrent write serialization
  - Atomic state transitions (SELECT+validate+UPDATE inside BEGIN IMMEDIATE)
affects:
  - 02-01 (proposal creation builds on complete 6-tool surface)
  - 02-02 (verdict submission inherits atomic transition pattern)
tech-stack:
  added: []
  patterns:
    - Read-only tool pattern (no transactions for SELECT-only operations)
    - Per-process asyncio.Lock write mutex for SQLite write serialization
    - Atomic state transitions (SELECT+validate+UPDATE inside single BEGIN IMMEDIATE block)
    - MockContext extraction to conftest.py for cross-test reuse
key-files:
  created:
    - .mcp.json
    - tools/gsd-review-broker/tests/test_polling.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py
    - tools/gsd-review-broker/src/gsd_review_broker/db.py
    - tools/gsd-review-broker/tests/conftest.py
    - tools/gsd-review-broker/tests/test_tools.py
key-decisions:
  - "get_review_status is read-only (no transactions, no write_lock) for lightweight polling"
  - "Write mutex (asyncio.Lock) serializes all write operations on shared SQLite connection"
  - "SELECT+validate_transition+UPDATE all inside BEGIN IMMEDIATE for atomicity"
  - "MockContext extracted to conftest.py for cross-test reuse"
patterns-established:
  - Read-only tool pattern for polling (no transactions needed)
  - Write mutex + BEGIN IMMEDIATE for atomic state transitions
  - conftest.py MockContext extraction for test reuse
duration: ~12 minutes (includes checkpoint review)
completed: 2026-02-16
---

# Phase 1 Plan 3: Poll Tool, .mcp.json Config, and Live MCP Connectivity Summary

get_review_status read-only polling tool (6th MCP tool), .mcp.json for Claude Code connectivity, 5 polling tests covering agent identity round-trip, plus user-contributed concurrency hardening with per-process write mutex and atomic state transitions across all write tools -- 44 tests passing.

## Performance

| Metric | Value |
|--------|-------|
| Duration | ~12 minutes (includes checkpoint review) |
| Started | 2026-02-16T23:20:00Z (approx) |
| Completed | 2026-02-17T00:47:00Z |
| Tasks | 2/2 |
| Files created | 2 |
| Files modified | 4 |
| Tests | 44 passing (18 state machine + 21 tool integration + 5 polling) |

## Accomplishments

1. **get_review_status polling tool** -- 6th MCP tool implementing INTER-02 poll-and-return pattern. Read-only SELECT returning review_id, status, intent, agent identity fields, claimed_by, verdict_reason, and updated_at. No transactions or write_lock needed -- returns in <10ms on localhost.

2. **.mcp.json configuration** -- Project-root configuration file enabling Claude Code to discover and connect to the broker as an MCP server at http://127.0.0.1:8321/mcp. Tools appear as mcp__gsdreview__create_review, etc.

3. **Polling test suite** -- 5 tests validating: agent identity round-trip (INTER-01), not-found error handling, transition visibility through polling, verdict reason retrieval, and None field handling.

4. **MockContext extraction** -- Moved MockContext dataclass from test_tools.py to conftest.py for reuse across test_tools.py and test_polling.py.

5. **Write mutex and atomic state transitions (user contribution)** -- Per-process asyncio.Lock added to AppContext serializing all write operations. All write tools (claim_review, submit_verdict, close_review) now perform SELECT+validate_transition+UPDATE inside a single BEGIN IMMEDIATE block under the lock, preventing race conditions.

6. **Concurrency regression test** -- New test verifying that simultaneous claim attempts on the same review result in exactly one success and one error, validating the mutex + atomic transition pattern.

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Implement get_review_status tool, .mcp.json config, and polling tests | `7967b37` | tools.py, test_polling.py, conftest.py, test_tools.py, .mcp.json |
| 2 | Checkpoint review: write mutex and atomic state transitions | `5320baf` | db.py, tools.py, test_tools.py |

## Files Created

- `.mcp.json` -- MCP server configuration for Claude Code, pointing to gsdreview at http://127.0.0.1:8321/mcp
- `tools/gsd-review-broker/tests/test_polling.py` -- 5 polling tests covering agent identity round-trip, not-found, transition visibility, verdict reason, and None fields

## Files Modified

- `tools/gsd-review-broker/src/gsd_review_broker/tools.py` -- Added get_review_status tool; refactored claim_review, submit_verdict, close_review for atomic transitions with write_lock
- `tools/gsd-review-broker/src/gsd_review_broker/db.py` -- Added write_lock (asyncio.Lock) to AppContext for write serialization
- `tools/gsd-review-broker/tests/conftest.py` -- Extracted MockContext dataclass for cross-test reuse
- `tools/gsd-review-broker/tests/test_tools.py` -- Added concurrency regression test (simultaneous claims); updated imports for conftest MockContext

## Decisions Made

1. **Read-only polling tool** -- get_review_status uses bare SELECT with no transactions and no write_lock. Polling should be as lightweight as possible per research Pattern 5 (poll-and-return). Write safety is irrelevant for reads.

2. **Per-process write mutex** -- asyncio.Lock in AppContext serializes all write operations at the application level before they reach SQLite. This prevents TOCTOU races where two coroutines could both SELECT a valid transition and then both UPDATE.

3. **Atomic state transitions** -- SELECT (read current status) + validate_transition + UPDATE all execute inside a single BEGIN IMMEDIATE block. Combined with the write_lock, this guarantees that state machine invariants hold under concurrency.

4. **MockContext extraction to conftest.py** -- The MockContext dataclass was moved from test_tools.py to conftest.py so both test_tools.py and test_polling.py can import it without duplication.

## Checkpoint Feedback Improvements

During the human-verify checkpoint, the user reviewed the implementation and identified concurrency/atomicity gaps in the write tools. The following improvements were contributed by the user and committed as `5320baf`:

1. **Write mutex (asyncio.Lock)** -- Added to AppContext.write_lock to serialize all write operations. Without this, two concurrent coroutines could both read a valid transition state and both attempt the UPDATE, violating state machine invariants.

2. **Atomic state transitions** -- Moved the SELECT+validate+UPDATE sequence inside a single BEGIN IMMEDIATE block under the lock for claim_review, submit_verdict, and close_review. Previously, the SELECT and UPDATE were separate operations with a validation gap between them.

3. **Agent-friendly error wrapping** -- Database exceptions now return `{"error": "db_error", "detail": str(e)}` dicts instead of propagating raw exceptions to the MCP client.

4. **Concurrency regression test** -- Added test that fires two simultaneous claim_review calls on the same review and asserts exactly one succeeds while the other gets an error dict. This validates the mutex + atomic transition pattern.

## Deviations from Plan

### Checkpoint-Driven Improvements

**1. [Checkpoint Feedback] Write mutex and atomic state transitions**
- **Found during:** Task 2 (human-verify checkpoint)
- **Issue:** Write tools had TOCTOU race conditions -- SELECT to validate transition was separate from UPDATE, allowing concurrent writes to both pass validation
- **Fix:** Added asyncio.Lock to AppContext; wrapped SELECT+validate+UPDATE inside BEGIN IMMEDIATE under the lock for all write tools
- **Files modified:** db.py, tools.py, test_tools.py
- **Commit:** 5320baf

## Issues Encountered

None beyond the concurrency gaps identified during checkpoint review, which were addressed as documented above.

## Phase 1 Completion

This plan completes Phase 1 (Core Broker Server). All 5 Phase 1 success criteria are met:

1. **FastMCP server starts on 127.0.0.1 and accepts connections** -- Server binds to 127.0.0.1:8321 with streamable-http transport, .mcp.json configures Claude Code connectivity
2. **Proposer can create a review with agent identity** -- create_review stores all agent identity fields and returns review_id
3. **Reviewer can list, claim, and transition reviews** -- list_reviews, claim_review, submit_verdict, close_review implement full state machine lifecycle
4. **Proposer can poll for status within MCP timeout** -- get_review_status returns immediately (<10ms) with full review state
5. **All data persists in SQLite and survives restart** -- WAL mode with explicit transactions, checkpoint on shutdown

### Requirements Fulfilled

| Requirement | Description | How Met |
|-------------|-------------|---------|
| FOUND-01 | SQLite with WAL mode and BEGIN IMMEDIATE | db.py lifespan, all write tools use BEGIN IMMEDIATE |
| FOUND-02 | FastMCP Streamable HTTP on 127.0.0.1 | server.py binds to 127.0.0.1:8321 |
| FOUND-04 | Cross-platform (Windows + macOS/Linux) | WAL checkpoint(TRUNCATE) on shutdown for Windows file locking |
| PROTO-01 | State machine with valid transitions | state_machine.py, enforced in all write tools |
| PROTO-04 | Proposer creates, reviewer claims | create_review + claim_review tools |
| INTER-01 | Agent identity on every review | 6 agent identity fields stored and round-tripped |
| INTER-02 | Poll-and-return for proposer | get_review_status read-only tool |

### Phase 2 Readiness

Phase 2 (Proposal and Diff Protocol) is unblocked:
- All 6 MCP tools are registered and working
- State machine is enforced with atomic transitions
- Concurrency is handled with write mutex
- Test infrastructure is mature (44 tests, MockContext in conftest)
- .mcp.json enables live Claude Code connectivity
- No blockers or concerns for Phase 2

## Self-Check: PASSED
