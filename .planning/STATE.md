# Project State

## Project Reference

See: .planning/PROJECT.md (updated 2026-02-25)

**Core value:** Every meaningful change Claude makes gets reviewed incrementally by a second intelligence before being applied
**Current focus:** Milestone v1.1 — Phase 8: Dashboard Shell and Infrastructure (Astro migration)

## Current Position

Phase: 8 of 12 (Dashboard Shell and Infrastructure)
Plan: 1 of 2 complete
Status: Executing — Plan 08-01 (Astro shell) complete, Plan 08-02 (static serving) pending
Last activity: 2026-02-26 — Completed 08-01-PLAN.md (dashboard shell and infrastructure)

Progress: [====================............] 62% (v1.0 complete, v1.1 phase 8 plan 1/2 done)

## Performance Metrics

**v1.0 Velocity:**
- Total plans completed: 17
- Average duration: ~6.2 minutes
- Total execution time: ~105 minutes

**v1.1 Velocity:**
- Previous 08-01 and 08-02 implementations scrapped (inline HTML approach abandoned)
- 08-01 (Astro shell): 5 min, 2 tasks, 16 files

## Accumulated Context

### Decisions

Decisions are logged in PROJECT.md Key Decisions table.

v1.1 decisions:
- **Astro static site generator** for dashboard frontend (replaces inline HTML/CSS/JS in Python)
- Astro project at `tools/gsd-review-broker/dashboard/`, builds to `dashboard/dist/`
- Python broker serves built static files at `/dashboard`, SSE endpoint stays in Python
- Built `dist/` committed to repo — no Node.js needed at runtime, only at build time
- register_*_routes(mcp) pattern for modular route registration (unchanged)
- is:global CSS for ThemeToggle (Astro scoped CSS cannot reach html[data-theme] from child component)
- SSE singleton on window.gsdSSE for cross-script EventSource sharing
- Custom event bus (sse-status events) for decoupled component communication

Key v1.0 decisions carried forward:

- MockContext dataclass pattern for testing @mcp.tool decorated functions via .fn attribute
- Dict error returns over exceptions for agent-friendly tool responses
- BEGIN IMMEDIATE / COMMIT / ROLLBACK for all database write operations
- Per-process asyncio.Lock write mutex serializes all write operations on shared SQLite connection
- Orchestrator-mediated review: only execute-phase workflow interacts with broker
- Broker writes structured JSONL logs to platform-specific app data dir (broker-logs/ and reviewer-logs/)

### v1.1 Phase Structure

- Phase 8: Dashboard shell (DASH-01, DASH-02) -- Astro project, static file serving, navigation shell, SSE
- Phase 9: Overview tab (OVER-01, OVER-02, OVER-03) -- status, stats, active reviewers
- Phase 10: Log viewer tab (LOGS-01, LOGS-02) -- JSONL browser + live tail
- Phase 11: Review browser tab (REVW-01, REVW-02, REVW-03) -- list, detail, discussion
- Phase 12: Pool management tab (POOL-01, POOL-02) -- pool status + token usage

### Pending Todos

- Future Goal: Add a master-reviewer orchestration layer for reviewer pools
- POOL-02 requires new token usage collection from Codex reviewer subprocesses

### Blockers/Concerns

- None currently

## Session Continuity

Last session: 2026-02-26
Stopped at: Completed 08-01-PLAN.md (Astro dashboard shell with design system, sidebar nav, theme toggle, tabs, SSE)
Resume notes: Execute 08-02-PLAN.md next (Python static file serving and SSE endpoint wiring).
