# Phase 11: Review Browser Tab - Context

**Gathered:** 2026-02-26
**Status:** Ready for planning

<domain>
## Phase Boundary

Browse the full history of reviews with filtering, inspect any review's diff and metadata in a detail view, and read complete discussion threads with verdict events. This is a read-only browser — no actions (approve, reject, comment) are taken from the dashboard.

</domain>

<decisions>
## Implementation Decisions

### List view layout
- Card rows (not compact table) — each review gets a card-style row with vertical space
- Full summary on each card: status badge, intent text, category, priority, agent type, timestamps, verdict
- Live updates via SSE — new reviews appear and statuses change in real-time

### SSE architecture (cross-cutting)
- Refactor to singleton SSE connection alive for the lifetime of the page
- Different features subscribe to an observable/store pattern for their updates
- No more per-tab teardown/reinit of EventSource connections on view switches
- This applies to all existing tabs (overview, logs) and new ones (reviews, pool)

### Diff rendering
- Unified view (single column, +/- lines with green/red coloring)
- Full syntax highlighting (language-aware coloring within diff lines, like GitHub PR diffs)
- Must remain self-contained (no CDN dependencies for highlighting library)

### Claude's Discretion (Diff)
- Multi-file organization (collapsible sections vs flat scroll)
- Line number display format

### Discussion thread
- Flat timeline layout (single column with role labels and timestamps, like GitHub PR comments)
- Verdict events (approved, changes_requested) appear inline in the timeline between messages — full chronological story
- Live updates via the shared SSE singleton — new messages and verdicts stream in while viewing a review

### Claude's Discretion (Discussion)
- Counter-patch display within messages (inline diff block vs separate link)

### Navigation flow
- Clicking a review replaces the list with a full-width detail view (with back button)
- Detail view uses tabbed sections: Overview | Diff | Discussion
- JS state only — no URL routing, detail views are not deep-linkable
- Returning to list preserves filters and scroll position

### Claude's Discretion (General)
- Filter bar implementation style (dropdowns, chips, sidebar)
- Sorting controls placement and options
- Empty state design for no reviews / no matching filters

</decisions>

<specifics>
## Specific Ideas

- SSE singleton pattern: "anything SSE related should have a singleton alive for the lifetime of the broker (or page) and features subscribe to an observable or store to get updates instead of teardown and reinit"
- Card rows should show enough info that you rarely need to click into detail for quick status checks
- Discussion timeline should tell the full chronological story including verdict events interleaved with messages

</specifics>

<deferred>
## Deferred Ideas

- Dashboard actions (approve/reject/comment from the browser) — potential future phase
- SSE refactor scope: the singleton pattern was decided here but implementation touches all existing tabs (phases 8-10 code) — researcher/planner should determine if this is done as part of phase 11 or factored separately

</deferred>

---

*Phase: 11-review-browser-tab*
*Context gathered: 2026-02-26*
