# Phase 4: GSD Workflow Integration - Context

**Gathered:** 2026-02-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Wire existing GSD commands (plan-phase, execute-phase, discuss-phase, verify-work) to pause at checkpoints and submit proposals through the review broker before applying changes. Sub-agents submit directly. Granularity and execution mode are configurable. The broker and its MCP tools already exist from Phases 1-3 -- this phase is purely about integrating GSD commands as broker clients.

</domain>

<decisions>
## Implementation Decisions

### Checkpoint placement
- plan-phase pauses after drafting a plan, submits the **full PLAN.md content** as the proposal body, waits for approval before writing to disk
- execute-phase pauses **before each task commit** (default granularity), submits a proposal with the task's diff, waits for approval before committing
- discuss-phase submits **full CONTEXT.md content** as a review gate before writing -- reviewer can approve, suggest changes, or add insights
- verify-work submits **full VERIFICATION.md content + pass/fail assessment** as a review gate -- reviewer confirms or disputes the verdict
- All four commands use review gates (not informational handoffs) -- reviewer controls the entire workflow pace
- On rejection: reviewer can propose a counter-patch or alternative fix via the existing Phase 3 counter-patch mechanism; executor incorporates the feedback

### Agent identity & handoffs
- Proposals include structured identity fields: agent_type, role, phase number, plan number, task number
- Sub-agents (gsd-executor, gsd-planner) submit proposals **directly to the broker** -- parent command does not mediate
- Each proposal includes a **category field** to distinguish type: plan_review, code_change, verification, handoff
- Execute-phase proposals include the **task description from PLAN.md** alongside the diff so the reviewer understands intent without switching files

### Config & granularity
- All tandem config lives in **.planning/config.json** alongside existing GSD settings
- Review granularity is a **global default only** (per-task or per-plan) -- no per-phase overrides
- **Optimistic mode**: executor applies changes and commits immediately, also submits for review; if reviewer rejects, changes are reverted via counter-patch
- **Solo mode toggle**: `tandem_enabled: false` in config.json skips all broker interactions -- commands run exactly as they do today

### Reviewer experience
- New proposals arrive via **existing Phase 3 push notification mechanism** -- no new notification system needed
- Proposals are categorized (plan_review, code_change, verification, handoff) so reviewer can filter or prioritize by type
- **No bulk approve** -- every proposal gets individual review, maximum oversight
- Execute-phase proposals bundle task context (description from PLAN.md) so the reviewer has full intent alongside the diff

### Claude's Discretion
- Exact MCP tool call patterns for checkpoint logic in each command
- How to fork/modify GSD command workflows without breaking non-tandem usage
- Error handling when broker is unavailable mid-workflow
- How optimistic mode reverts work in practice (git revert vs counter-patch application)

</decisions>

<specifics>
## Specific Ideas

- Rejection flow leverages the counter-patch mechanism from Phase 3 -- reviewer supplies a fix, executor incorporates it
- Optimistic mode is "execute and queue for review" -- changes are committed immediately but submitted to broker; revert on rejection
- Solo mode is an explicit config toggle, not implicit broker availability detection

</specifics>

<deferred>
## Deferred Ideas

None -- discussion stayed within phase scope

</deferred>

---

*Phase: 04-gsd-workflow-integration*
*Context gathered: 2026-02-17*
