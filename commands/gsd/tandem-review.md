---
name: gsd:tandem-review
description: Submit current diff to gsdreview and drive the full proposer lifecycle
argument-hint: "[description]"
allowed-tools:
  - Read
  - Write
  - Edit
  - Glob
  - Grep
  - Bash
  - mcp__gsdreview__*
---
<objective>
Submit your current local diff to gsdreview with a description and handle the full review lifecycle with minimal manual intervention.

This command should:
- Identify as `proposer-${PROJECT_NAME}` for broker calls
- Submit review payload (description + diff)
- Long-poll reviewer status
- Use discussion messages for clarifications
- Revise on the same review when changes are requested
- Close the review after approval
</objective>

<execution_context>
@~/.claude/get-shit-done/workflows/tandem-review.md
</execution_context>

<context>
Description from user: `$ARGUMENTS`

If no description is supplied, auto-generate one from current branch, changed files, and intent summary.
</context>

<process>
Execute the tandem-review workflow from @~/.claude/get-shit-done/workflows/tandem-review.md end-to-end.
Preserve lifecycle invariants (single active review chain, discussion vs revision rules, close on approval).
</process>
