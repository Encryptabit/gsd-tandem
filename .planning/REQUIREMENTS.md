# Requirements: GSD Tandem

**Defined:** 2026-02-16
**Core Value:** Every meaningful change Claude makes gets reviewed incrementally by a second intelligence before being applied

## v1 Requirements

### MCP Server Foundation

- [ ] **FOUND-01**: Broker persists all review data in SQLite at .planning/codex_review_broker.sqlite3 with WAL mode and BEGIN IMMEDIATE transactions
- [ ] **FOUND-02**: Broker runs as FastMCP Streamable HTTP server bound to 127.0.0.1 only, supporting concurrent connections from proposer and reviewer
- [ ] **FOUND-03**: Broker exposes MCP tools for full review lifecycle (create, claim, message, verdict, status, close)
- [ ] **FOUND-04**: Broker works on Windows (PowerShell) and macOS/Linux (bash) without platform-specific workarounds

### Review Protocol

- [ ] **PROTO-01**: Reviews follow a state machine: pending -> claimed -> in_review -> approved/changes_requested -> closed, with valid transition enforcement
- [ ] **PROTO-02**: Proposer can create a proposal containing intent description (natural language) and unified diff (machine-parseable), validated on submission
- [ ] **PROTO-03**: Reviewer can submit a verdict: approve, request_changes, or comment, each with optional notes
- [ ] **PROTO-04**: Proposer creates a review and reviewer claims it, with one reviewer per review
- [ ] **PROTO-05**: Diffs are transported in standard unified format, stored as text in SQLite, supporting multi-file diffs
- [ ] **PROTO-06**: Reviewer can submit counter-patches (alternative unified diffs) attached to request_changes or comment verdicts
- [ ] **PROTO-07**: Broker runs `git apply --check` on proposal submission to detect conflicts before review begins
- [ ] **PROTO-08**: Reviews support priority levels (critical, normal, low) affecting reviewer queue ordering

### Agent Interaction

- [ ] **INTER-01**: Every message and proposal includes full agent identity: agent_type, agent_role, phase, plan, task number
- [ ] **INTER-02**: Proposer blocks (poll-and-return with configurable interval) until reviewer responds, respecting MCP tool timeout constraints
- [ ] **INTER-03**: Messages form a threaded conversation per review, supporting multi-round back-and-forth (propose -> request changes -> revise -> re-review)
- [ ] **INTER-04**: Review granularity is configurable: per-task (default) or per-plan, settable in .planning/config.json
- [ ] **INTER-05**: Optimistic execution mode available: proposer applies changes provisionally, rolls back on rejection (opt-in per config)
- [ ] **INTER-06**: Push notification mechanism available when reviewer is connected, falling back to polling when not

### GSD Integration

- [ ] **GSDI-01**: plan-phase, execute-phase, discuss-phase, and verify-work commands include mcp__gsdreview__* in allowed-tools
- [ ] **GSDI-02**: Checkpoint mechanism in plan-phase pauses after drafting plan outlines to submit proposal and await approval before writing PLAN.md
- [ ] **GSDI-03**: Checkpoint mechanism in execute-phase pauses before each task commit (or per-plan, per config) to submit proposal and await approval
- [ ] **GSDI-04**: Sub-agents (gsd-executor, gsd-planner) can submit proposals directly to the broker with their own agent identity
- [ ] **GSDI-05**: discuss-phase and verify-work can send handoff messages through the broker for reviewer awareness
- [ ] **GSDI-06**: Review history and audit log accessible via MCP tool, showing all reviews, verdicts, and messages for the project

### Observability

- [ ] **OBSV-01**: User can see proposals flowing through the broker in real-time by watching the reviewer's terminal or querying status tools

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

## Traceability

| Requirement | Phase | Status |
|-------------|-------|--------|
| FOUND-01 | Phase 1 | Pending |
| FOUND-02 | Phase 1 | Pending |
| FOUND-03 | Phase 2 | Pending |
| FOUND-04 | Phase 1 | Pending |
| PROTO-01 | Phase 1 | Pending |
| PROTO-02 | Phase 2 | Pending |
| PROTO-03 | Phase 2 | Pending |
| PROTO-04 | Phase 1 | Pending |
| PROTO-05 | Phase 2 | Pending |
| PROTO-06 | Phase 3 | Pending |
| PROTO-07 | Phase 2 | Pending |
| PROTO-08 | Phase 3 | Pending |
| INTER-01 | Phase 1 | Pending |
| INTER-02 | Phase 1 | Pending |
| INTER-03 | Phase 3 | Pending |
| INTER-04 | Phase 4 | Pending |
| INTER-05 | Phase 4 | Pending |
| INTER-06 | Phase 3 | Pending |
| GSDI-01 | Phase 4 | Pending |
| GSDI-02 | Phase 4 | Pending |
| GSDI-03 | Phase 4 | Pending |
| GSDI-04 | Phase 4 | Pending |
| GSDI-05 | Phase 4 | Pending |
| GSDI-06 | Phase 5 | Pending |
| OBSV-01 | Phase 5 | Pending |

**Coverage:**
- v1 requirements: 25 total
- Mapped to phases: 25
- Unmapped: 0

---
*Requirements defined: 2026-02-16*
*Last updated: 2026-02-16 after roadmap creation*
