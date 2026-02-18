# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-16)

**Core value:** Every meaningful change Claude makes gets reviewed incrementally by a second intelligence before being applied
**Current focus:** Phase 7 complete -- reviewer lifecycle management

## Current Position

Phase: 7 of 7 (Add Reviewer Lifecycle Management to Broker)
Plan: 4 of 4 in current phase
Status: Complete
Last activity: 2026-02-18 -- Completed 07-04-PLAN.md

Progress: [############] 100% (17/17 plans)

## Performance Metrics

**Velocity:**
- Total plans completed: 17
- Average duration: ~6.2 minutes
- Total execution time: ~105 minutes

**By Phase:**

| Phase | Plans | Total | Avg/Plan |
|-------|-------|-------|----------|
| 1. Core Broker Server | 3/3 | ~21 min | ~7 min |
| 2. Proposal and Diff Protocol | 2/2 | ~11 min | ~5.5 min |
| 3. Discussion and Patches | 2/2 | ~13 min | ~6.5 min |
| 4. GSD Workflow Integration | 3/3 | ~11 min | ~3.7 min |
| 5. Observability and Validation | 2/2 | ~14 min | ~7 min |
| 6. Review Gate Enforcement | 1/1 | ~9 min | ~9 min |
| 7. Reviewer Lifecycle Management | 4/4 | ~60 min | ~15 min |

**Recent Trend:**
- Last 5 plans: 06-01 (~9 min), 07-01 (~18 min), 07-02 (~24 min), 07-03 (~31 min), 07-04 (~29 min)
- Trend: Increased complexity in Phase 7 due cross-cutting broker/runtime/test integration

*Updated after each plan completion*

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.
Recent decisions affecting current work:

- [01-02]: MockContext dataclass pattern for testing @mcp.tool decorated functions via .fn attribute
- [01-02]: Dict error returns over exceptions for agent-friendly tool responses
- [01-02]: BEGIN IMMEDIATE / COMMIT / ROLLBACK for all database write operations
- [01-03]: Per-process asyncio.Lock write mutex serializes all write operations on shared SQLite connection
- [02-01]: SCHEMA_MIGRATIONS catches only duplicate-column migration errors
- [02-02]: Mock validate_diff in proposal tests rather than setting up real git repos
- [03-01]: Priority fixed at review creation time, not modified on revision
- [03-02]: Counter-patches restricted to changes_requested and comment verdicts only
- [04-01]: Category stored as free-text for forward compatibility; StrEnum provides canonical constants
- [04-01]: Category set at creation time, not modified on revision
- [04-01]: Tandem config defaults: tandem_enabled=false, review_granularity=per_task, execution_mode=blocking
- [04-02]: Gate placement before write/commit (not after) so reviewer approves before finalization
- [04-02]: Solo mode fallback on first broker connection failure for entire execution
- [04-02]: Category mapping: plan_review for plans, handoff for context, verification for UAT
- [04-03]: Tandem review gate inserts before task_commit_protocol as additive step
- [04-03]: Per-plan optimistic normalized to per-plan blocking for v1
- [04-03]: Optimistic rejection reverts all commits from rejected task onward (deterministic cascade)
- [04-03]: Broker connection error falls back to solo mode for entire session
- [05-01]: Audit events use string values directly (not enum references) since column is TEXT
- [05-01]: Activity feed uses correlated subqueries for simplicity at small data volumes
- [05-01]: Approval rate from audit_events verdict_submitted events via json_extract
- [05-01]: Average time-in-state uses LEAD() window function over consecutive audit events
- [05-01]: ISO 8601 timestamps only on new tool output; existing tools unchanged
- [06-01]: Orchestrator-mediated review: only execute-phase workflow interacts with broker, executor subagents focus on execution
- [06-01]: skip_diff_validation persisted as INTEGER column on reviews table, respected by both create_review and claim_review
- [06-01]: Wave parallelism forced off when tandem_enabled=true to ensure clean per-plan diffs
- [06-01]: CLAUDE.md removed from .gitignore so tandem review rule is version-controlled
- [07-01]: reviewer_pool missing-key behavior resolves to disabled pool (`None`) for backward compatibility
- [07-02]: reviewer subprocesses use shell-free argv and DEVNULL stdio to avoid long-running pipe deadlocks
- [07-02]: spawn DB-write failure path terminates orphan subprocesses before returning error
- [07-03]: claim fencing is strict for broker-managed reviewers while preserving legacy manual-claim compatibility
- [07-04]: startup recovery performs ownership sweep reclaim for all claimed reviews not owned by live current-session reviewers

### Pending Todos

- Future Goal: Add a master-reviewer orchestration layer for reviewer pools. Master reviewer is the single verdict authority, tracks file/symbol ownership across active reviews, reconciles overlap/conflicts from sub-reviewers, and delegates non-overlapping review packets back to sub-reviewers.

### Roadmap Evolution

- Phase 7 executed and completed

### Blockers/Concerns

- Research Gap 3: Codex MCP client capabilities unverified -- test early in Phase 1
- Research Gap 2: Claude Code MCP_TIMEOUT configuration unclear -- determine empirically in Phase 1

## Session Continuity

Last session: 2026-02-18T08:00:00Z
Stopped at: Phase 7 execution complete
Resume file: .planning/phases/07-add-reviewer-lifecycle-management-to-broker/07-VERIFICATION.md
