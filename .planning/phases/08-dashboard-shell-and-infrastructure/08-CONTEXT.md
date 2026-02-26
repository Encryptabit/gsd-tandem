# Phase 8: Dashboard Shell and Infrastructure - Context

**Gathered:** 2026-02-25
**Updated:** 2026-02-26 (Astro migration pivot)
**Status:** Re-planning with Astro framework

<domain>
## Phase Boundary

Serve a web dashboard from the running broker process at `/dashboard`. The frontend is built with **Astro** (static site generator) producing pre-built HTML/CSS/JS files that the Python broker serves as static assets. Deliver the HTML scaffold, left sidebar navigation shell, visual design system, and SSE connection infrastructure. All later feature tabs (Overview, Logs, Reviews, Pool) are implemented as Astro components that plug into this shell. No external CDN or network dependencies in the built output.

**Architecture change:** The original implementation embedded all HTML/CSS/JS as Python string constants in dashboard.py. This proved immediately unmaintainable — escaping issues, no syntax highlighting, no component boundaries, mixing concerns. The pivot to Astro gives us:
- Proper `.astro` component files with scoped CSS
- Real dev tooling (hot reload, syntax highlighting, linting)
- Build-to-static output that requires no Node runtime at serve-time
- Clean separation between frontend (Astro) and backend (Python SSE/API)

</domain>

<decisions>
## Implementation Decisions

### Framework choice: Astro
- Astro static site generator builds to plain HTML/CSS/JS
- Project lives at `tools/gsd-review-broker/dashboard/` (sibling to `src/` and `tests/`)
- `npm run build` produces `dashboard/dist/` with static files
- Python broker serves `dist/` contents at `/dashboard` routes
- SSE endpoint (`/dashboard/events`) remains in Python (broker-side)
- No Node.js runtime needed when running the broker — only at build time
- The built `dist/` directory is committed to the repo so users don't need Node to run the broker

### Visual identity (LOCKED — unchanged from original discussion)
- Dark theme by default with light mode toggle
- Dark mode: very dark gray background (#1a1a1a to #1e1e1e range)
- Accent color: neon cyan/teal — cool-toned, high contrast against dark backgrounds
- Slightly brighter neon-ish accents for interactive elements and highlights
- Light mode available via visible toggle (sun/moon style)
- Full monospace typography throughout — consistent terminal/dev-tool aesthetic
- Dense data-table layout style rather than card-based sections

### Layout and navigation (LOCKED — unchanged)
- Left sidebar for main navigation (not top tabs)
- Sidebar sections: Overview, Logs, Reviews, Pool
- Sidebar shows live badge counts alongside nav items
- "GSD Tandem" text-only branding at top of sidebar
- Connection status indicator at bottom of sidebar (always visible)
- Dark/light mode toggle as visible button
- Basic responsive — works on tablet, primary target is desktop

### Log viewer structure (cross-phase decision for Phase 10)
- Two-tier tab system within Logs section: Active tab and Expired tab
- Active tab: live tail of currently writing logs from active reviewers
- Expired tab: complete lifecycle logs of past reviewer sessions
- Stable split-view layout: reviewer list panel on one side, log viewport on the other
- Expired logs: paginated list with "show 5|20|50" control, date filtering

### Data refresh strategy
- SSE (server-sent events) for real-time push of live data
- Auto-refresh on interval for Overview tab stats and reviewer list
- Active log tail: auto-scroll by default, pauses when user scrolls up
- Connection status indicator always visible in sidebar bottom

### Claude's Discretion
- Astro project structure and component breakdown
- Exact hex values for the neon cyan accent palette and dark/light theme color systems
- Auto-refresh interval timing
- SSE endpoint design and event naming
- Sidebar width and collapsibility behavior
- Specific monospace font choice (system mono stack)
- Badge count implementation details
- Build script and npm scripts configuration
- Whether to use Astro islands for interactive components or vanilla JS

</decisions>

<specifics>
## Specific Ideas

- Astro components: Sidebar.astro, ThemeToggle.astro, ConnectionStatus.astro, TabPanel.astro
- CSS lives in component files (scoped) + a global design-tokens.css for theme variables
- JavaScript for interactivity: tab switching, theme toggle, SSE connection — can be inline `<script>` in Astro components or separate .ts files
- The SSE EventSource connection code should be a shared utility since multiple tabs will use it
- Build output should be a flat structure that's easy to serve: index.html + assets/

</specifics>

<deferred>
## Deferred Ideas

None — discussion stayed within phase scope

</deferred>

---

*Phase: 08-dashboard-shell-and-infrastructure*
*Context gathered: 2026-02-25, updated 2026-02-26*
