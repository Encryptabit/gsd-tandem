# Phase 2: Proposal and Diff Protocol - Context

**Gathered:** 2026-02-16
**Status:** Ready for planning

<domain>
## Phase Boundary

Enrich the review lifecycle with structured proposal content (intent descriptions + unified diffs), reviewer verdicts with reasoning, and pre-application diff validation. Proposer submits proposals containing what changed and why; reviewer evaluates and issues typed verdicts; broker validates diffs apply cleanly before reviewer acts. Multi-round discussion, counter-patches, and priority levels belong in Phase 3.

</domain>

<decisions>
## Implementation Decisions

### Proposal structure
- Structured breakdown for intent: natural language description + list of what changed and why (PR-description style, not one-liner)
- Full agent identity context included: agent_type, role, phase, plan, task — reviewer sees exactly where the change fits in the workflow
- Multi-file diffs stored as a single unified diff blob, but proposal metadata lists affected files separately for easier navigation
- All file operations supported: create, modify, delete — full unified diff semantics including /dev/null for new/deleted files

### Verdict model
- Three verdict types: approve, request_changes, comment — matches GitHub PR review model
- Notes required for request_changes and comment; only approve can be bare
- request_changes transitions to a new 'changes_requested' state (distinct from pending) — stays claimed by the same reviewer, signals proposer needs to act
- Revisions replace in place — latest proposal overwrites previous content, no version history

### Diff validation
- git apply --check runs on claim, not on submission — diffs can enter the queue before validation
- No format validation on submission — trust the proposer (always an agent generating valid diffs)
- If git apply --check fails on claim: auto-reject with detailed errors (capture git apply --check stderr so proposer knows exactly which files/hunks conflicted)

### Claude's Discretion
- Tool surface design: whether to extend existing tools or create new ones for proposal submission, verdict, and diff retrieval
- How the reviewer accesses proposal content (inline in claim response vs separate tool)
- Revision mechanism details (how resubmission works on same review_id)
- Verdict tool design (standalone vs extending Phase 1 approve/reject tools)

</decisions>

<specifics>
## Specific Ideas

- Revision flow: proposer calls submit again with the same review_id to replace content (not a separate update tool)
- Validation timing chosen to catch drift between submission and review — on claim catches the actual state the reviewer will work with
- Auto-reject on conflict is hard — reviewer doesn't get stuck evaluating stale diffs

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 02-proposal-and-diff-protocol*
*Context gathered: 2026-02-16*
