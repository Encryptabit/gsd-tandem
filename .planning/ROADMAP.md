# Roadmap: GSD Tandem

## Overview

GSD Tandem delivers a local MCP review broker that intercepts every meaningful change Claude makes -- plans, code, verification artifacts -- and routes it through a reviewer (Codex, another Claude instance, or a human) before it gets applied. v1.0 established the core broker with full review lifecycle, GSD workflow integration, and reviewer pool management. v1.1 adds a web dashboard embedded in the broker server, giving users real-time visual insight into broker activity, review history, reviewer pool status, and structured logs without leaving their browser.

## Milestones

- Complete **v1.0 Core Broker** - Phases 1-7 (shipped 2026-02-18)
- Active **v1.1 Web Dashboard** - Phases 8-12 (in progress)

## Phases

**Phase Numbering:**
- Integer phases (1, 2, 3): Planned milestone work
- Decimal phases (2.1, 2.2): Urgent insertions (marked with INSERTED)

Decimal phases appear between their surrounding integers in numeric order.

<details>
<summary>Complete: v1.0 Core Broker (Phases 1-7) - SHIPPED 2026-02-18</summary>

- [x] **Phase 1: Core Broker Server** - SQLite-backed FastMCP server with state machine, basic propose/claim/verdict lifecycle
- [x] **Phase 2: Proposal and Diff Protocol** - Full proposal creation with unified diffs, verdict submission, conflict detection
- [x] **Phase 3: Discussion and Patches** - Multi-round threaded conversation, counter-patches, priority levels, push notifications
- [x] **Phase 4: GSD Workflow Integration** - Checkpoint mechanisms in GSD commands, sub-agent proposals, configurable granularity
- [x] **Phase 5: Observability and Validation** - Real-time visibility into broker activity, audit log, end-to-end workflow validation
- [x] **Phase 6: Review Gate Enforcement** - Make broker a proper gate: skip_diff_validation for post-commit diffs, orchestrator-mediated review, executor simplification
- [x] **Phase 7: Add Reviewer Lifecycle Management to Broker** - Broker-internal subprocess spawning, auto-scaling pool, fenced reclaim, lifecycle audit

</details>

### v1.1 Web Dashboard

- [ ] **Phase 8: Dashboard Shell and Infrastructure** - HTTP route, HTML scaffold, tab navigation, static asset serving embedded in broker
- [ ] **Phase 9: Overview Tab** - Broker status, configuration, aggregate review stats, active reviewer list
- [ ] **Phase 10: Log Viewer Tab** - JSONL log file browser and real-time streaming tail
- [ ] **Phase 11: Review Browser Tab** - Review list with filtering, detail view with diffs and verdicts, discussion threads
- [ ] **Phase 12: Pool Management Tab** - Reviewer subprocess status display and aggregate token usage tracking

## Phase Details

<details>
<summary>Complete: v1.0 Core Broker (Phases 1-7)</summary>

### Phase 1: Core Broker Server
**Goal**: A proposer and reviewer can connect to the broker, create a review, claim it, and reach a terminal state (approved/rejected/closed) via polling
**Depends on**: Nothing (first phase)
**Requirements**: FOUND-01, FOUND-02, FOUND-04, PROTO-01, PROTO-04, INTER-01, INTER-02
**Success Criteria** (what must be TRUE):
  1. FastMCP server starts on 127.0.0.1 and accepts connections from two separate MCP clients simultaneously
  2. Proposer can create a review with agent identity (agent_type, role, phase, plan, task) and receive a review_id back
  3. Reviewer can list pending reviews and claim one, transitioning it through the state machine (pending -> claimed -> approved/rejected -> closed)
  4. Proposer can poll for review status without exceeding MCP tool timeout (returns within 30 seconds per call)
  5. All review data persists in SQLite at .planning/codex_review_broker.sqlite3 and survives server restart
**Plans**: 3 plans

Plans:
- [x] 01-01-PLAN.md -- Project scaffolding, FastMCP server with lifespan-managed SQLite connection
- [x] 01-02-PLAN.md -- Review state machine, data models, and core lifecycle tools with tests
- [x] 01-03-PLAN.md -- Poll-and-return tool, .mcp.json config, live MCP connectivity verification

### Phase 2: Proposal and Diff Protocol
**Goal**: Proposer can submit proposals containing intent descriptions and unified diffs, reviewer can evaluate them, and invalid diffs are caught before review begins
**Depends on**: Phase 1
**Requirements**: FOUND-03, PROTO-02, PROTO-03, PROTO-05, PROTO-07
**Success Criteria** (what must be TRUE):
  1. Proposer can submit a proposal with both a natural language intent description and a machine-parseable unified diff, validated on submission
  2. Reviewer can submit a verdict (approve, request_changes, or comment) with optional notes explaining the decision
  3. Diffs are stored in standard unified format as text in SQLite, supporting multi-file diffs
  4. Broker runs git apply --check on proposal submission and re-validates on claim, rejecting diffs that fail either gate
  5. Full review lifecycle tools are exposed as MCP tools (create, claim, message, verdict, status, close)
**Plans**: 2 plans

Plans:
- [x] 02-01-PLAN.md -- Schema evolution, diff utilities, model updates, repo root discovery, and diff_utils tests
- [x] 02-02-PLAN.md -- Extend tool handlers (create_review, claim_review, submit_verdict), add get_proposal, proposal lifecycle tests

### Phase 3: Discussion and Patches
**Goal**: Proposer and reviewer can have multi-round conversations within a review, with the reviewer able to supply alternative patches and prioritize reviews
**Depends on**: Phase 2
**Requirements**: INTER-03, PROTO-06, PROTO-08, INTER-06
**Success Criteria** (what must be TRUE):
  1. Messages form a threaded conversation per review, supporting multiple rounds of back-and-forth (propose -> request changes -> revise -> re-review)
  2. Reviewer can attach a counter-patch (alternative unified diff) to a request_changes or comment verdict
  3. Reviews support priority levels (critical, normal, low) that affect the order reviews appear when the reviewer queries pending work
  4. When the reviewer is connected, push notifications alert them to new proposals; when disconnected, polling serves as fallback
**Plans**: 2 plans

Plans:
- [x] 03-01-PLAN.md -- Schema, models, utility modules (priority, notifications), message threading tools, and message tests
- [x] 03-02-PLAN.md -- Counter-patch in verdicts, accept/reject tools, priority sort in list_reviews, notification-enhanced polling

### Phase 4: GSD Workflow Integration
**Goal**: GSD commands (plan-phase, execute-phase, discuss-phase, verify-work) use the broker to submit proposals and await approval before applying changes
**Depends on**: Phase 3
**Requirements**: GSDI-01, GSDI-02, GSDI-03, GSDI-04, GSDI-05, INTER-04, INTER-05
**Success Criteria** (what must be TRUE):
  1. plan-phase pauses after drafting plan outlines, submits a proposal to the broker, and waits for approval before writing PLAN.md
  2. execute-phase pauses before each task commit (or per-plan, depending on config), submits a proposal, and waits for approval before applying
  3. Sub-agents (gsd-executor, gsd-planner) can submit proposals directly with their own agent identity without going through the parent command
  4. discuss-phase and verify-work can send handoff messages through the broker so the reviewer has awareness of workflow transitions
  5. Review granularity (per-task or per-plan) and execution mode (blocking or optimistic) are configurable via .planning/config.json
**Plans**: 3 plans

Plans:
- [x] 04-01-PLAN.md -- Category field support in broker, tandem config in config.json, category tests
- [x] 04-02-PLAN.md -- MCP tool permissions in commands, tandem review gates in planner/discuss/verify
- [x] 04-03-PLAN.md -- Executor tandem integration: blocking, optimistic, per-plan modes

### Phase 5: Observability and Validation
**Goal**: Users can monitor broker activity in real-time and query complete review history for any project
**Depends on**: Phase 4
**Requirements**: OBSV-01, GSDI-06
**Success Criteria** (what must be TRUE):
  1. User can see proposals flowing through the broker in real-time by watching the reviewer's terminal or querying status tools
  2. Complete review history and audit log is accessible via MCP tool, showing all reviews, verdicts, and messages for the project
  3. A full GSD workflow (plan-phase -> execute-phase -> verify-work) completes end-to-end with the broker mediating every proposal
**Plans**: 2 plans

Plans:
- [x] 05-01-PLAN.md -- Audit infrastructure, observability tools (activity feed, audit log, stats, timeline), audit wiring
- [x] 05-02-PLAN.md -- Observability tool tests and end-to-end workflow validation

### Phase 6: Review Gate Enforcement
**Goal**: All code changes flow through the broker as a proper gate, with the orchestrator mediating review on behalf of subagents
**Depends on**: Phase 5
**Requirements**: GSDI-02, GSDI-03
**Success Criteria** (what must be TRUE):
  1. create_review supports skip_diff_validation for post-commit diffs that are already applied to the working tree
  2. Executor subagents no longer reference broker tools directly; orchestrator handles all broker interaction
  3. execute-phase orchestrator submits post-plan diffs to broker and waits for verdict before proceeding
  4. CLAUDE.md documents the tandem review requirement for ad-hoc changes
**Plans**: 1 plan

Plans:
- [x] 06-01-PLAN.md -- skip_diff_validation flag, executor simplification, orchestrator review gate, CLAUDE.md rule

### Phase 7: Add Reviewer Lifecycle Management to Broker
**Goal:** Broker spawns, scales, drains, and terminates Codex reviewer subprocesses internally with fenced reclaim, replacing external manual launcher scripts
**Depends on:** Phase 6
**Requirements:** RLMC-01, RLMC-02, RLMC-03, RLMC-04, RLMC-05, RLMC-06, RLMC-07, RLMC-08
**Success Criteria** (what must be TRUE):
  1. Broker spawns Codex reviewer subprocesses using shell-free argv-list (WSL on Windows, native on Linux/macOS)
  2. Pool auto-scales based on pending:reviewer ratio, scales to zero when idle, cold-starts on first review
  3. Fenced reclaim prevents stale verdicts: claim_generation fence token rejects late submissions after timeout
  4. Graceful drain on TTL/idle timeout: finish current review, then terminate
  5. Per-reviewer stats tracked (reviews completed, average time, approval rate)
  6. Full lifecycle audit trail (spawn, drain, terminate, reclaim events)
  7. Manual override MCP tools (spawn_reviewer, kill_reviewer) available alongside auto-scaling
  8. Stale session recovery on broker restart: reclaim claimed reviews from dead sessions
**Plans**: 4 plans

Plans:
- [x] 07-01-PLAN.md -- Schema foundation: reviewers table, fence token columns, state machine update, config validation, prompt template
- [x] 07-02-PLAN.md -- ReviewerPool class with subprocess spawning, platform-aware argv builder, drain/terminate lifecycle
- [x] 07-03-PLAN.md -- Fenced reclaim: claim_generation fence tokens, claimed_at timestamps, stale verdict rejection
- [x] 07-04-PLAN.md -- Auto-scaling, background tasks, MCP tools (spawn/kill/list), lifespan integration, stale session recovery

</details>

### Phase 8: Dashboard Shell and Infrastructure
**Goal**: User can open a web dashboard in their browser served directly from the running broker, with a tabbed navigation shell ready for feature tabs
**Depends on**: Phase 7 (broker must be running)
**Requirements**: DASH-01, DASH-02
**Success Criteria** (what must be TRUE):
  1. User can navigate to http://127.0.0.1:{port}/dashboard in a browser and see a styled HTML page served by the broker process
  2. Dashboard displays a tab navigation bar with placeholder tabs (Overview, Logs, Reviews, Pool) that switch visible content areas
  3. Dashboard loads without any external CDN or network dependencies -- all assets are self-contained
  4. Dashboard page is visually polished with a distinctive, production-grade interface (DASH-02 frontend-design skill applied throughout all phases)
**Plans**: 2 plans

Plans:
- [ ] 08-01-PLAN.md -- Astro project setup, design system, layout shell with sidebar navigation, theme toggle, SSE utility, build to dist/
- [ ] 08-02-PLAN.md -- Python dashboard module (static file serving, SSE endpoint), server.py integration, route tests

**Note**: DASH-02 (frontend-design skill) is a cross-cutting process requirement applied during all dashboard phases, not a standalone deliverable. It is assigned here as its home phase but its quality standard applies to phases 9-12 as well.

### Phase 9: Overview Tab
**Goal**: User can see at a glance whether the broker is healthy, how reviews are performing, and which reviewers are active
**Depends on**: Phase 8
**Requirements**: OVER-01, OVER-02, OVER-03
**Success Criteria** (what must be TRUE):
  1. Dashboard displays broker status including server address, uptime, version, and key configuration settings from config.json
  2. Dashboard displays aggregate review statistics (total reviews, approval rate, average review time) populated from the same data as get_review_stats
  3. Dashboard displays a list of active reviewer subprocesses showing each reviewer's status, current review assignment, and per-reviewer stats (reviews completed, uptime)
**Plans**: TBD

### Phase 10: Log Viewer Tab
**Goal**: User can browse historical log files and watch new log entries appear in real-time without leaving the dashboard
**Depends on**: Phase 8
**Requirements**: LOGS-01, LOGS-02
**Success Criteria** (what must be TRUE):
  1. Dashboard lists available JSONL log files from both broker-logs/ and reviewer-logs/ directories with file names, sizes, and modification timestamps
  2. User can select a log file and view its entries rendered as a readable, scrollable list (not raw JSON)
  3. User can activate a live tail mode that streams new log entries into the view as they are written to disk, without manual refresh
**Plans**: TBD

### Phase 11: Review Browser Tab
**Goal**: User can navigate the full history of reviews, inspect any review's diff and metadata, and read the complete discussion thread
**Depends on**: Phase 8
**Requirements**: REVW-01, REVW-02, REVW-03
**Success Criteria** (what must be TRUE):
  1. Dashboard displays a sortable, filterable list of reviews with columns for status, category, priority, agent identity, and timestamps
  2. User can click a review to see its detail view showing intent description, unified diff (rendered readably), verdict history, and metadata
  3. User can view the full discussion thread for any review, showing all messages in chronological order with sender identity and timestamps
  4. User can filter reviews by status (pending, approved, rejected, closed) and by category to find specific reviews quickly
**Plans**: TBD

### Phase 12: Pool Management Tab
**Goal**: User can monitor the reviewer pool health and understand token consumption across all reviewer subprocesses
**Depends on**: Phase 8, Phase 9 (builds on reviewer data patterns from Overview)
**Requirements**: POOL-01, POOL-02
**Success Criteria** (what must be TRUE):
  1. Dashboard displays each reviewer subprocess with its status, reviews completed count, uptime, and current review assignment (if any)
  2. Dashboard displays aggregate token usage accumulated across all reviewer subprocesses over the broker's lifetime
  3. Token usage data is collected from Codex reviewer subprocesses and persisted so it survives broker restarts
**Plans**: TBD

## Progress

**Execution Order:**
Phases execute in numeric order: 1 -> 2 -> 3 -> 4 -> 5 -> 6 -> 7 -> 8 -> 9 -> 10 -> 11 -> 12

| Phase | Milestone | Plans Complete | Status | Completed |
|-------|-----------|----------------|--------|-----------|
| 1. Core Broker Server | v1.0 | 3/3 | Complete | 2026-02-16 |
| 2. Proposal and Diff Protocol | v1.0 | 2/2 | Complete | 2026-02-17 |
| 3. Discussion and Patches | v1.0 | 2/2 | Complete | 2026-02-17 |
| 4. GSD Workflow Integration | v1.0 | 3/3 | Complete | 2026-02-17 |
| 5. Observability and Validation | v1.0 | 2/2 | Complete | 2026-02-18 |
| 6. Review Gate Enforcement | v1.0 | 1/1 | Complete | 2026-02-18 |
| 7. Reviewer Lifecycle Management | v1.0 | 4/4 | Complete | 2026-02-18 |
| 8. Dashboard Shell and Infrastructure | v1.1 | 0/2 | Not started | - |
| 9. Overview Tab | v1.1 | 0/? | Not started | - |
| 10. Log Viewer Tab | v1.1 | 0/? | Not started | - |
| 11. Review Browser Tab | v1.1 | 0/? | Not started | - |
| 12. Pool Management Tab | v1.1 | 0/? | Not started | - |
