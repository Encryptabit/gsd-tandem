# Requirements: GSD Tandem

**Defined:** 2026-02-16
**Core Value:** Every meaningful change Claude makes gets reviewed incrementally by a second intelligence before being applied

## v1 Requirements

### MCP Server Foundation

- [x] **FOUND-01**: Broker persists all review data in SQLite at .planning/codex_review_broker.sqlite3 with WAL mode and BEGIN IMMEDIATE transactions
- [x] **FOUND-02**: Broker runs as FastMCP Streamable HTTP server bound to 127.0.0.1 only, supporting concurrent connections from proposer and reviewer
- [x] **FOUND-03**: Broker exposes MCP tools for full review lifecycle (create, claim, message, verdict, status, close)
- [x] **FOUND-04**: Broker works on Windows (PowerShell) and macOS/Linux (bash) without platform-specific workarounds

### Review Protocol

- [x] **PROTO-01**: Reviews follow a state machine: pending -> claimed -> in_review -> approved/changes_requested -> closed, with valid transition enforcement
- [x] **PROTO-02**: Proposer can create a proposal containing intent description (natural language) and unified diff (machine-parseable), validated on submission
- [x] **PROTO-03**: Reviewer can submit a verdict: approve, request_changes, or comment, each with optional notes
- [x] **PROTO-04**: Proposer creates a review and reviewer claims it, with one reviewer per review
- [x] **PROTO-05**: Diffs are transported in standard unified format, stored as text in SQLite, supporting multi-file diffs
- [x] **PROTO-06**: Reviewer can submit counter-patches (alternative unified diffs) attached to request_changes or comment verdicts
- [x] **PROTO-07**: Broker runs `git apply --check` on proposal submission to detect conflicts before review begins
- [x] **PROTO-08**: Reviews support priority levels (critical, normal, low) affecting reviewer queue ordering

### Reviewer Lifecycle Management

- [x] **RLMC-01**: Broker spawns Codex reviewer subprocesses with shell-free argv lists across Windows (WSL) and native platforms
- [x] **RLMC-02**: Reviewer pool auto-scales using pending:reviewer ratio and reactive cold-start checks
- [x] **RLMC-03**: Fenced reclaim prevents stale verdicts using claim_generation and reclaim transitions
- [x] **RLMC-04**: Reviewer lifecycle supports drain/terminate/shutdown with subprocess + DB state consistency
- [x] **RLMC-05**: Broker exposes manual override MCP tools for reviewer lifecycle control (spawn/kill/list)
- [x] **RLMC-06**: Reviewer lifecycle schema exists (reviewers table, status model, lifecycle audit event types)
- [x] **RLMC-07**: Reviewer pool configuration is validated with strict allowlists/range checks and backward-compatible disable behavior
- [x] **RLMC-08**: Broker startup recovery reclaims stale claimed reviews via ownership sweep and session fencing

### Agent Interaction

- [x] **INTER-01**: Every message and proposal includes full agent identity: agent_type, agent_role, phase, plan, task number
- [x] **INTER-02**: Proposer blocks (poll-and-return with configurable interval) until reviewer responds, respecting MCP tool timeout constraints
- [x] **INTER-03**: Messages form a threaded conversation per review, supporting multi-round back-and-forth (propose -> request changes -> revise -> re-review)
- [x] **INTER-04**: Review granularity is configurable: per-task (default) or per-plan, settable in .planning/config.json
- [x] **INTER-05**: Optimistic execution mode available: proposer applies changes provisionally, rolls back on rejection (opt-in per config)
- [x] **INTER-06**: Push notification mechanism available when reviewer is connected, falling back to polling when not

### GSD Integration

- [x] **GSDI-01**: plan-phase, execute-phase, discuss-phase, and verify-work commands include mcp__gsdreview__* in allowed-tools
- [x] **GSDI-02**: Checkpoint mechanism in plan-phase pauses after drafting plan outlines to submit proposal and await approval before writing PLAN.md
- [x] **GSDI-03**: Checkpoint mechanism in execute-phase pauses before each task commit (or per-plan, per config) to submit proposal and await approval
- [x] **GSDI-04**: Sub-agents (gsd-executor, gsd-planner) can submit proposals directly to the broker with their own agent identity
- [x] **GSDI-05**: discuss-phase and verify-work can send handoff messages through the broker for reviewer awareness
- [ ] **GSDI-06**: Review history and audit log accessible via MCP tool, showing all reviews, verdicts, and messages for the project

### Observability

- [ ] **OBSV-01**: User can see proposals flowing through the broker in real-time by watching the reviewer's terminal or querying status tools

## v1.1 Requirements

Requirements for Web Dashboard milestone. Each maps to roadmap phases 8+.

### Dashboard Infrastructure

- [ ] **DASH-01**: Broker serves a web dashboard on a /dashboard route embedded in the existing FastMCP server, no separate process needed
- [ ] **DASH-02**: Dashboard UI is built using frontend-design skill for production-grade, distinctive interface quality

### Overview

- [ ] **OVER-01**: Dashboard displays broker status and running configuration (address, uptime, version, config settings)
- [ ] **OVER-02**: Dashboard displays aggregate review stats (total reviews, approval rate, avg review time) reusing get_review_stats data
- [ ] **OVER-03**: Dashboard displays active reviewer subprocesses with status, current review, and per-reviewer stats

### Log Viewer

- [ ] **LOGS-01**: Dashboard lists and displays broker and reviewer JSONL log files from disk
- [ ] **LOGS-02**: Dashboard streams new log entries in real-time as they are written (live tail)

### Review Browser

- [ ] **REVW-01**: Dashboard lists reviews with status and category filtering and sortable columns
- [ ] **REVW-02**: Dashboard displays review detail including intent, unified diff, verdicts, and metadata
- [ ] **REVW-03**: Dashboard displays the full discussion thread for a review

### Pool Management

- [ ] **POOL-01**: Dashboard displays reviewer pool status (each subprocess: status, reviews completed, uptime, current review)
- [ ] **POOL-02**: Dashboard displays aggregate token usage accumulated across all reviewer subprocesses over broker lifetime

## v2 Requirements

### Enhanced Review

- **ENHC-01**: Structured inline comments on specific diff hunks (line-level feedback)
- **ENHC-02**: Review delegation/escalation to a different reviewer when primary is unavailable
- **ENHC-03**: Review metrics and analytics (approval rate, average review time, rejection reasons)
- **ENHC-04**: Review templates/checklists for consistent evaluation criteria

### Extended Integration

- **EXTI-01**: Diff application assistance (broker helps proposer apply complex patches)
- **EXTI-02**: Multi-reviewer consensus (quorum voting for critical changes)

## Out of Scope

| Feature | Reason |
|---------|--------|
| Rich diff rendering (syntax highlighting, side-by-side) | Agents read text; adds web UI dependency contradicting local-MCP-only design |
| Authentication / authorization | Localhost trusted environment; zero value on 127.0.0.1 |
| File-level access control (CODEOWNERS) | Over-engineering for 1:1 review broker with single reviewer |
| Automatic merge/commit on approval | Violates "Claude remains sole writer/committer" constraint |
| Real-time collaborative editing | Requires WebSocket sync, OT/CRDT -- fundamentally different architecture |
| Natural language diff generation | Unreliable; proposer has git diff access for machine-generated diffs |
| Review bot / linting integration | Broker is communication channel, not CI system; security risk |
| Remote/cloud deployment | Local-only by design; no SaaS dependency |
| OpenCode/Gemini runtime support | Claude Code + reviewer only for v1 |
| Log level filtering | Deferred — live tail + file browsing sufficient for v1.1 |
| Spawn/kill reviewers from UI | Deferred — MCP tools and CLI sufficient for v1.1 |
| REST API as separate layer | Implicit in dashboard routes; no separate API versioning needed |

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 | Phase 1 | Complete |
| FOUND-02 | Phase 1 | Complete |
| FOUND-03 | Phase 2 | Complete |
| FOUND-04 | Phase 1 | Complete |
| PROTO-01 | Phase 1 | Complete |
| PROTO-02 | Phase 2 | Complete |
| PROTO-03 | Phase 2 | Complete |
| PROTO-04 | Phase 1 | Complete |
| PROTO-05 | Phase 2 | Complete |
| PROTO-06 | Phase 3 | Complete |
| PROTO-07 | Phase 2 | Complete |
| PROTO-08 | Phase 3 | Complete |
| INTER-01 | Phase 1 | Complete |
| INTER-02 | Phase 1 | Complete |
| INTER-03 | Phase 3 | Complete |
| INTER-04 | Phase 4 | Complete |
| INTER-05 | Phase 4 | Complete |
| INTER-06 | Phase 3 | Complete |
| GSDI-01 | Phase 4 | Complete |
| GSDI-02 | Phase 4 | Complete |
| GSDI-03 | Phase 4 | Complete |
| GSDI-04 | Phase 4 | Complete |
| GSDI-05 | Phase 4 | Complete |
| GSDI-06 | Phase 5 | Pending |
| OBSV-01 | Phase 5 | Pending |
| RLMC-01 | Phase 7 | Complete |
| RLMC-02 | Phase 7 | Complete |
| RLMC-03 | Phase 7 | Complete |
| RLMC-04 | Phase 7 | Complete |
| RLMC-05 | Phase 7 | Complete |
| RLMC-06 | Phase 7 | Complete |
| RLMC-07 | Phase 7 | Complete |
| RLMC-08 | Phase 7 | Complete |

**Coverage:**
- v1.0 requirements: 33 total (31 complete, 2 pending)
- v1.1 requirements: 12 total
- Mapped to phases: 33 (v1.1 mapping pending roadmap creation)
- Unmapped: 12 (v1.1 — will be mapped during roadmap creation)

---
*Requirements defined: 2026-02-16*
*Last updated: 2026-02-25 after v1.1 requirements definition*
