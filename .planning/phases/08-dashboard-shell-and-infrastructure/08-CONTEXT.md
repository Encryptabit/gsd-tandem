# Phase 8: Dashboard Shell and Infrastructure - Context

**Gathered:** 2026-02-25
**Status:** Ready for planning

<domain>
## Phase Boundary

Serve a self-contained web dashboard from the running broker process at `/dashboard`. Deliver the HTML scaffold, tabbed navigation shell, static asset serving, and visual design system. All later feature tabs (Overview, Logs, Reviews, Pool) plug into this shell. No external CDN or network dependencies.

</domain>

<decisions>
## Implementation Decisions

### Visual identity
- Dark theme by default with light mode toggle
- Dark mode: very dark gray background (#1a1a1a to #1e1e1e range), not near-black
- Accent color: neon cyan/teal — cool-toned, high contrast against dark backgrounds
- Slightly brighter neon-ish accents for interactive elements and highlights
- Light mode available via visible toggle (sun/moon style)
- Full monospace typography throughout — consistent terminal/dev-tool aesthetic
- Dense data-table layout style rather than card-based sections — optimized for showing lots of review data

### Layout and navigation
- Left sidebar for main navigation (not top tabs like Serena)
- Sidebar sections: Overview, Logs, Reviews, Pool
- Sidebar shows live badge counts alongside nav items (e.g., "Reviews (3)", pending indicator dots)
- "GSD Tandem" text-only branding at top of sidebar (no logo/icon)
- Connection status indicator at bottom of sidebar (always visible)
- Dark/light mode toggle as visible button (not tucked in a menu)
- Basic responsive — works on tablet, primary target is desktop, no mobile optimization

### Log viewer structure (cross-phase decision for Phase 10)
- Two-tier tab system within Logs section: Active tab and Expired tab (sub-tabs)
- Active tab: live tail of currently writing logs from active reviewers
- Expired tab: complete lifecycle logs of past reviewer sessions
- Stable split-view layout: reviewer list panel on one side, log viewport on the other — no layout shift when selecting a reviewer
- Expired logs: paginated list with "show 5|20|50" control, date filtering (list grows quickly)
- Each reviewer selection shows their logs in the dedicated viewport, whether active tail or expired complete log

### Data refresh strategy
- SSE (server-sent events) for real-time push of live data (log tail, reviewer status changes)
- Auto-refresh on interval for Overview tab stats and reviewer list
- Active log tail: auto-scroll by default, pauses when user scrolls up, resume button to re-attach
- Connection status indicator always visible in sidebar bottom — shows broker connection health

### Claude's Discretion
- Exact hex values for the neon cyan accent palette and dark/light theme color systems
- Auto-refresh interval timing (5s, 10s, 30s, etc.)
- SSE endpoint design and event naming
- Sidebar width and collapsibility behavior
- Specific monospace font choice (system mono stack vs. specific font)
- Badge count implementation details (dots vs. numbers vs. both)

</decisions>

<specifics>
## Specific Ideas

- Reference: Serena dashboard (images provided) for overall structure inspiration, but GSD Tandem should have its own distinct identity
- Serena uses warm orange on dark charcoal with monospace — GSD Tandem uses neon cyan on darker gray with monospace (same family, different personality)
- Log viewer: think of it as a stable two-pane split — reviewer list is always there, viewport is always there, selecting a reviewer populates the viewport without shifting layout
- The "Active" vs "Expired" distinction maps to whether a reviewer subprocess is still running or has terminated

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 08-dashboard-shell-and-infrastructure*
*Context gathered: 2026-02-25*
