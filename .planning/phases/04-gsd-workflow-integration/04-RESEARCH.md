# Phase 4: GSD Workflow Integration - Research

**Researched:** 2026-02-17
**Domain:** GSD command/agent markdown modification, MCP tool integration, config-driven workflow gating
**Confidence:** HIGH

## Summary

Phase 4 wires the four GSD commands (plan-phase, execute-phase, discuss-phase, verify-work) and two sub-agents (gsd-executor, gsd-planner) to the review broker built in Phases 1-3. This is a pure integration phase -- no new broker server code is needed. The work consists entirely of modifying markdown command definitions, markdown agent definitions, and adding config fields to `.planning/config.json`.

The core pattern is: at specific checkpoint moments in each command/agent's workflow, insert instructions to (1) check if tandem is enabled, (2) construct a proposal via `mcp__gsdreview__create_review`, (3) poll via `mcp__gsdreview__get_review_status` with `wait=true`, (4) handle the verdict (approved -> proceed, changes_requested -> incorporate feedback, rejected -> revert). This pattern repeats across all four commands with variation in what content is submitted and when.

The broker already exposes all needed MCP tools: `create_review`, `get_review_status`, `get_proposal`, `list_reviews`, `claim_review`, `submit_verdict`, `close_review`, `accept_counter_patch`, `reject_counter_patch`, `add_message`, `get_discussion`. The `category` field mentioned in CONTEXT.md does not yet exist in the schema -- it needs to be added as a new column via migration.

**Primary recommendation:** Modify 6 markdown files (4 commands + 2 agents), add 1 schema migration for category field, add tandem config fields to config.json, and add a small tandem helper module for shared polling/verdict-handling logic.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- plan-phase pauses after drafting a plan, submits the **full PLAN.md content** as the proposal body, waits for approval before writing to disk
- execute-phase pauses **before each task commit** (default granularity), submits a proposal with the task's diff, waits for approval before committing
- discuss-phase submits **full CONTEXT.md content** as a review gate before writing -- reviewer can approve, suggest changes, or add insights
- verify-work submits **full VERIFICATION.md content + pass/fail assessment** as a review gate -- reviewer confirms or disputes the verdict
- All four commands use review gates (not informational handoffs) -- reviewer controls the entire workflow pace
- On rejection: reviewer can propose a counter-patch or alternative fix via the existing Phase 3 counter-patch mechanism; executor incorporates the feedback
- Proposals include structured identity fields: agent_type, role, phase number, plan number, task number
- Sub-agents (gsd-executor, gsd-planner) submit proposals **directly to the broker** -- parent command does not mediate
- Each proposal includes a **category field** to distinguish type: plan_review, code_change, verification, handoff
- Execute-phase proposals include the **task description from PLAN.md** alongside the diff so the reviewer understands intent without switching files
- All tandem config lives in **.planning/config.json** alongside existing GSD settings
- Review granularity is a **global default only** (per-task or per-plan) -- no per-phase overrides
- **Optimistic mode**: executor applies changes and commits immediately, also submits for review; if reviewer rejects, changes are reverted via counter-patch
- **Solo mode toggle**: `tandem_enabled: false` in config.json skips all broker interactions -- commands run exactly as they do today
- New proposals arrive via **existing Phase 3 push notification mechanism** -- no new notification system needed
- Proposals are categorized (plan_review, code_change, verification, handoff) so reviewer can filter or prioritize by type
- **No bulk approve** -- every proposal gets individual review, maximum oversight
- Execute-phase proposals bundle task context (description from PLAN.md) so the reviewer has full intent alongside the diff
- Rejection flow leverages the counter-patch mechanism from Phase 3 -- reviewer supplies a fix, executor incorporates it
- Optimistic mode is "execute and queue for review" -- changes are committed immediately but submitted to broker; revert on rejection
- Solo mode is an explicit config toggle, not implicit broker availability detection

### Claude's Discretion
- Exact MCP tool call patterns for checkpoint logic in each command
- How to fork/modify GSD command workflows without breaking non-tandem usage
- Error handling when broker is unavailable mid-workflow
- How optimistic mode reverts work in practice (git revert vs counter-patch application)

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

## Standard Stack

This phase modifies existing markdown files and adds minor Python code. No new libraries needed.

### Core (Files to Modify)

| File | Type | What Changes |
|------|------|-------------|
| `commands/gsd/plan-phase.md` | Command | Add `mcp__gsdreview__*` to allowed-tools, add tandem review gate instructions |
| `commands/gsd/execute-phase.md` | Command | Add `mcp__gsdreview__*` to allowed-tools, add tandem review gate instructions |
| `commands/gsd/discuss-phase.md` | Command | Add `mcp__gsdreview__*` to allowed-tools, add tandem review gate instructions |
| `commands/gsd/verify-work.md` | Command | Add `mcp__gsdreview__*` to allowed-tools, add tandem review gate instructions |
| `agents/gsd-executor.md` | Agent | Add checkpoint-before-commit tandem logic, optimistic mode, counter-patch handling |
| `agents/gsd-planner.md` | Agent | Add submit-plan-for-review tandem logic before writing PLAN.md to disk |

### Supporting (New/Modified Code)

| File | Type | Purpose |
|------|------|---------|
| `tools/gsd-review-broker/src/gsd_review_broker/db.py` | Python | Add `category` column migration |
| `tools/gsd-review-broker/src/gsd_review_broker/tools.py` | Python | Accept `category` parameter in `create_review` |
| `tools/gsd-review-broker/src/gsd_review_broker/models.py` | Python | Add `Category` StrEnum |
| `.planning/config.json` | Config | Add tandem fields: `tandem_enabled`, `review_granularity`, `execution_mode` |

### No New Dependencies

No new pip packages, no new npm packages. All integration is through MCP tool calls (which are instructions in markdown) and minor schema evolution in existing Python code.

## Architecture Patterns

### Pattern 1: Tandem Guard — Config-Driven Conditional Checkpoint

**What:** Every tandem checkpoint follows the same pattern: check config, skip if disabled, construct proposal, submit, poll, handle verdict. This pattern is expressed as markdown instructions in command/agent files.

**When to use:** Every point where a GSD command or agent needs to pause for review.

**Pattern (in markdown instructions for agents):**

```markdown
<tandem_review_gate>
## Tandem Review Gate

**Before applying changes, check tandem config:**

```bash
TANDEM_ENABLED=$(cat .planning/config.json 2>/dev/null | grep -o '"tandem_enabled"[[:space:]]*:[[:space:]]*[^,}]*' | grep -o 'true\|false' || echo "false")
```

**If `TANDEM_ENABLED=false`:** Skip review gate entirely. Proceed as normal (solo mode).

**If `TANDEM_ENABLED=true`:**

1. **Submit proposal:**
   ```
   mcp__gsdreview__create_review(
     intent="[what this change does]",
     agent_type="[gsd-executor|gsd-planner]",
     agent_role="proposer",
     phase="[phase number]",
     plan="[plan number or null]",
     task="[task number or null]",
     category="[plan_review|code_change|verification|handoff]",
     description="[full content for review]",
     diff="[unified diff if applicable, else null]"
   )
   ```

2. **Poll for verdict (long-poll):**
   ```
   Loop:
     result = mcp__gsdreview__get_review_status(review_id=ID, wait=true)
     if result.status in ("approved", "changes_requested"):
       break
   ```

3. **Handle verdict:**
   - **approved** → Proceed with applying changes. Call `mcp__gsdreview__close_review(review_id)`.
   - **changes_requested** → Read `result.verdict_reason`. Check for counter-patch. Incorporate feedback. Revise and resubmit via `create_review(review_id=ID, ...)`. Return to step 2.

</tandem_review_gate>
```

**Confidence:** HIGH -- This directly maps to the existing `create_review` and `get_review_status(wait=true)` APIs.

### Pattern 2: Category-Based Proposal Routing

**What:** Each proposal type (plan_review, code_change, verification, handoff) gets a `category` field stored in the review. The reviewer can use `list_reviews(status="pending")` and filter by category in the response.

**Schema change required:**

```python
# In SCHEMA_MIGRATIONS list
"ALTER TABLE reviews ADD COLUMN category TEXT",
```

**Tool signature change:**

```python
@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    category: str | None = None,  # NEW: plan_review|code_change|verification|handoff
    description: str | None = None,
    diff: str | None = None,
    review_id: str | None = None,
    ctx: Context = None,
) -> dict:
```

**Category mapping:**

| Command/Agent | Category | What's Submitted |
|---------------|----------|------------------|
| gsd-planner (via plan-phase) | `plan_review` | Full PLAN.md content as description, no diff |
| gsd-executor (via execute-phase) | `code_change` | Task description + unified diff |
| discuss-phase | `handoff` | Full CONTEXT.md content as description, no diff |
| verify-work (via gsd-verifier) | `verification` | Full VERIFICATION.md content + pass/fail as description, no diff |

**Confidence:** HIGH -- Straightforward schema addition following established migration pattern.

### Pattern 3: Solo Mode Guard

**What:** A single boolean `tandem_enabled` in config.json controls whether any broker interaction happens. When false, all commands run exactly as they do today. No broker calls, no polling, no latency.

**Implementation:** Each command/agent markdown file starts with a config check. If tandem is disabled, the tandem-specific instruction blocks are skipped entirely.

**config.json additions:**

```json
{
  "tandem_enabled": false,
  "review_granularity": "per_task",
  "execution_mode": "blocking"
}
```

- `tandem_enabled`: boolean, default `false` (safe default -- must opt in)
- `review_granularity`: `"per_task"` (default) or `"per_plan"`
- `execution_mode`: `"blocking"` (default) or `"optimistic"`

**Confidence:** HIGH -- config.json is already read by all GSD commands via grep patterns.

### Pattern 4: Optimistic Mode with Git Revert

**What:** In optimistic mode, the executor commits changes immediately (normal flow), then submits the proposal to the broker. If the reviewer rejects, the executor reverts the commit.

**Revert mechanism:** `git revert --no-edit <commit_hash>` is the simplest and safest approach. It creates a new commit that undoes the previous one, preserving full history and avoiding destructive operations like `reset --hard`.

**Why git revert over counter-patch application:**
- Git revert is deterministic -- it always undoes exactly what was committed
- Counter-patches from the reviewer are for *improvements*, not for undoing -- they replace the reverted code with a better version
- The flow is: commit -> submit for review -> rejected -> revert commit -> apply counter-patch (if provided) -> resubmit

**Optimistic mode flow:**

```
1. Execute task normally
2. Commit (per standard task_commit_protocol)
3. Record commit hash
4. Submit proposal to broker (with diff and task description)
5. Continue to next task (don't wait)
6. At end of plan (or periodically), check pending reviews:
   - If approved -> close review, no action needed
   - If changes_requested -> revert commit (git revert), apply counter-patch if any, resubmit
```

**Important edge case:** In optimistic mode with per-task granularity, later tasks may build on earlier (now-reverted) changes. A rejection cascade can occur. The simplest mitigation: on rejection, revert ALL subsequent commits from the same plan and re-execute from the rejected task onward. This is complex. For v1, a pragmatic approach: optimistic mode should warn that rejection of early tasks may require re-executing later tasks.

**Confidence:** MEDIUM -- The basic git revert flow is straightforward, but cascade handling in optimistic mode adds complexity. The CONTEXT.md says optimistic mode should exist, but the implementation will need careful scoping to avoid over-engineering.

### Pattern 5: Granularity Configuration (Per-Task vs Per-Plan)

**What:** `review_granularity` in config.json controls when the executor pauses for review.

**Per-task (default):** Executor submits a proposal before each task commit. Reviewer sees each task's diff individually. Maximum oversight.

**Per-plan:** Executor runs all tasks in a plan, accumulates a combined diff (from first task's start to last task's end), and submits one proposal for the entire plan. Reviewer sees the aggregated change. Faster workflow but less granular control.

**Implementation in gsd-executor.md:**
- Read `review_granularity` from config at executor start
- Per-task: Insert tandem review gate in `task_commit_protocol` (before the commit)
- Per-plan: Skip per-task gates, add a single tandem review gate in `summary_creation` (after all tasks but before final commit)

**Per-plan diff collection:**

```bash
# Before first task: record starting point
PLAN_START_REF=$(git rev-parse HEAD)

# After all tasks complete: generate combined diff
COMBINED_DIFF=$(git diff ${PLAN_START_REF}..HEAD)
```

**Confidence:** HIGH -- Both modes use the same `create_review` tool, just at different points in the workflow.

### Recommended Modification Structure

```
commands/gsd/
├── plan-phase.md          # Add mcp__gsdreview__* to allowed-tools
├── execute-phase.md       # Add mcp__gsdreview__* to allowed-tools
├── discuss-phase.md       # Add mcp__gsdreview__* to allowed-tools
└── verify-work.md         # Add mcp__gsdreview__* to allowed-tools

agents/
├── gsd-executor.md        # Add tandem sections: guard, per-task gate, optimistic, counter-patch
└── gsd-planner.md         # Add tandem section: submit plan for review before write

tools/gsd-review-broker/src/gsd_review_broker/
├── db.py                  # Add category migration
├── tools.py               # Add category param to create_review, include in list_reviews/get_review_status
└── models.py              # Add Category StrEnum

.planning/
└── config.json            # Add tandem_enabled, review_granularity, execution_mode
```

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Polling for review status | Custom HTTP polling with sleep | `get_review_status(wait=true)` long-poll | Already built in Phase 3; handles 25s timeout, notification-driven wake |
| Counter-patch handling | Custom diff application code | Existing `accept_counter_patch` / `reject_counter_patch` tools | Already validated with re-validation on accept |
| Notification to reviewer | New push mechanism | Existing `NotificationBus.notify()` triggered by `create_review` | Phase 3 already wired this up |
| Review state machine | Custom approval tracking | Existing review lifecycle (pending -> claimed -> approved/changes_requested -> closed) | 6-state machine already tested |
| Diff validation | Custom git apply wrapper | Existing `validate_diff` in broker | Already handles cwd, async subprocess, error reporting |
| Commit reverting (optimistic) | Complex git branch management | Simple `git revert --no-edit <hash>` | Clean, non-destructive, preserves history |
| Category filtering in list_reviews | Complex SQL with joins | Add `category` to WHERE clause in existing `list_reviews` | One-line SQL change |

**Key insight:** The broker from Phases 1-3 already provides everything needed. Phase 4's job is purely to call existing tools at the right moments from the right agents.

## Common Pitfalls

### Pitfall 1: Breaking Solo Mode by Referencing Unavailable MCP Tools

**What goes wrong:** Adding `mcp__gsdreview__*` to allowed-tools means Claude sees these tools in its toolkit. If the broker server isn't running and tandem is enabled, every MCP call fails, blocking the entire workflow.

**Why it happens:** The `.mcp.json` file registers the broker as an MCP server. If the server process isn't running, tool calls fail.

**How to avoid:** Two layers of protection:
1. `tandem_enabled: false` (default) means no MCP tool calls are attempted
2. If tandem is enabled, wrap the first MCP call in error handling: if `create_review` fails with a connection error, warn the user ("Broker not running -- run `gsd-review-broker` or set tandem_enabled: false") and fall back to solo mode for this session

**Warning signs:** "Connection refused" errors on the first proposal submission.

### Pitfall 2: Context Explosion from Full-Content Proposals

**What goes wrong:** Submitting full PLAN.md or CONTEXT.md content as the `description` field means large text blobs in the database. The reviewer agent then needs to read these via `get_proposal`.

**Why it happens:** The CONTEXT.md decision specifies "full content" submissions.

**How to avoid:** This is by design (per CONTEXT.md decisions), so don't fight it. But ensure:
- `get_proposal` returns the full description (it already does)
- The reviewer sees the description directly in the claim response or via get_proposal
- Don't duplicate the content across multiple fields

### Pitfall 3: Optimistic Mode Revert Cascades

**What goes wrong:** In optimistic mode, task 3 builds on task 2. If task 2 is rejected and reverted, task 3's commit becomes invalid (references code that was reverted).

**Why it happens:** Optimistic mode allows forward progress before reviews complete.

**How to avoid:** For v1, implement optimistic mode with a practical limitation:
- Optimistic mode operates at the per-task level, not truly concurrent
- When a rejection arrives for task N, warn the user and stop optimistic execution
- The executor must re-execute from task N onward after incorporating feedback
- Document this as a known limitation

**Warning signs:** `git revert` failing because the working tree has been modified by subsequent tasks.

### Pitfall 4: Infinite Revision Loops

**What goes wrong:** Reviewer requests changes, proposer revises, reviewer requests more changes, cycle repeats indefinitely.

**Why it happens:** No maximum revision count enforced.

**How to avoid:** The broker already tracks `current_round` (incremented on revision). Add a soft limit in the agent instructions: after 3 revision rounds, escalate to the user with a message like "Review has gone through 3 rounds. Consider discussing directly." This is a behavioral instruction, not a hard enforcement.

### Pitfall 5: Conflating Allowed-Tools with Required Behavior

**What goes wrong:** Adding `mcp__gsdreview__*` to a command's allowed-tools does NOT teach Claude how to use them. The agent/command must have explicit instructions for when and how to call broker tools.

**Why it happens:** Allowed-tools only grants permission. Behavior comes from instructions.

**How to avoid:** Adding tools to the frontmatter is necessary but not sufficient. The actual checkpoint logic must be written as detailed step-by-step instructions within the agent/command markdown, including:
- When to check tandem config
- What to submit as the proposal
- How to poll for results
- What to do on each verdict type

### Pitfall 6: Per-Plan Granularity Breaking Atomic Task Commits

**What goes wrong:** In per-plan mode, the executor runs all tasks and commits each one individually (standard behavior). Then it submits a single proposal for the whole plan. If rejected, individual task commits have already been made.

**Why it happens:** The existing task_commit_protocol commits after each task, but per-plan review happens after all tasks.

**How to avoid:** In per-plan mode, the executor should still commit each task individually (for traceability), but the review gate moves to after all tasks complete. On rejection, the executor reverts all task commits for the plan. The combined diff for the proposal should be generated from the plan's starting point.

## Code Examples

### Example 1: Config Check in Agent (Markdown Instructions)

```markdown
<tandem_config>
## Load Tandem Configuration

At executor start, read tandem settings:

```bash
TANDEM_ENABLED=$(cat .planning/config.json 2>/dev/null | grep -o '"tandem_enabled"[[:space:]]*:[[:space:]]*[^,}]*' | grep -o 'true\|false' || echo "false")
REVIEW_GRANULARITY=$(cat .planning/config.json 2>/dev/null | grep -o '"review_granularity"[[:space:]]*:[[:space:]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"' || echo "per_task")
EXECUTION_MODE=$(cat .planning/config.json 2>/dev/null | grep -o '"execution_mode"[[:space:]]*:[[:space:]]*"[^"]*"' | grep -o '"[^"]*"$' | tr -d '"' || echo "blocking")
```

Store these for use throughout execution. If `TANDEM_ENABLED=false`, skip all `<tandem_*>` sections.
</tandem_config>
```

### Example 2: Per-Task Review Gate in gsd-executor (Markdown Instructions)

```markdown
<tandem_task_review>
## Tandem Task Review Gate (Per-Task Mode)

**When:** After task verification passes but BEFORE git commit.
**Skip if:** `TANDEM_ENABLED=false` or `REVIEW_GRANULARITY=per_plan`

1. Generate the task's diff:
   ```bash
   TASK_DIFF=$(git diff HEAD)
   ```

2. Submit proposal:
   ```
   result = mcp__gsdreview__create_review(
     intent="Task {N}: {task_name} in plan {phase}-{plan}",
     agent_type="gsd-executor",
     agent_role="proposer",
     phase="{phase_number}",
     plan="{plan_number}",
     task="{task_number}",
     category="code_change",
     description="## Task Description\n{task description from PLAN.md}\n\n## Verification\n{verification result}",
     diff=TASK_DIFF
   )
   review_id = result["review_id"]
   ```

3. Wait for verdict (blocking mode):
   ```
   Loop:
     status = mcp__gsdreview__get_review_status(review_id=review_id, wait=true)
     if status["status"] == "approved":
       mcp__gsdreview__close_review(review_id=review_id)
       # Proceed to commit
       break
     if status["status"] == "changes_requested":
       # Read feedback
       reason = status["verdict_reason"]
       # Check for counter-patch
       proposal = mcp__gsdreview__get_proposal(review_id=review_id)
       # Incorporate feedback, modify files, regenerate diff
       # Resubmit: mcp__gsdreview__create_review(review_id=review_id, ...)
   ```

4. After approval: proceed to standard `task_commit_protocol`.
</tandem_task_review>
```

### Example 3: Plan Review Gate in gsd-planner (Markdown Instructions)

```markdown
<tandem_plan_review>
## Tandem Plan Review Gate

**When:** After PLAN.md content is drafted but BEFORE writing to disk via Write tool.
**Skip if:** `TANDEM_ENABLED=false`

1. Submit the plan for review:
   ```
   result = mcp__gsdreview__create_review(
     intent="Plan {phase}-{plan}: {plan objective}",
     agent_type="gsd-planner",
     agent_role="proposer",
     phase="{phase_number}",
     plan="{plan_number}",
     category="plan_review",
     description=PLAN_CONTENT  # Full PLAN.md content
   )
   review_id = result["review_id"]
   ```

2. Wait for verdict:
   ```
   Loop:
     status = mcp__gsdreview__get_review_status(review_id=review_id, wait=true)
     if status["status"] == "approved":
       mcp__gsdreview__close_review(review_id=review_id)
       # Now write PLAN.md to disk
       break
     if status["status"] == "changes_requested":
       # Read feedback, revise plan, resubmit
   ```

3. After approval: write PLAN.md to disk using Write tool.
</tandem_plan_review>
```

### Example 4: Schema Migration for Category (Python)

```python
# In db.py SCHEMA_MIGRATIONS list
SCHEMA_MIGRATIONS: list[str] = [
    # Phase 2 migrations
    "ALTER TABLE reviews ADD COLUMN description TEXT",
    "ALTER TABLE reviews ADD COLUMN diff TEXT",
    "ALTER TABLE reviews ADD COLUMN affected_files TEXT",
    # Phase 3 migrations
    "ALTER TABLE reviews ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'",
    "ALTER TABLE reviews ADD COLUMN current_round INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE reviews ADD COLUMN counter_patch TEXT",
    "ALTER TABLE reviews ADD COLUMN counter_patch_affected_files TEXT",
    "ALTER TABLE reviews ADD COLUMN counter_patch_status TEXT",
    # Phase 4 migrations
    "ALTER TABLE reviews ADD COLUMN category TEXT",
]
```

### Example 5: Updated create_review Tool (Python)

```python
@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    category: str | None = None,
    description: str | None = None,
    diff: str | None = None,
    review_id: str | None = None,
    ctx: Context = None,
) -> dict:
    # ... existing validation logic ...

    # New review flow: include category in INSERT
    await app.db.execute(
        """INSERT INTO reviews (id, status, intent, description, diff,
                                affected_files, agent_type, agent_role,
                                phase, plan, task, priority, category,
                                created_at, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
        (
            new_review_id,
            ReviewStatus.PENDING,
            intent,
            description,
            diff,
            affected_files,
            agent_type,
            agent_role,
            phase,
            plan,
            task,
            str(priority),
            category,
        ),
    )
```

### Example 6: Broker Error Handling (Markdown Instructions)

```markdown
<tandem_error_handling>
## Broker Connection Error Handling

If any `mcp__gsdreview__*` call fails with a connection error:

1. Log warning: "Review broker unreachable. Falling back to solo mode for this session."
2. Set `TANDEM_ENABLED=false` for the remainder of this agent's execution
3. Proceed with normal (non-tandem) workflow
4. Do NOT retry broker calls -- the broker is likely not running

This ensures workflow never blocks on a missing broker.
</tandem_error_handling>
```

### Example 7: Optimistic Mode (Markdown Instructions)

```markdown
<tandem_optimistic_mode>
## Optimistic Execution Mode

**When:** `EXECUTION_MODE=optimistic` and `TANDEM_ENABLED=true`
**Behavior:** Execute and commit immediately, submit for review asynchronously.

1. Execute task normally (standard task_commit_protocol)
2. Record commit hash: `TASK_COMMIT=$(git rev-parse --short HEAD)`
3. Submit proposal (same as blocking mode but don't wait):
   ```
   result = mcp__gsdreview__create_review(...)
   PENDING_REVIEWS[$TASK_NUMBER] = {"review_id": result["review_id"], "commit": TASK_COMMIT}
   ```
4. Continue to next task immediately

**At plan completion (or periodically):** Check all pending reviews:
   ```
   for each pending_review in PENDING_REVIEWS:
     status = mcp__gsdreview__get_review_status(review_id=pending_review.review_id)
     if status["status"] == "approved":
       mcp__gsdreview__close_review(review_id=pending_review.review_id)
     if status["status"] == "changes_requested":
       # STOP optimistic execution
       # Revert this and all subsequent commits
       git revert --no-edit ${pending_review.commit}..HEAD
       # Incorporate feedback and re-execute from this task
   ```

**Limitation:** If a reviewer rejects an early task, all subsequent tasks must be re-executed.
Document this in SUMMARY.md under "Optimistic Mode Reverts".
</tandem_optimistic_mode>
```

### Example 8: Updated list_reviews with Category Filter (Python)

```python
@mcp.tool
async def list_reviews(
    status: str | None = None,
    category: str | None = None,  # NEW
    ctx: Context = None,
) -> dict:
    """List reviews, optionally filtered by status and/or category."""
    app: AppContext = ctx.lifespan_context
    conditions = []
    params = []
    if status is not None:
        conditions.append("status = ?")
        params.append(status)
    if category is not None:
        conditions.append("category = ?")
        params.append(category)

    where_clause = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    # ... rest of query with order_clause ...
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Informational handoffs (fire-and-forget) | Full review gates (blocking) | CONTEXT.md decision | All 4 commands are review-gated, not just informational |
| Parent mediates sub-agent proposals | Sub-agents submit directly | CONTEXT.md decision | gsd-executor and gsd-planner call broker tools themselves |
| Implicit broker detection | Explicit config toggle | CONTEXT.md decision | `tandem_enabled` flag, not try-catch on tool calls |

## Open Questions

1. **Counter-patch application in optimistic mode**
   - What we know: On rejection, `git revert --no-edit` undoes the commit. Counter-patch is available via `get_proposal` after `accept_counter_patch`.
   - What's unclear: If the reviewer's counter-patch assumes the pre-commit state (which git revert restores), does `git apply` of the counter-patch work cleanly? It should, since `git revert` restores the exact prior state.
   - Recommendation: Test this flow in Phase 4's tests. If edge cases arise, document them.

2. **Per-plan diff aggregation with staged/unstaged changes**
   - What we know: `git diff PLAN_START_REF..HEAD` gives the combined diff.
   - What's unclear: If tasks modify the same file in different hunks, the combined diff may be harder for the reviewer to understand than individual task diffs.
   - Recommendation: Include per-task commit messages in the description alongside the combined diff so the reviewer can understand the logical breakdown.

3. **Maximum content size for description field**
   - What we know: SQLite TEXT columns can hold up to 1GB. Full PLAN.md files are typically 5-20KB.
   - What's unclear: Whether very large descriptions cause issues for the reviewer's context window when reading proposals.
   - Recommendation: Not a concern for v1. Plans and verification reports are well within reasonable bounds.

4. **Allowed-tools propagation to sub-agents**
   - What we know: Command files list allowed-tools in frontmatter. Agents are spawned via `Task()`.
   - What's unclear: Whether MCP tools listed in the parent command's frontmatter are automatically available to Task()-spawned sub-agents, or if they need to be in the agent's frontmatter too.
   - Recommendation: Test empirically. If sub-agents don't inherit MCP tools, add `mcp__gsdreview__*` to both the command frontmatter AND the agent's tools list. The agent markdown `tools:` line would need updating.

## Sources

### Primary (HIGH confidence)
- **Existing codebase analysis** -- All broker tools, models, state machine, schema, and tests read directly
- **GSD command files** -- `commands/gsd/plan-phase.md`, `execute-phase.md`, `discuss-phase.md`, `verify-work.md` read in full
- **GSD agent files** -- `agents/gsd-executor.md`, `gsd-planner.md`, `gsd-verifier.md` read in full
- **Workflow files** -- `~/.claude/get-shit-done/workflows/execute-phase.md`, `discuss-phase.md`, `verify-work.md` read in full
- **Config and state** -- `.planning/config.json`, `STATE.md`, `ROADMAP.md`, `REQUIREMENTS.md` read in full
- **Existing test patterns** -- `tests/conftest.py`, `tests/test_proposals.py` analyzed for MockContext pattern

### Secondary (MEDIUM confidence)
- **Optimistic mode git revert behavior** -- Based on standard git revert semantics; no project-specific testing yet
- **MCP tool inheritance in sub-agents** -- Based on observed patterns in command/agent frontmatter; needs empirical verification

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- All files identified, all modifications are incremental additions to existing files
- Architecture patterns: HIGH -- All patterns derive from existing codebase structures and established config/tool conventions
- Pitfalls: HIGH -- Based on direct analysis of actual code flow in commands, agents, and broker tools
- Optimistic mode: MEDIUM -- Git revert is standard, but cascade behavior needs testing
- Sub-agent MCP tool access: MEDIUM -- Needs empirical verification

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (stable -- no external library changes involved)
