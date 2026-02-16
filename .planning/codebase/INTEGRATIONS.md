# External Integrations

**Analysis Date:** 2026-02-16

## APIs & External Services

**Web Search:**
- Brave Search API - Optional web search capability for research phases
  - SDK/Client: Direct HTTP via Node.js `net`/`http`
  - Auth: `BRAVE_API_KEY` environment variable or `~/.gsd/brave_api_key` file
  - Implementation: `get-shit-done/bin/gsd-tools.cjs` - `cmdWebsearch()` function
  - Endpoint: `https://api.search.brave.com/res/v1/web/search`
  - Parameters: Query, limit (default 10), freshness (day/week/month)
  - Fallback: If key not configured, silently skips and agents fall back to built-in WebSearch tool
  - Usage: Called by gsd-phase-researcher, gsd-project-researcher for discovery and research

**WebFetch:**
- Built-in tool for fetching web content
- Implementation: Claude Code/OpenCode/Gemini native WebFetch tool
- Used in agents for documentation lookup, link validation

**WebSearch:**
- Built-in tool for web search
- Claude Code native: Uses Claude's built-in search
- OpenCode native: Uses WebSearch plugin/MCP
- Gemini: Uses `google_web_search` built-in tool
- Fallback when Brave API not available

## Model Lookup & AI Platform Integration

**Claude Code / OpenCode / Gemini:**
- Agents spawn subagents which execute plans
- Model resolution: `gsd-planner`, `gsd-executor`, `gsd-debugger`, etc. choose model from profile
- Config location: Runtime-specific settings.json manages model preferences
- No direct API calls - integrations are through native runtime tool systems

## Data Storage

**Local Filesystem Only:**
- `.planning/` directory: All project state, plans, summaries, verification docs
- `~/.claude/cache/` - Update check cache (gsd-update-check.json)
- `~/.claude/todos/` - Todo list files per session
- `~/.gsd/` - Optional user-level config (brave_api_key)

**No Database:**
- File-based state management
- .planning/config.json for project configuration
- STATE.md for project-wide state tracking
- ROADMAP.md for phase planning
- Phase-specific: PLAN.md, SUMMARY.md, VERIFICATION.md per phase directory

## Version Control Integration

**Git:**
- Detection: Checks for `.git` directory to determine if repo exists
- Commits: `gsd-tools.cjs` performs git commits via execSync
  - Commands: `git commit`, `git check-ignore`, `git branch`, `git log`
  - Used by: phase operations, documentation commits, summary commits
- .gitkeep files: Created in phase directories to track empty folders
- .gitignore detection: Respects `.gitignore` rules for codebase mapping searches
- Config: Branching strategy (if configured in .planning/config.json)

**Git-based Workflows:**
- Phase branch templates: `phase_branch_template`, `milestone_branch_template` in config
- Commit hash verification: Validates referenced commit hashes exist in history
- Planning docs auto-commit: Controlled by `planning.commit_docs` setting

## Package Management Integration

**npm:**
- Version checking: Queries `npm view get-shit-done-cc version` to detect updates
- Hook: `gsd-check-update.js` runs via SessionStart hook
- Cache: Results stored in `~/.claude/cache/gsd-update-check.json`
- Background process: Non-blocking update check spawned at session start

**Installation Targets:**
- Claude Code config: `~/.claude/` or project `./.claude/`
- OpenCode config: `~/.config/opencode/` or project `./.opencode/`
- Gemini config: `~/.gemini/` or project `./.gemini/`

## MCP Servers (Model Context Protocol)

**Context7 MCP:**
- Used for library/framework documentation lookup
- Agents using Context7: `gsd-phase-researcher`, `gsd-project-researcher`, `gsd-planner`
- Tools: `mcp__context7__resolve-library-id`, `mcp__context7__query-docs`
- Purpose: Current, authoritative documentation for technology choices and API lookups
- Fallback: If unavailable, agents use WebSearch + official docs as backup

**MCP Tool Handling:**
- Tools prefixed with `mcp__` are auto-discovered by Gemini CLI at runtime
- Not explicitly listed in Gemini tool manifests (filtered out during conversion)
- Claude Code and OpenCode pass through MCP tools as-is

## Hooks & System Integration

**SessionStart Hooks:**
- Location: `~/.claude/hooks/` (or runtime equivalent)
- `gsd-check-update.js` - Checks for GSD updates in background
- `gsd-statusline.js` - Renders statusline with model, task, and context usage

**Statusline Hook:**
- Input: JSON from Claude Code containing model info, context window, session ID
- Output: Formatted statusline string with:
  - Model name (from data.model.display_name)
  - Current task (from todos)
  - Working directory
  - Context usage percentage with color-coded bar
  - Update notification if new GSD version available
- File: `.claude/hooks/gsd-statusline.js`

**Update Check Hook:**
- Spawned as background process (detached)
- Non-blocking: Runs via `spawn(..., { stdio: 'ignore', detached: true })`
- Timeout: 10 seconds for npm version query
- Result cache: `~/.claude/cache/gsd-update-check.json`

## File Reference System

**Path Templates:**
- Claude Code: `~/.claude/` replaced with runtime path during installation
- OpenCode: `~/.config/opencode/` (XDG Base Directory spec)
- Local installs: `./.claude/`, `./.opencode/`, `./.gemini/`
- Referenced in agent files and commands, templated at install time

**Frontmatter Conversion:**
- Claude → OpenCode: tools array converted to tools object with boolean values
- Claude → Gemini: tools converted to YAML array format, color field removed
- Path references: Updated to match runtime directory structure
- Attribution (Co-Authored-By): Can be removed, kept, or customized per runtime

## Tool Name Mapping

**Claude Code → OpenCode:**
- Read → read
- Write → write
- Edit → replace (mapped in conversion)
- Bash → bash
- Glob → glob
- Grep → search_file_content (mapped)
- WebSearch → websearch
- WebFetch → webfetch
- AskUserQuestion → question (mapped)
- SlashCommand → skill (mapped)
- TodoWrite → todowrite (mapped)

**Claude Code → Gemini CLI:**
- Read → read_file (mapped)
- Write → write_file (mapped)
- Edit → replace (mapped)
- Bash → run_shell_command (mapped)
- Glob → glob
- Grep → search_file_content (mapped)
- WebSearch → google_web_search (mapped)
- WebFetch → web_fetch (mapped)
- TodoWrite → write_todos (mapped)
- AskUserQuestion → ask_user (mapped)
- MCP tools: Filtered out (auto-discovered at runtime)
- Task tool: Filtered out (agents auto-registered as tools)

## Permissions & Safety

**OpenCode Permissions:**
- Configured in `opencode.json` during install
- Permissions granted for read access to `get-shit-done/*` directory
- Prevents permission prompts when accessing GSD reference docs
- Automatically set up by `configureOpencodePermissions()` function

**Claude Code Settings:**
- Optional: `--dangerously-skip-permissions` flag for frictionless automation
- Recommended for GSD workflows to avoid approval prompts
- Explicit permissions in `.claude/settings.json` for granular control

## Environment Variables & Config Files

**Required (Optional - all have fallbacks):**
- `BRAVE_API_KEY` - For Brave Search API (optional, skipped if not set)

**Optional Locations:**
- `CLAUDE_CONFIG_DIR` - Custom Claude Code config directory
- `GEMINI_CONFIG_DIR` - Custom Gemini CLI config directory
- `OPENCODE_CONFIG_DIR` - Custom OpenCode config directory
- `XDG_CONFIG_HOME` - XDG Base Directory spec support

**Credentials Storage:**
- `.env` files: NOT used by GSD (projects may have their own)
- API keys: Environment variables or `~/.gsd/` directory
- Never committed: Keys stored outside `.planning/` directory

## External Command Dependencies

**Required System Commands:**
- `git` - For version control operations
- `npm` - For package version checking
- `node` - JavaScript runtime
- Standard Unix commands: `find`, `grep`, `date` (via Bash tool)

**Optional System Commands:**
- `curl` / `wget` - May be invoked by WebFetch tool (runtime-dependent)

---

*Integration audit: 2026-02-16*
