# Phase 3: Discussion and Patches - Context

**Gathered:** 2026-02-16
**Status:** Ready for planning

<domain>
## Phase Boundary

Multi-round threaded conversation within reviews, counter-patches from the reviewer, priority levels for review ordering, and push notifications to connected clients. This extends the single-round propose/verdict flow into a rich back-and-forth protocol.

</domain>

<decisions>
## Implementation Decisions

### Message threading
- Claude's discretion on structure (flat list vs threaded tree) — pick what fits the existing broker design
- Turn-based messaging — strict alternation between proposer and reviewer (proposer submits, reviewer responds, proposer revises, etc.)
- Messages support text body + optional structured metadata (e.g., file paths, line references)
- Claude's discretion on whether status queries return full history or latest round

### Counter-patches
- Counter-patch replaces the original proposal's diff (not coexisting as an option)
- Counter-patches go through the same git apply --check validation as original diffs
- Claude's discretion on which verdicts can carry counter-patches (request_changes, comment, approve)
- Proposer must explicitly accept a counter-patch before it becomes the active diff — it's pending until accepted

### Priority levels
- Auto-inferred from agent identity context (agent role + phase), not manually set
- Priority is fixed at submission time — reviewer cannot change it after the fact
- Priority affects sort order in list_reviews only (critical first, then normal, then low) — no visual flags or other behavioral differences
- Inference rules: planner proposals = critical, executor tasks = normal, verification = low

### Push notifications
- Claude's discretion on mechanism (MCP notifications vs SSE vs other) — pick what works best with FastMCP
- Trigger events: new proposal creation and proposer revision submissions (not all state changes)
- Both proposer and reviewer receive push notifications for events relevant to them
- Claude's discretion on reconnect behavior (catch-up backlog vs poll-only fallback)

### Claude's Discretion
- Message structure (flat vs threaded)
- History retrieval strategy (full vs latest round)
- Which verdicts can carry counter-patches
- Push notification transport mechanism
- Reconnect/catch-up behavior

</decisions>

<specifics>
## Specific Ideas

No specific requirements — open to standard approaches.

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope.

</deferred>

---

*Phase: 03-discussion-and-patches*
*Context gathered: 2026-02-16*
