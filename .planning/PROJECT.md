# GSD Tandem

## What This Is

A fork of the get-shit-done (GSD) meta-prompting system that adds real-time tandem pairing between Claude Code and a reviewer (Codex, another Claude instance, or a human) via a shared local MCP server. During planning and execution, Claude proposes changes as unified diffs through a review broker, and the reviewer approves, requests changes, or supplies patches before Claude applies them. The system provides full workflow coverage across plan, execute, discuss, and verify stages.

## Core Value

Every meaningful change Claude makes — plans, code, verification artifacts — gets reviewed incrementally by a second intelligence before being applied, catching errors and improving quality without slowing the developer down.

## Requirements

### Validated

- ✓ GSD command system (30+ slash commands) — existing
- ✓ Agent orchestration (11 specialized agents) — existing
- ✓ Wave-based parallel execution with dependency tracking — existing
- ✓ State management via .planning/ directory — existing
- ✓ Atomic task commits with per-task git history — existing
- ✓ Plan checker and verifier agents — existing
- ✓ Session pause/resume with context preservation — existing
- ✓ Multi-runtime support (Claude Code, OpenCode, Gemini CLI) — existing
- ✓ MCP review broker server (Python FastMCP, Streamable HTTP, SQLite persistence) — v1.0
- ✓ Review lifecycle: create, claim, message, submit verdict, close — v1.0
- ✓ Proposal/approval protocol with unified diffs, verdicts, counter-patches — v1.0
- ✓ Full agent identity in proposals (agent type, phase, plan, task) — v1.0
- ✓ Claude-side checkpoint mechanism with broker gate enforcement — v1.0
- ✓ Configurable wait strategy and review granularity — v1.0
- ✓ Full GSD workflow coverage (plan, execute, discuss, verify) — v1.0
- ✓ Reviewer-agnostic design (Codex, Claude, human) — v1.0
- ✓ MCP observability tools (audit log, activity feed, stats, timeline) — v1.0
- ✓ Reviewer lifecycle management (auto-scaling pool, fenced reclaim, subprocess spawning) — v1.0
- ✓ localhost-only binding, SQLite persistence — v1.0

### Active

- [ ] Web dashboard embedded in broker (Overview, Logs, Review Browser, Pool Management tabs)
- [ ] Real-time broker status and configuration display
- [ ] JSONL log viewer for both broker and reviewer subprocess logs
- [ ] Review browser with diff viewer and discussion threads
- [ ] Pool management UI (spawn, kill, status of reviewer subprocesses)

### Out of Scope

- Remote/cloud deployment of the broker — local-only by design
- Reviewer authentication/authorization — trusted local environment
- Multi-project broker (one broker per project) — keeps SQLite aligned with .planning/
- Codex committing code — Claude remains the sole writer/committer
- Rewriting GSD's core workflow engine — minimal fork, additive changes only
- OpenCode/Gemini runtime support for tandem mode — Claude Code + reviewer only for v1

## Context

GSD is a meta-prompting system where markdown files define commands, agents, and workflows. Commands have YAML frontmatter with `allowed-tools` lists. Agents are spawned via `Task(subagent_type="gsd-*")`. The system uses `.planning/` for all state and artifacts.

The key files to modify are:
- `commands/gsd/plan-phase.md` — add MCP tools to allowed-tools, add checkpoint instructions
- `commands/gsd/execute-phase.md` — add MCP tools, checkpoint instructions
- `commands/gsd/discuss-phase.md` — add MCP tools for handoff messages
- `commands/gsd/verify-work.md` — add MCP tools for handoff messages
- `agents/gsd-planner.md` — add checkpoint behavior in execution flow
- `agents/gsd-executor.md` — add checkpoint behavior in task execution

The MCP server will be a new Python project at `tools/gsd-review-broker/` using FastMCP with Streamable HTTP transport. Both Claude Code and the reviewer connect to the same server.

GSD's existing checkpoint system (checkpoint:human-verify, checkpoint:decision) provides a pattern to follow — tandem review checkpoints are analogous but go to a machine reviewer instead of the human.

## Constraints

- **Transport**: MCP Streamable HTTP on localhost — both Claude Code and reviewer must connect to the same server
- **Persistence**: SQLite under .planning/ so review threads align with GSD artifacts and get cleaned up together
- **Security**: 127.0.0.1 binding only; broker never executes shell commands; no credential storage
- **Fork philosophy**: Minimal diff from upstream GSD — prefer additive new commands/instructions over deep rewrites
- **Cross-platform**: Must work on Windows (PowerShell) and macOS/Linux (bash)
- **Zero cloud dependency**: Everything runs locally, no SaaS, no external APIs
- **Python dependency**: Broker requires Python 3.10+ and FastMCP SDK — acceptable since it's a dev tool

## Key Decisions

| Decision | Rationale | Outcome |
|----------|-----------|---------|
| MCP server in Python (FastMCP) | Official MCP SDK, Streamable HTTP support, fast to build | — Pending |
| Broker inside GSD fork (tools/) | Tightly coupled to GSD workflow, one repo, one .planning/ | — Pending |
| SQLite for persistence | Simple, file-based, aligns with .planning/ directory, no server needed | — Pending |
| Reviewer-agnostic protocol | Future-proofs for Claude-Claude pairing, human review, other tools | — Pending |
| Blocking wait as default | Simpler to reason about, prevents state divergence | — Pending |
| Full agent identity in proposals | Enables observability and maps reviews back to agent JSONL logs | — Pending |

## Current Milestone: v1.1 Web Dashboard

**Goal:** Give users real-time visual insight into broker activity, review history, reviewer pool status, and structured logs through a web dashboard embedded in the broker server.

**Target features:**
- Overview tab with broker status, config, stats, active reviewers
- Logs tab streaming JSONL from broker and reviewer subprocesses
- Review browser for navigating reviews, viewing diffs, reading discussion threads
- Pool management for spawning/killing/monitoring reviewer subprocesses

---
*Last updated: 2026-02-25 after milestone v1.1 start*
