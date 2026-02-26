<purpose>
Submit the current project diff to gsdreview and run the proposer lifecycle end-to-end: submit, long-poll, discuss/resubmit as needed, and close on approval.
</purpose>

<required_reading>
Read all files referenced by the invoking prompt's execution_context before starting.
</required_reading>

<process>

<step name="resolve_identity">
Resolve deterministic project identity for this review chain:

```bash
PROJECT_ROOT=$(git rev-parse --show-toplevel 2>/dev/null || pwd)
PROJECT_NAME=${GSD_REVIEW_PROJECT:-$(basename "$PROJECT_ROOT" | tr '[:upper:]' '[:lower:]' | sed 's/[^a-z0-9._-]/-/g')}
CALLER_ID="proposer-${PROJECT_NAME}"
BRANCH_NAME=$(git rev-parse --abbrev-ref HEAD 2>/dev/null || echo "detached")
```

Use `CALLER_ID` for lifecycle reads/polls:
- `mcp__gsdreview__get_review_status(..., caller_id=CALLER_ID)`
- `mcp__gsdreview__get_proposal(..., caller_id=CALLER_ID)`
- `mcp__gsdreview__get_discussion(..., caller_id=CALLER_ID)`
</step>

<step name="capture_diff_payload">
Collect reviewable changes from current workspace state.

```bash
STATUS=$(git status --porcelain)
TRACKED_DIFF=$(git diff HEAD)
UNTRACKED=$(git ls-files --others --exclude-standard)
```

If `STATUS` is empty:
- Stop with: "No local changes detected. Nothing to submit for tandem review."

Build `REVIEW_DIFF`:
1. Start with `TRACKED_DIFF`
2. Append add-file patches for untracked files:
```bash
REVIEW_DIFF="$TRACKED_DIFF"
while IFS= read -r f; do
  [ -n "$f" ] || continue
  REVIEW_DIFF="${REVIEW_DIFF}"$'\n'"$(git diff --no-index -- /dev/null "$f" || true)"
done <<< "$UNTRACKED"
```

Build changed-files summary for description payload:
```bash
TRACKED_FILES=$(git diff --name-only HEAD)
UNTRACKED_FILES=$(git ls-files --others --exclude-standard | sed 's/^/[new] /')
```

If `REVIEW_DIFF` is still empty after assembly:
- Stop with: "Detected status changes but no serializable diff payload. Please stage/modify files and retry."
</step>

<step name="build_submission_metadata">
Use user-provided description when present (`$ARGUMENTS`). Otherwise auto-generate a concise summary from branch + changed files.

Set:
- `INTENT`: Prefix with `Tandem review command:` for traceability.
- `DESCRIPTION_PAYLOAD`: Include:
  - user description (or generated summary)
  - project name + branch
  - explicit changed-files list
  - reviewer focus request (correctness, regressions, test gaps)

Example payload shape:

```markdown
## Submission
{description}

## Context
- Project: {PROJECT_NAME}
- Branch: {BRANCH_NAME}

## Changed Files
- {file1}
- {file2}

## Review Focus
Prioritize correctness, behavioral regressions, integration risk, and missing tests.
```
</step>

<step name="select_chain_or_create_review">
Before creating a new review, check for an open review chain from this command in the same project.

Call:
- `mcp__gsdreview__get_activity_feed(project=PROJECT_NAME)`

If the latest open review (`pending`, `claimed`, `in_review`, `changes_requested`, or `approved`) has intent starting with `Tandem review command:`:
- Reuse that `review_id` and continue lifecycle handling.

Otherwise, create a new review:
- `mcp__gsdreview__create_review` with:
  - `intent`: `INTENT`
  - `agent_type`: `"claude-code"`
  - `agent_role`: `"proposer"`
  - `project`: `PROJECT_NAME`
  - `category`: `"code_change"`
  - `description`: `DESCRIPTION_PAYLOAD`
  - `diff`: `REVIEW_DIFF`

If create fails with diff-validation error, retry once with:
- `skip_diff_validation=true`

Store resulting `ID`.
</step>

<step name="lifecycle_loop">
Run a long-poll lifecycle loop until resolved:

```text
status = mcp__gsdreview__get_review_status(review_id=ID, wait=true, caller_id=CALLER_ID)
```

Handle by status:

1. `approved`
- Call `mcp__gsdreview__close_review(review_id=ID, closer_role="proposer")`
- Stop successfully.

2. `changes_requested`
- Read `verdict_reason`.
- Fetch proposal context:
  - `PROPOSAL = mcp__gsdreview__get_proposal(review_id=ID, caller_id=CALLER_ID)`

- Decide response path:
  - **Discussion path (no immediate code change required)**:
    - Use `mcp__gsdreview__add_message(review_id=ID, sender_role="proposer", body=...)`
    - Continue polling. Do **not** create a new review.
  - **Revision path (code/content changes needed)**:
    - Apply requested changes locally.
    - If `PROPOSAL.counter_patch_status == "pending"`:
      - Accept when appropriate via `mcp__gsdreview__accept_counter_patch(review_id=ID)`, then incorporate resulting active diff/content.
      - Otherwise reject via `mcp__gsdreview__reject_counter_patch(review_id=ID)` and implement manually.
    - Rebuild `REVIEW_DIFF`, `DESCRIPTION_PAYLOAD`, and `INTENT`.
    - Resubmit the **same** review chain:
      - `mcp__gsdreview__create_review(review_id=ID, intent=..., description=..., diff=...)`
      - Retry with `skip_diff_validation=true` only if validation fails.
    - Continue polling.

3. `pending`, `claimed`, `in_review`
- Continue polling with `wait=true`.

4. `comment`
- Treat as reviewer feedback without state transition.
- Use discussion message for clarifications, or revise/resubmit same `review_id` if actionable changes are needed.

5. `closed`
- Stop. If closure happened before approval, report final state and reason.
</step>

<step name="lifecycle_invariants">
Always enforce these invariants:

- Use `caller_id=CALLER_ID` (`proposer-${PROJECT_NAME}`) on status/proposal/discussion reads.
- While a review is open, continue the same chain (`review_id=ID`) for revisions.
- Use `add_message` for clarifications/progress notes.
- Do **not** create a new review for the same unresolved thread.
- Only create a new review after the previous one is closed and new work needs review.
- On approval, always close with `closer_role="proposer"`.
</step>

<step name="result_format">
Return concise completion output:

```markdown
## Tandem Review Complete

- Review ID: {ID}
- Project: {PROJECT_NAME}
- Caller ID: {CALLER_ID}
- Final Status: {closed}
- Branch: {BRANCH_NAME}
- Revision Rounds: {N}
```
</step>

</process>
