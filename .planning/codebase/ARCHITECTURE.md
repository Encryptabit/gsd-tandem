# Architecture

**Analysis Date:** 2026-02-16

## Pattern Overview

**Overall:** Multi-layer agent orchestration system for spec-driven development

**Key Characteristics:**
- Modular agent-based workflow orchestration spawned via slash commands
- Context engineering layer that ensures consistent Claude quality across multi-session projects
- Declarative YAML-frontmatter markdown specifications for all agents, commands, and templates
- Wave-based parallel execution with dependency tracking for atomic task commits
- State management through `.planning/` directory with git-tracked workflow artifacts

## Layers

**Presentation Layer (Commands):**
- Purpose: Entry points for user interaction via slash commands in Claude Code, OpenCode, Gemini CLI
- Location: `commands/gsd/` — 30+ command definitions (e.g., `/gsd:new-project`, `/gsd:execute-phase`)
- Contains: YAML-frontmatter markdown files with objective, execution context, process steps
- Depends on: Workflows, templates, shell execution via Bash
- Used by: User directly via runtime (Claude Code, OpenCode, Gemini)

**Agent Layer:**
- Purpose: Specialized subagents that handle specific phases of project development (research, planning, execution, verification)
- Location: `agents/gsd-*.md` — 11 agent definitions spawned on-demand
- Contains: Agent definitions (gsd-planner, gsd-executor, gsd-phase-researcher, gsd-verifier, gsd-debugger, etc.)
- Depends on: Workflows, templates, reference documentation, state management
- Used by: Orchestrator commands when spawning Task(subagent_type="...")

**Orchestration Layer (Workflows):**
- Purpose: Defines step-by-step processes for complex commands; maintains workflow state and gates
- Location: `get-shit-done/workflows/` — process definitions for new-project, plan-phase, execute-phase, etc.
- Contains: Markdown with XML-tagged process steps, CLI calls to `gsd-tools.cjs`, conditional branching, error handling
- Depends on: Templates, tools, state management, git operations
- Used by: Commands invoke workflows end-to-end; orchestrators call gsd-tools for state queries

**State Management Layer:**
- Purpose: Persistent project context across sessions via `.planning/` directory
- Location: `.planning/PROJECT.md`, `.planning/ROADMAP.md`, `.planning/STATE.md`, `.planning/config.json`, `.planning/research/`, `.planning/phases/`
- Contains: Project definition, phase planning (REQUIREMENTS.md), phase context (CONTEXT.md), execution plans (PLAN.md, SUMMARY.md), codebase analysis (STACK.md, ARCHITECTURE.md, etc.)
- Depends on: Git tracking (committed to repo), file system access
- Used by: All orchestrators, agents, and workflows read state to maintain context across commands

**Tooling Layer:**
- Purpose: Utilities for state operations, model resolution, git operations, and manifest management
- Location: `get-shit-done/bin/gsd-tools.cjs` — Node.js utility for init, roadmap queries, state snapshots, manifest generation
- Contains: CLI tool called via `node ~/.claude/get-shit-done/bin/gsd-tools.cjs <command> <args>`
- Depends on: File system, git CLI, JSON parsing
- Used by: Workflows invoke for queries and state mutations (init, roadmap, phase-plan-index, state-snapshot)

**Template Layer:**
- Purpose: Standardized markdown and JSON structures for consistent artifact generation
- Location: `get-shit-done/templates/` — templates for PROJECT.md, REQUIREMENTS.md, PLAN.md, SUMMARY.md, codebase analysis docs, config.json
- Contains: Template files with placeholders and structural examples
- Depends on: None (passive reference)
- Used by: Agents and workflows when creating planning artifacts

**Reference Layer:**
- Purpose: Documentation, patterns, and guidance for agents to follow
- Location: `get-shit-done/references/` — decision trees, pattern guides, verification patterns, model profiles, questioning frameworks
- Contains: Files like questioning.md (how to ask users), model-profiles.md (model selection logic), checkpoints.md (human verification patterns), verification-patterns.md, tdd.md
- Depends on: None (passive reference)
- Used by: Agents and workflows reference during execution for consistency

## Data Flow

**New Project Flow:**
1. User runs `/gsd:new-project` command
2. Command invokes `new-project.md` workflow
3. Workflow uses `AskUserQuestion` tool to gather project details (goals, constraints, tech stack, edge cases)
4. Workflow triggers parallel researcher agents (stack, features, architecture, pitfalls) via Task()
5. Researchers write to `.planning/research/` directory
6. Orchestrator synthesizes findings into REQUIREMENTS.md and ROADMAP.md
7. PROJECT.md, config.json, and STATE.md written to `.planning/`
8. Artifacts committed to git

**Phase Planning Flow (Iterative):**
1. User runs `/gsd:plan-phase N`
2. Orchestrator workflow initializes via `gsd-tools.cjs init plan-phase`
3. If no context: Ask user to run `/gsd:discuss-phase` first (captures design decisions in CONTEXT.md)
4. Spawn gsd-phase-researcher (if enabled) to investigate implementation approach
5. Spawn gsd-planner to create PLAN.md with 2-3 atomic tasks per plan
6. Spawn gsd-plan-checker to verify plans achieve phase goals
7. If checker rejects: Loop to revision (max 3 iterations)
8. On approval: Plans written to `.planning/phases/NN-phase-slug/` directory
9. Artifacts committed to git

**Phase Execution Flow (Parallel Waves):**
1. User runs `/gsd:execute-phase N`
2. Orchestrator workflow initializes, discovers all plans
3. gsd-tools.cjs phase-plan-index groups plans by wave (dependency-aware)
4. For each wave sequentially:
   - Orchestrator reports what's being built
   - Spawn gsd-executor agents in parallel (if parallelization enabled)
   - Each executor reads plan file, executes 2-3 tasks atomically, commits per task
   - Executor writes SUMMARY.md to plan directory
   - Orchestrator spot-checks SUMMARY.md and git commits
5. After all waves complete: gsd-verifier spawned if verification enabled
6. Verifier checks that phase objectives achieved
7. STATE.md updated with execution position

**Verification Flow:**
1. User runs `/gsd:verify-work N`
2. Verifier analyzes phase deliverables and extracts testable actions
3. User walks through checklist, marking pass/fail on each deliverable
4. If any fail: gsd-debugger spawned to diagnose root cause
5. Debugger writes gap-closure plans to phase directory
6. User can re-run `/gsd:execute-phase N --gaps-only` to fix and retry verification

**State Management:**
- Stateless orchestrators: Each command reads full context from `.planning/` at start, computes next step, delegates to agents
- Agents operate independently: Each agent loads only necessary context (plan, state, config), executes in fresh 200k context window
- Handoff via STATE.md: Captures current position, blocking decisions, accumulated state — survives session breaks via `/gsd:pause-work` and `/gsd:resume-work`

## Key Abstractions

**Command:**
- Purpose: User-facing entry point; maps to slash command in Claude Code
- Location: `commands/gsd/{command}.md`
- Pattern: YAML frontmatter (name, description, allowed-tools, argument-hint) + markdown body with objective, execution_context, and process
- Example: `/gsd:plan-phase` invokes `commands/gsd/plan-phase.md`

**Agent:**
- Purpose: Specialized executor spawned on-demand via Task(subagent_type="...")
- Location: `agents/gsd-{role}.md`
- Pattern: YAML frontmatter (name, description, allowed-tools, color) + XML-tagged role and context
- Examples: gsd-planner (creates task breakdowns), gsd-executor (implements tasks), gsd-verifier (checks goals achieved)

**Workflow:**
- Purpose: Procedural definition of complex multi-step orchestration
- Location: `get-shit-done/workflows/{command}.md`
- Pattern: Markdown with XML tags (purpose, required_reading, process) containing step definitions
- Each step: condition check, action (bash call or Task spawn), error handling, result routing

**Plan:**
- Purpose: Atomic, executable task list with dependencies
- Location: `.planning/phases/NN-{slug}/{N}-PLAN.md`
- Pattern: XML-structured file with <objective>, <context>, <tasks>, and <success_criteria>
- Example task: `<task type="auto"><name>...</name><files>...</files><action>...</action><verify>...</verify><done>...</done></task>`

**Summary:**
- Purpose: Record of what was executed; proves work completed
- Location: `.planning/phases/NN-{slug}/{N}-SUMMARY.md`
- Pattern: Markdown with key-files (created/modified), commits made, time spent, self-check results
- Proves: Files exist on disk, git commits made, tasks verified

**State:**
- Purpose: Project memory; survives session breaks
- Location: `.planning/STATE.md`
- Pattern: Markdown tracking: current position (phase/plan), blocking decisions, accumulated findings, error log
- Guarantees: Next `/gsd:resume-work` knows where to pick up, context not lost

## Entry Points

**Command Entry:**
- Location: `commands/gsd/{command}.md`
- Triggers: User types `/gsd:{command}` in Claude Code/OpenCode/Gemini CLI
- Responsibilities: Validate arguments, load config, initialize workflow, route to orchestrator

**Agent Spawn Entry:**
- Location: `agents/gsd-{role}.md`
- Triggers: Orchestrator calls Task(subagent_type="gsd-{role}", prompt=...)
- Responsibilities: Parse objective and context, read referenced files (@-paths), execute role-specific logic, return results

**Workflow Entry:**
- Location: `get-shit-done/workflows/{workflow}.md`
- Triggers: Command invokes workflow via reference (`@~/.claude/get-shit-done/workflows/{workflow}.md`)
- Responsibilities: Execute process steps sequentially, handle gates and branches, route errors, aggregate results

**Tool Entry:**
- Location: `get-shit-done/bin/gsd-tools.cjs`
- Triggers: `node ~/.claude/get-shit-done/bin/gsd-tools.cjs {command} {args}`
- Responsibilities: Query/mutate project state, compute derived data (waves, branches, file manifests), return JSON

## Error Handling

**Strategy:** Progressive disclosure with automated recovery paths

**Patterns:**

1. **Validation Errors** — Caught early in orchestrator
   - Missing required files (PROJECT.md, ROADMAP.md)
   - Invalid phase number (not found in roadmap)
   - Conflict (unfinished phase, broken state)
   - Response: Clear error message, suggest fix (run `/gsd:new-project`, `/gsd:health --repair`)

2. **Execution Errors** — Caught in executor agents
   - Task failed (file write failed, bash command failed)
   - Test verification failed (npm run build failed)
   - Response: Executor writes error to SUMMARY.md marked `## Self-Check: FAILED`, orchestrator routes to manual recovery or automated debug agent

3. **Verification Errors** — Caught by verifier/checker agents
   - Plan doesn't achieve phase goals
   - Code doesn't pass automated tests
   - Response: Verifier spawns gsd-debugger, debugger creates gap-closure plans, user re-executes

4. **State Corruption** — Detected by `/gsd:health`
   - Orphaned files, manifest mismatches
   - Response: `--repair` flag auto-fixes common issues (remove orphaned hooks, rebuild manifest)

5. **Session Break** — Handled by pause/resume
   - Incomplete phase, user stops work mid-execution
   - Response: `/gsd:pause-work` creates continue-here.md checkpoint, `/gsd:resume-work` reads checkpoint and resumes

## Cross-Cutting Concerns

**Logging:**
- Orchestrators print progress to stdout (wave completion, agent spawning, spot-checks)
- Agents write detailed output to markdown files (SUMMARY.md, RESEARCH.md)
- SUMMARY.md includes timing, files modified, git commits created
- No structured logging — human-readable markdown is the audit trail

**Validation:**
- Entry points validate arguments (phase number exists, command has required tools)
- Workflows validate state before proceeding (gate checks: confirm_project, confirm_phases, confirm_roadmap)
- Agents validate file writes (verify created files exist on disk before returning)
- Checkpoints validate human inputs (user approves/rejects before continuing)

**Authentication:**
- No user authentication — assumes single local user
- External integrations (APIs, services) use environment variables (stored in local .env, not committed)
- Secrets protection: GSD forbids committing files containing credentials; `.env` excluded from artifact scanning

**Parallelization:**
- Plans within a wave run in parallel (independent plans don't block each other)
- Waves run sequentially (wave 2 waits for wave 1 completion)
- Controlled by config.json: `parallelization.enabled`, `parallelization.max_concurrent_agents` (default 3)
- Dependency analysis groups plans into waves automatically (plans with same dependencies → same wave)

**Model Selection:**
- Three profiles: quality (Opus everywhere), balanced (Opus planner, Sonnet executor), budget (Sonnet planner, Haiku executor)
- Per-agent override: Agent definitions have no hardcoded model — orchestrator passes model via Task(model=...)
- Resolution: config.json profile → workflows compute model for each agent type → Task(model=resolved_model)

**Git Integration:**
- Every task gets atomic commit with prefix: `{phase_id}({timestamp}): {task_name}`
- Branching strategies: none (commit to current), phase (branch per phase), milestone (branch per milestone)
- Merges at phase/milestone completion: user chooses squash or merge-with-history
- Hooks: SessionStart hook runs `gsd-check-update.js` (soft update check, no interruption)

---

*Architecture analysis: 2026-02-16*
