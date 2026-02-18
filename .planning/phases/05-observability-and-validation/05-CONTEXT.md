# Phase 5: Observability and Validation - Context

**Gathered:** 2026-02-17
**Status:** Ready for planning

<domain>
## Phase Boundary

Real-time visibility into broker activity, complete review history with audit log, and end-to-end workflow validation proving the full GSD pipeline works with broker mediation. This phase does NOT add new broker capabilities (state machine changes, new verdict types, etc.) — it surfaces what's already happening and validates the complete flow.

</domain>

<decisions>
## Implementation Decisions

### Status & Monitoring
- Full activity feed by default — show all reviews (active + recent completed), not just a summary dashboard
- Filterable by status (pending, claimed, approved, rejected, closed) AND category (plan_review, code_change, handoff, verification)
- Absolute ISO 8601 timestamps throughout (not relative "3 min ago")
- Each review in the feed includes a truncated preview of the most recent message, plus message count and last_message_at

### Audit Log & History
- Dedicated append-only `audit_events` table — not derived from querying existing tables
- Log state transitions AND message exchanges (propose, request_changes, counter-patch, verdict, etc.)
- Diff validation results and notification events are NOT logged (only lifecycle + messages)
- History returns everything — no time-range filtering needed (data volume is small per project)
- Output format: structured dict/JSON, consistent with existing broker tool response pattern

### Tool Interface Design
- Specialized tools (more tools, simpler each) rather than few tools with parameters
- Include a stats tool with counts + timing: total reviews, approval/rejection rates, reviews by category, average time-to-verdict, average review duration, time in each state
- Dedicated review timeline tool: Claude's Discretion (evaluate whether it adds enough value over composing get_proposal + get_messages)

### Claude's Discretion
- Exact tool naming and parameter signatures
- Whether a dedicated review_timeline tool is warranted vs composing existing tools
- Audit event schema details (columns, indexes)
- E2E validation test structure and assertion strategy

</decisions>

<specifics>
## Specific Ideas

- Activity feed should feel like watching a live log — everything flows through, you can filter to focus
- Stats tool is about workflow health assessment — "how is the tandem review process performing?"
- Append-only audit_events is the user's explicit preference over derived queries

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 05-observability-and-validation*
*Context gathered: 2026-02-17*
