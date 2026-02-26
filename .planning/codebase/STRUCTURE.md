# Codebase Structure

**Analysis Date:** 2026-02-16

## Directory Layout

```
gsd-tandem/
├── agents/                          # Agent definitions spawned on-demand
│   ├── gsd-planner.md              # Creates executable phase plans
│   ├── gsd-executor.md             # Implements tasks from plans
│   ├── gsd-phase-researcher.md     # Researches domain before planning
│   ├── gsd-verifier.md             # Verifies phase goals achieved
│   ├── gsd-debugger.md             # Diagnoses execution failures
│   ├── gsd-plan-checker.md         # Validates plans against goals
│   ├── gsd-project-researcher.md   # Researches new project domains
│   ├── gsd-roadmapper.md           # Creates project roadmap
│   ├── gsd-research-synthesizer.md # Synthesizes research findings
│   ├── gsd-integration-checker.md  # Validates external integrations
│   └── gsd-phase-researcher.md     # Domain research for phases
│
├── commands/gsd/                    # User-facing slash commands (30+)
│   ├── new-project.md              # Initialize project with research & roadmap
│   ├── plan-phase.md               # Plan a phase with research & verification
│   ├── execute-phase.md            # Execute all plans in parallel waves
│   ├── discuss-phase.md            # Capture design decisions before planning
│   ├── verify-work.md              # Manual UAT and verification
│   ├── debug.md                    # Systematic debugging with state tracking
│   ├── quick.md                    # Execute ad-hoc task without full planning
│   ├── add-phase.md                # Append phase to roadmap
│   ├── insert-phase.md             # Insert urgent work between phases
│   ├── remove-phase.md             # Remove future phase
│   ├── audit-milestone.md          # Verify milestone achieved goals
│   ├── complete-milestone.md       # Archive milestone, tag release
│   ├── new-milestone.md            # Start next version
│   ├── progress.md                 # Show current progress & next steps
│   ├── pause-work.md               # Create handoff checkpoint
│   ├── resume-work.md              # Restore from checkpoint
│   ├── map-codebase.md             # Analyze existing codebase
│   ├── health.md                   # Validate .planning/ integrity
│   ├── help.md                     # Show all commands
│   ├── update.md                   # Update GSD with changelog
│   ├── settings.md                 # Configure workflow and models
│   ├── set-profile.md              # Switch model profile
│   ├── add-todo.md                 # Capture idea for later
│   ├── check-todos.md              # List pending todos
│   ├── list-phase-assumptions.md   # Show Claude's intended approach
│   ├── plan-milestone-gaps.md      # Create phases to close gaps
│   ├── cleanup.md                  # Clean up abandoned checkpoints
│   ├── reapply-patches.md          # Merge locally modified GSD files
│   └── join-discord.md             # Community link
│
├── get-shit-done/                   # GSD system (the engine)
│   ├── bin/
│   │   ├── gsd-tools.cjs           # CLI utility for state queries (Node.js)
│   │   ├── gsd-tools.test.js       # Tests for gsd-tools
│   │   └── VERSION                 # Version marker
│   │
│   ├── workflows/                   # Process definitions (30+ workflows)
│   │   ├── new-project.md          # Full initialization flow
│   │   ├── plan-phase.md           # Research → Plan → Verify loop
│   │   ├── execute-phase.md        # Wave-based parallel execution
│   │   ├── execute-plan.md         # Single plan execution (for executors)
│   │   ├── discuss-phase.md        # Capture design decisions
│   │   ├── verify-work.md          # UAT and verification loop
│   │   ├── diagnose-issues.md      # Debug workflow
│   │   ├── discovery-phase.md      # Initial domain exploration
│   │   ├── pause-work.md           # Create handoff
│   │   ├── resume-work.md          # Restore from handoff
│   │   ├── audit-milestone.md      # Verification of milestone completion
│   │   ├── complete-milestone.md   # Finalize & tag release
│   │   ├── new-milestone.md        # Start next version
│   │   └── [19+ other workflows]
│   │
│   ├── templates/                   # Artifact templates
│   │   ├── PROJECT.md              # Project definition template
│   │   ├── REQUIREMENTS.md         # Requirements template
│   │   ├── ROADMAP.md              # Roadmap template
│   │   ├── CONTEXT.md              # Phase context template (from discuss-phase)
│   │   ├── PLAN.md                 # Executable task plan template
│   │   ├── SUMMARY.md              # Execution summary template
│   │   ├── DEBUG.md                # Debug session template
│   │   ├── config.json             # Default config template
│   │   ├── continue-here.md        # Session checkpoint template
│   │   ├── discovery.md            # Discovery session template
│   │   ├── phase-prompt.md         # Phase execution prompt template
│   │   ├── codebase/
│   │   │   ├── architecture.md     # ARCHITECTURE.md template
│   │   │   ├── structure.md        # STRUCTURE.md template
│   │   │   ├── stack.md            # STACK.md template
│   │   │   ├── integrations.md     # INTEGRATIONS.md template
│   │   │   ├── conventions.md      # CONVENTIONS.md template
│   │   │   ├── testing.md          # TESTING.md template
│   │   │   └── concerns.md         # CONCERNS.md template
│   │   └── [other templates]
│   │
│   └── references/                  # Decision frameworks & patterns
│       ├── questioning.md           # How to ask users effectively
│       ├── model-profiles.md        # Model selection (quality/balanced/budget)
│       ├── model-profile-resolution.md # How to compute model per agent
│       ├── checkpoints.md           # Human verification patterns
│       ├── verification-patterns.md # What to verify, how
│       ├── tdd.md                   # Test-driven development in GSD
│       ├── git-integration.md       # Git workflow patterns
│       ├── git-planning-commit.md   # Commit message format
│       ├── phase-argument-parsing.md # How to parse phase arguments
│       ├── decimal-phase-calculation.md # Sub-phase numbering logic
│       ├── ui-brand.md              # UI/UX brand guidelines
│       ├── planning-config.md       # Config schema and behavior
│       ├── continuation-format.md   # Multi-session context format
│       ├── milestone-archive.md     # Milestone archiving structure
│       └── [other references]
│
├── bin/
│   └── install.js                  # NPM postinstall script
│                                    # (deploys GSD to ~/.claude, ~/.config/opencode, ~/.gemini)
│
├── hooks/
│   ├── src/                         # TypeScript source for hooks
│   │   ├── gsd-statusline.ts       # Status bar integration
│   │   ├── gsd-check-update.ts     # Soft update check
│   │   └── [other hooks]
│   ├── dist/                        # Compiled hooks (bundled with npm package)
│   │   ├── gsd-statusline.js
│   │   └── gsd-check-update.js
│   └── [build artifacts]
│
├── scripts/
│   └── build-hooks.js              # Builds TypeScript hooks to dist/
│
├── assets/
│   └── terminal.svg                # Terminal screenshot for README
│
├── docs/
│   └── USER-GUIDE.md               # Configuration reference & troubleshooting
│
├── .github/
│   ├── workflows/                  # CI/CD
│   │   └── auto-label-issues.yml
│   ├── ISSUE_TEMPLATE/
│   │   ├── bug_report.yml
│   │   └── feature_request.yml
│   └── pull_request_template.md
│
├── .planning/                       # Project state (created by `/gsd:new-project`)
│   ├── codebase/                    # Codebase analysis (from `/gsd:map-codebase`)
│   │   ├── STACK.md                # Technology stack analysis
│   │   ├── INTEGRATIONS.md         # External services & APIs
│   │   ├── ARCHITECTURE.md         # Conceptual architecture
│   │   ├── STRUCTURE.md            # Physical structure & layout
│   │   ├── CONVENTIONS.md          # Code conventions
│   │   ├── TESTING.md              # Testing patterns
│   │   └── CONCERNS.md             # Technical debt & issues
│   ├── config.json                 # Workflow configuration (modes, models, gates)
│   ├── PROJECT.md                  # Project definition & vision
│   ├── REQUIREMENTS.md             # Scoped requirements (v1, v2, out-of-scope)
│   ├── ROADMAP.md                  # Phase structure & timeline
│   ├── STATE.md                    # Current position & accumulated state
│   ├── research/                   # Domain research (populated by researchers)
│   │   ├── stack.md                # Technology/ecosystem findings
│   │   ├── features.md             # Similar products & features
│   │   ├── architecture.md         # How to structure this type of project
│   │   └── pitfalls.md             # Common mistakes & gotchas
│   ├── phases/                      # Phase planning & execution
│   │   ├── 01-auth/                # Phase 1: Authentication
│   │   │   ├── 01-CONTEXT.md       # Design decisions from /gsd:discuss-phase
│   │   │   ├── 01-RESEARCH.md      # Research findings
│   │   │   ├── 01-01-PLAN.md       # First plan (2-3 tasks)
│   │   │   ├── 01-01-SUMMARY.md    # Execution summary
│   │   │   ├── 01-02-PLAN.md       # Second plan
│   │   │   ├── 01-02-SUMMARY.md
│   │   │   ├── 01-VERIFICATION.md  # Verification results
│   │   │   └── 01-UAT.md           # User acceptance testing
│   │   ├── 02-dashboard/
│   │   └── [more phases]
│   ├── quick/                       # Ad-hoc tasks (from `/gsd:quick`)
│   │   ├── 001-dark-mode-toggle/
│   │   │   ├── PLAN.md
│   │   │   └── SUMMARY.md
│   │   └── [other quick tasks]
│   ├── debug/                       # Debug sessions (from `/gsd:debug`)
│   │   ├── session-20250216-001/
│   │   │   ├── DEBUG.md
│   │   │   └── findings.md
│   │   └── [other debug sessions]
│   └── todos/                       # Captured ideas (from `/gsd:add-todo`)
│       └── captured-todos.md
│
├── package.json                     # npm metadata (no dependencies, just bin script)
├── package-lock.json
├── README.md                        # Main documentation
├── CHANGELOG.md                     # Version history
├── LICENSE                          # MIT
└── SECURITY.md                      # Security guidelines
```

## Directory Purposes

**`agents/`:**
- Purpose: Specialized agent definitions for different workflow roles
- Contains: 11 YAML-frontmatter markdown files defining agents (gsd-planner, gsd-executor, etc.)
- Key files: Each agent has role, tools, and detailed instructions
- Pattern: When orchestrator calls `Task(subagent_type="gsd-planner", ...)`, Claude loads the corresponding agent definition

**`commands/gsd/`:**
- Purpose: User-facing entry points (slash commands)
- Contains: 30+ command definitions (`.md` files)
- Key files: `new-project.md`, `plan-phase.md`, `execute-phase.md` (the core workflow commands)
- Pattern: Each command has YAML frontmatter (name, allowed-tools, argument-hint) + body with execution flow

**`get-shit-done/bin/`:**
- Purpose: Node.js utilities for state management and tool access
- Contains: `gsd-tools.cjs` (main CLI tool), test file, VERSION file
- Key operations: init (load all context), roadmap queries, phase-plan-index (wave grouping), state-snapshot (accumulated decisions)
- Called by: All workflows via `node ~/.claude/get-shit-done/bin/gsd-tools.cjs <command>`

**`get-shit-done/workflows/`:**
- Purpose: Procedural definitions of complex orchestration flows
- Contains: 30+ workflow files (one per command)
- Key files: `new-project.md` (initialize), `plan-phase.md` (research → plan → verify), `execute-phase.md` (wave-based execution)
- Pattern: Each workflow has step definitions with bash calls, Task spawns, conditional gates

**`get-shit-done/templates/`:**
- Purpose: Template artifacts that agents write to
- Contains: `.md` templates (PROJECT.md, REQUIREMENTS.md, PLAN.md, SUMMARY.md) and codebase analysis templates
- Key files: `codebase/` subdirectory with STACK.md, ARCHITECTURE.md, CONVENTIONS.md, etc. templates
- Usage: Agents copy template, fill in sections, write to `.planning/`

**`get-shit-done/references/`:**
- Purpose: Decision frameworks and pattern guides
- Contains: `.md` files with question flows, model selection logic, verification patterns, git workflows
- Key files: `questioning.md` (how to ask users), `checkpoints.md` (verification patterns), `model-profiles.md` (model selection)
- Usage: Agents reference during execution for consistent decision-making

**`bin/`:**
- Purpose: Installation and setup
- Contains: `install.js` (NPM postinstall script)
- Key operations: Deploy GSD to `~/.claude/`, `~/.config/opencode/`, `~/.gemini/`; Configure runtime settings; Handle upgrading

**`hooks/`:**
- Purpose: Runtime integration (status bar, update checks)
- Contains: TypeScript source (`src/`) and compiled JavaScript (`dist/`)
- Key files: `gsd-statusline.ts` (shows model, task, context %), `gsd-check-update.ts` (soft version check)
- Usage: Compiled hooks deployed to `~/.claude/hooks/` and registered in settings.json

**`.planning/`:**
- Purpose: Project state (created on first `/gsd:new-project`)
- Contains: Config, roadmap, requirements, phase planning, research, execution summaries
- Key files: `config.json` (workflow preferences), `PROJECT.md` (vision), `ROADMAP.md` (phases), `STATE.md` (position)
- Committed: Yes — entire `.planning/` directory tracked in git for full project history

## Key File Locations

**Entry Points:**

| File | Purpose |
|------|---------|
| `commands/gsd/*.md` | User slash commands (e.g., `/gsd:new-project`) |
| `agents/gsd-*.md` | Specialist agents (spawned on-demand) |
| `get-shit-done/workflows/*.md` | Orchestration flows |
| `bin/install.js` | NPM deployment |

**Configuration:**

| File | Purpose |
|------|---------|
| `.planning/config.json` | Workflow modes, model profiles, gates, parallelization |
| `get-shit-done/templates/config.json` | Default config template |
| `package.json` | NPM metadata |

**Core Logic:**

| File | Purpose |
|------|---------|
| `get-shit-done/bin/gsd-tools.cjs` | State queries, tool access |
| `get-shit-done/workflows/plan-phase.md` | Planning orchestration |
| `get-shit-done/workflows/execute-phase.md` | Execution orchestration |

**Testing:**

| File | Purpose |
|------|---------|
| `get-shit-done/bin/gsd-tools.test.js` | Unit tests for gsd-tools |

**Project State (created by `/gsd:new-project`):**

| File | Purpose |
|------|---------|
| `.planning/PROJECT.md` | Project vision and context |
| `.planning/REQUIREMENTS.md` | Scoped requirements (v1, v2, out-of-scope) |
| `.planning/ROADMAP.md` | Phase breakdown and timeline |
| `.planning/STATE.md` | Current position, decisions, blockers |
| `.planning/codebase/` | Analysis docs (STACK.md, ARCHITECTURE.md, etc.) |
| `.planning/phases/*/PLAN.md` | Executable task plans |
| `.planning/phases/*-SUMMARY.md` | Execution results |

## Naming Conventions

**Files:**

| Pattern | Example | Usage |
|---------|---------|-------|
| `{command}.md` | `new-project.md` | Command definitions in `commands/gsd/` |
| `gsd-{role}.md` | `gsd-planner.md` | Agent definitions in `agents/` |
| `{workflow}.md` | `plan-phase.md` | Workflow definitions in `get-shit-done/workflows/` |
| `{NN}-{slug}/` | `01-auth/` | Phase directories in `.planning/phases/` |
| `{NN}-PLAN.md` | `01-01-PLAN.md` | Plan file (phase 1, plan 1) |
| `{NN}-SUMMARY.md` | `01-01-SUMMARY.md` | Summary file (phase 1, plan 1) |
| `{NN}-CONTEXT.md` | `01-CONTEXT.md` | Design context (phase 1) |
| `{NN}-RESEARCH.md` | `01-RESEARCH.md` | Research findings (phase 1) |
| `{NN}-VERIFICATION.md` | `01-VERIFICATION.md` | Verification results (phase 1) |
| `{NN}-UAT.md` | `01-UAT.md` | User acceptance test (phase 1) |

**Directories:**

| Pattern | Example | Usage |
|---------|---------|-------|
| `{NN}-{slug}` | `01-auth` | Phase directory (padded number + kebab-case slug) |
| `quick/{NNN}-{slug}` | `quick/001-dark-mode` | Quick task directory |
| `debug/session-{date}-{num}` | `debug/session-20250216-001` | Debug session directory |
| `research/` | `.planning/research/` | Domain research output |
| `codebase/` | `.planning/codebase/` | Codebase analysis output |
| `todos/` | `.planning/todos/` | Captured ideas |

## Where to Add New Code

**New Agent:**
- Create: `agents/gsd-{role}.md`
- Template: YAML frontmatter (name, description, allowed-tools, color) + XML-tagged role and instructions
- Register: Referenced in workflows via `Task(subagent_type="gsd-{role}", ...)`

**New Command:**
- Create: `commands/gsd/{command-name}.md`
- Template: YAML frontmatter (name, description, argument-hint, allowed-tools) + objective/execution_context/process
- Reference workflow: `@~/.claude/get-shit-done/workflows/{command-name}.md`

**New Workflow:**
- Create: `get-shit-done/workflows/{workflow-name}.md`
- Template: XML tags for purpose/required_reading/process with step definitions
- Call from command: Include via execution_context

**New Reference Doc:**
- Create: `get-shit-done/references/{topic}.md`
- Usage: Agents reference via `@~/.claude/get-shit-done/references/{topic}.md` in their instructions

**New Template:**
- Create: `get-shit-done/templates/{artifact}.md`
- Usage: Agents copy template, fill placeholders, write to `.planning/{destination}`

**Templates for Codebase Analysis:**
- Create: `get-shit-done/templates/codebase/{topic}.md`
- Invoked by: `/gsd:map-codebase` (spawns analysis agents)

## Special Directories

**`.planning/`:**
- Purpose: Project state and workflow artifacts
- Generated: Yes (created by `/gsd:new-project`)
- Committed: Yes (entire directory tracked in git)
- Ignored: No (must be committed for full project history)

**`.planning/phases/`:**
- Purpose: Phase planning and execution artifacts
- Generated: Populated by `/gsd:plan-phase` (creates PLAN.md) and `/gsd:execute-phase` (creates SUMMARY.md)
- Committed: Yes (all plans and summaries tracked)
- Structure: One directory per phase: `{NN}-{slug}/`

**`.planning/quick/`:**
- Purpose: Ad-hoc task execution (from `/gsd:quick`)
- Generated: Yes (when using quick mode)
- Committed: Yes (for audit trail)
- Structure: One directory per quick task: `{NNN}-{slug}/`

**`.planning/debug/`:**
- Purpose: Debug session artifacts (from `/gsd:debug`)
- Generated: Yes (when using debug mode)
- Committed: Yes (for debugging history)
- Structure: One directory per session: `session-{date}-{num}/`

**`.planning/research/`:**
- Purpose: Domain research from initial project setup
- Generated: Yes (populated by phase researchers)
- Committed: Yes (reference for future phases)
- Files: `stack.md`, `features.md`, `architecture.md`, `pitfalls.md`

**`.planning/codebase/`:**
- Purpose: Existing codebase analysis (from `/gsd:map-codebase`)
- Generated: Yes (created on brownfield projects)
- Committed: Yes (loaded by planner/executor for context)
- Files: `STACK.md`, `INTEGRATIONS.md`, `ARCHITECTURE.md`, `STRUCTURE.md`, `CONVENTIONS.md`, `TESTING.md`, `CONCERNS.md`

**`hooks/dist/`:**
- Purpose: Compiled hooks bundled with npm package
- Generated: Yes (from TypeScript source in `hooks/src/`)
- Committed: Yes (distributed with npm package)
- Build: `npm run build:hooks` → outputs to `dist/`

**`get-shit-done/CHANGELOG.md`:**
- Purpose: Version history (copied to `.claude/get-shit-done/CHANGELOG.md` during install)
- Generated: No (maintained manually)
- Committed: Yes

---

*Structure analysis: 2026-02-16*
