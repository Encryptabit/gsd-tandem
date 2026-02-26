---
phase: 08-dashboard-shell-and-infrastructure
plan: 01
subsystem: ui
tags: [astro, css-custom-properties, sse, dark-theme, static-site, dashboard]

# Dependency graph
requires: []
provides:
  - "Astro static site project at tools/gsd-review-broker/dashboard/"
  - "Built dist/ with index.html ready for Python broker to serve"
  - "Design token system with dark/light themes and neon cyan accent"
  - "Sidebar navigation shell with Overview/Logs/Reviews/Pool tabs"
  - "SSE EventSource utility with auto-reconnect for live data"
  - "Theme toggle with localStorage persistence"
  - "Tab switching with localStorage persistence"
  - "Connection status indicator (connected/disconnected/reconnecting)"
affects: [08-02, 09, 10, 11, 12]

# Tech tracking
tech-stack:
  added: [astro@5.x]
  patterns: [astro-static-site, css-custom-properties-theming, sse-singleton, event-delegation-tabs]

key-files:
  created:
    - tools/gsd-review-broker/dashboard/package.json
    - tools/gsd-review-broker/dashboard/astro.config.mjs
    - tools/gsd-review-broker/dashboard/tsconfig.json
    - tools/gsd-review-broker/dashboard/src/layouts/Layout.astro
    - tools/gsd-review-broker/dashboard/src/pages/index.astro
    - tools/gsd-review-broker/dashboard/src/styles/design-tokens.css
    - tools/gsd-review-broker/dashboard/src/components/Sidebar.astro
    - tools/gsd-review-broker/dashboard/src/components/ThemeToggle.astro
    - tools/gsd-review-broker/dashboard/src/components/ConnectionStatus.astro
    - tools/gsd-review-broker/dashboard/src/scripts/theme.ts
    - tools/gsd-review-broker/dashboard/src/scripts/tabs.ts
    - tools/gsd-review-broker/dashboard/src/scripts/sse.ts
    - tools/gsd-review-broker/dashboard/dist/index.html
  modified: []

key-decisions:
  - "Used is:global CSS for ThemeToggle to enable data-theme selectors from html element"
  - "SSE singleton pattern with window.gsdSSE global for cross-script access"
  - "Inline is:inline theme init script in head prevents flash of wrong theme"
  - "Event delegation on sidebar-nav container for tab click handling"

patterns-established:
  - "Astro component pattern: scoped CSS in component files, global CSS via design-tokens.css"
  - "Custom event bus: window.dispatchEvent(new CustomEvent('sse-status', { detail })) for component communication"
  - "localStorage persistence pattern: gsd-dashboard-theme and gsd-dashboard-active-tab keys"
  - "Tab panel pattern: data-tab on nav items maps to id='tab-{name}' on sections"

requirements-completed: [DASH-01, DASH-02]

# Metrics
duration: 5min
completed: 2026-02-26
---

# Phase 8 Plan 1: Dashboard Shell Summary

**Astro static site with dark/cyan design system, sidebar navigation (Overview/Logs/Reviews/Pool), theme toggle, tab switching, and SSE connection utility built to dist/**

## Performance

- **Duration:** 5 min
- **Started:** 2026-02-26T11:31:21Z
- **Completed:** 2026-02-26T11:36:57Z
- **Tasks:** 2
- **Files modified:** 16

## Accomplishments

- Full Astro project at tools/gsd-review-broker/dashboard/ with static output configuration
- Comprehensive CSS design token system with dark (#1a1a1a bg, #00e5ff accent) and light (#f5f5f5 bg, #0097a7 accent) themes, full monospace typography stack
- Left sidebar navigation with four tabs (Overview, Logs, Reviews, Pool), badge count placeholders, and active state highlighting
- Theme toggle, tab switching, and SSE connection utility -- all client-side scripts with localStorage persistence
- Clean build to dist/ with zero external CDN dependencies

## Task Commits

Each task was committed atomically:

1. **Task 1: Initialize Astro project and build design system with layout shell** - `09405e0` (feat)
2. **Task 2: Create interactive components, scripts, and build to dist/** - `4b116bf` (feat)

## Files Created/Modified

- `tools/gsd-review-broker/dashboard/package.json` - Astro project definition with build scripts
- `tools/gsd-review-broker/dashboard/astro.config.mjs` - Static output config with /dashboard base path
- `tools/gsd-review-broker/dashboard/tsconfig.json` - Strict Astro TypeScript config
- `tools/gsd-review-broker/dashboard/src/layouts/Layout.astro` - Root HTML layout with sidebar + main content
- `tools/gsd-review-broker/dashboard/src/pages/index.astro` - Single page with four tab panel sections
- `tools/gsd-review-broker/dashboard/src/styles/design-tokens.css` - CSS custom properties for dark/light themes, typography, spacing
- `tools/gsd-review-broker/dashboard/src/components/Sidebar.astro` - Fixed left sidebar with nav items, badges, branding
- `tools/gsd-review-broker/dashboard/src/components/ThemeToggle.astro` - Sun/moon SVG toggle button
- `tools/gsd-review-broker/dashboard/src/components/ConnectionStatus.astro` - SSE status indicator with colored dot
- `tools/gsd-review-broker/dashboard/src/scripts/theme.ts` - Theme toggle logic with localStorage persistence
- `tools/gsd-review-broker/dashboard/src/scripts/tabs.ts` - Tab switching via event delegation with persistence
- `tools/gsd-review-broker/dashboard/src/scripts/sse.ts` - Shared EventSource to /dashboard/events with exponential backoff
- `tools/gsd-review-broker/dashboard/dist/index.html` - Built static HTML entry point
- `tools/gsd-review-broker/dashboard/dist/_astro/index.*.css` - Bundled CSS output
- `tools/gsd-review-broker/dashboard/.gitignore` - Ignores node_modules/ and .astro/

## Decisions Made

- **is:global CSS for ThemeToggle:** Astro scoped CSS cannot reach the html[data-theme] selector, so ThemeToggle uses `<style is:global>` for icon visibility toggling
- **SSE singleton on window.gsdSSE:** All tabs share one EventSource connection via a global manager exposed on window, avoiding multiple connections
- **Inline theme init in head:** A synchronous `is:inline` script in `<head>` reads localStorage and applies data-theme before first paint, preventing flash of wrong theme
- **Event delegation for tabs:** Single click listener on sidebar-nav container with closest() lookup, rather than per-button listeners

## Deviations from Plan

### Auto-fixed Issues

**1. [Rule 1 - Bug] Fixed ThemeToggle scoped CSS selector mismatch**
- **Found during:** Task 2 (build verification)
- **Issue:** Astro scoped CSS added data-astro-cid attributes to selectors like `[data-astro-cid-x3pjskd3][data-theme=dark]`, which only matches if data-theme is on the button element itself -- but data-theme lives on `<html>`
- **Fix:** Changed ThemeToggle from `<style>` to `<style is:global>` with selectors targeting `[data-theme="dark"] .theme-toggle .icon-sun`
- **Files modified:** tools/gsd-review-broker/dashboard/src/components/ThemeToggle.astro
- **Verification:** Rebuilt, confirmed CSS output uses global `[data-theme=dark] .theme-toggle .icon-sun{display:block}`
- **Committed in:** 4b116bf (Task 2 commit)

---

**Total deviations:** 1 auto-fixed (1 bug)
**Impact on plan:** Essential fix for theme toggle icon visibility. No scope creep.

## Issues Encountered

None - both tasks executed cleanly. Build succeeded on first attempt (after the ThemeToggle CSS fix).

## User Setup Required

None - no external service configuration required.

## Next Phase Readiness

- Dashboard shell built and ready at dist/ for Python broker to serve as static files
- Plan 08-02 can now wire static file serving in the Python broker routes
- Phases 9-12 will add Astro components that plug into the four tab panel sections
- SSE utility ready for live data once the broker SSE endpoint is connected

## Self-Check: PASSED

All 13 created files verified on disk. Both task commits (09405e0, 4b116bf) verified in git log.

---
*Phase: 08-dashboard-shell-and-infrastructure*
*Completed: 2026-02-26*
