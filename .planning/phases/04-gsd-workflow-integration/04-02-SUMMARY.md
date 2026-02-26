---
phase: 04-gsd-workflow-integration
plan: 02
subsystem: gsd-workflow
tags: [mcp-permissions, tandem-review, workflow-gates, review-broker]
requires:
  - 04-01 (category field and tandem config)
  - 01-01 (core broker server)
  - 02-01 (proposal protocol with create_review)
provides:
  - MCP broker tool permissions in all four GSD commands
  - Tandem plan review gate in gsd-planner (before PLAN.md write)
  - Tandem context review gate in discuss-phase workflow (before CONTEXT.md write)
  - Tandem UAT review gate in verify-work workflow (before commit)
affects:
  - 04-03 (reviewer-side workflow integration depends on these gates)
  - Phase 5 (production hardening may tune these gates)
tech-stack:
  added: []
  patterns:
    - "tandem_review_gate pattern: check config, submit for review, long-poll verdict, resubmit on changes_requested, fallback on error"
    - "Solo mode fallback: first broker connection failure disables tandem for remainder of execution"
key-files:
  created: []
  modified:
    - commands/gsd/plan-phase.md
    - commands/gsd/execute-phase.md
    - commands/gsd/discuss-phase.md
    - commands/gsd/verify-work.md
    - agents/gsd-planner.md
    - get-shit-done/workflows/discuss-phase.md
    - get-shit-done/workflows/verify-work.md
key-decisions:
  - "Gate placement: before disk write (planner, discuss) / before commit (verify) to allow reviewer to approve content before finalization"
  - "Error fallback: single connection failure disables tandem for rest of execution rather than retrying"
  - "Revision limit: 3 rounds in planner gate before warning, no hard cap to avoid blocking"
  - "Category mapping: plan_review for plans, handoff for context, verification for UAT"
duration: ~4 min
completed: 2026-02-17
---

# Phase 04 Plan 02: MCP Tool Permissions and Workflow Gates Summary

MCP broker tool permissions added to all four GSD commands; tandem review gates integrated into plan-phase (gsd-planner), discuss-phase, and verify-work workflows with solo-mode fallback on broker errors.

## Performance

- **Duration:** ~4 minutes
- **Tasks:** 3/3 completed
- **Deviations:** 0

## Accomplishments

1. **MCP tool permissions:** All four GSD command frontmatters (`plan-phase.md`, `execute-phase.md`, `discuss-phase.md`, `verify-work.md`) now include `mcp__gsdreview__*` in their `allowed-tools` list, granting broker access to any agent spawned by these commands.

2. **Plan review gate (gsd-planner):** Added `<tandem_plan_review>` section to `agents/gsd-planner.md` inside `<execution_flow>`, positioned before `<step name="write_phase_prompt">`. When tandem is enabled, the planner submits full PLAN.md content via `create_review` (category: `plan_review`) and long-polls for verdict before writing to disk. Supports resubmission on `changes_requested` with 3-round revision warning.

3. **Context review gate (discuss-phase):** Added `<tandem_review_gate>` section to `get-shit-done/workflows/discuss-phase.md` inside `<step name="write_context">`, positioned before the file write instruction. When tandem is enabled, submits full CONTEXT.md content via `create_review` (category: `handoff`) and waits for approval before writing.

4. **UAT review gate (verify-work):** Added `<tandem_review_gate>` section to `get-shit-done/workflows/verify-work.md` inside `<step name="complete_session">`, positioned before the commit command. When tandem is enabled, submits finalized UAT content via `create_review` (category: `verification`) and waits for approval before committing.

## Task Commits

| Task | Name | Commit | Key Files |
|------|------|--------|-----------|
| 1 | Add mcp__gsdreview__* to all four command frontmatters | 84fc29f | commands/gsd/*.md |
| 2 | Add tandem review gate to gsd-planner.md | 9506d29 | agents/gsd-planner.md |
| 3 | Add workflow-level tandem review gates to discuss-phase and verify-work | 93dbfab | get-shit-done/workflows/discuss-phase.md, verify-work.md |

## Files Modified

- `commands/gsd/plan-phase.md` -- added `mcp__gsdreview__*` to allowed-tools
- `commands/gsd/execute-phase.md` -- added `mcp__gsdreview__*` to allowed-tools
- `commands/gsd/discuss-phase.md` -- added `mcp__gsdreview__*` to allowed-tools
- `commands/gsd/verify-work.md` -- added `mcp__gsdreview__*` to allowed-tools
- `agents/gsd-planner.md` -- added `mcp__gsdreview__*` to tools frontmatter; added `<tandem_plan_review>` section
- `get-shit-done/workflows/discuss-phase.md` -- added `<tandem_review_gate>` section in write_context step
- `get-shit-done/workflows/verify-work.md` -- added `<tandem_review_gate>` section in complete_session step

## Decisions Made

| Decision | Rationale |
|----------|-----------|
| Gate before write/commit, not after | Reviewer must approve content before it hits disk/git; reverting is harder than gating |
| Solo mode on first connection failure | Avoids repeated retries that would stall the workflow; single failure is enough signal |
| 3-round revision warning (not hard cap) | Hard cap would block legitimate back-and-forth; warning surfaces concern without stopping |
| Category mapping: plan_review/handoff/verification | Aligns with 04-01 category field; each workflow stage has distinct review semantics |

## Deviations from Plan

None -- plan executed exactly as written.

## Issues Encountered

None.

## Next Phase Readiness

- **04-03 (Reviewer-side workflow integration):** Ready. All proposer-side gates are in place with correct categories. The reviewer agent can now be wired to consume these reviews.
- **No blockers** for downstream plans.

## Self-Check: PASSED
