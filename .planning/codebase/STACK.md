# Technology Stack

**Analysis Date:** 2026-02-16

## Languages

**Primary:**
- JavaScript (Node.js) - Core runtime for all tools, scripts, and hooks
- Markdown - All commands, agents, workflows, and templates use Markdown with YAML frontmatter

**Secondary:**
- TOML - Generated format for Gemini CLI command conversion
- JSON - Configuration files, package manifests, fixtures
- JSONC (JSON with Comments) - Supported for OpenCode configuration parsing

## Runtime

**Environment:**
- Node.js >= 16.7.0 (specified in package.json engines)
- Cross-platform: Mac, Windows, Linux

**Package Manager:**
- npm - Version not pinned
- Lockfile: `package-lock.json` present (v3)

## Frameworks

**Core:**
- None - Minimal dependencies philosophy (zero production dependencies)

**Build/Dev:**
- esbuild 0.24.x - For bundling hooks (copy-only, no actual bundling in current setup)

**Test:**
- Node.js built-in test runner - Native Node test framework used in `gsd-tools.test.cjs`

## Key Dependencies

**Production (Zero):**
- No production npm packages
- System relies on Node.js built-in modules only: `fs`, `path`, `os`, `readline`, `crypto`, `child_process`, `net`, `http`

**Development:**
- esbuild ^0.24.0 - Used for potential hook bundling via `npm run build:hooks`

## Configuration

**Environment:**
- `BRAVE_API_KEY` - Optional, enables Brave Search API integration in websearch command
- `CLAUDE_CONFIG_DIR` - Override Claude Code config directory (default: ~/.claude)
- `GEMINI_CONFIG_DIR` - Override Gemini CLI config directory (default: ~/.gemini)
- `OPENCODE_CONFIG_DIR` - Override OpenCode config directory (default: ~/.config/opencode)
- `XDG_CONFIG_HOME` - Used by OpenCode for XDG Base Directory spec compliance

**Build:**
- `scripts/build-hooks.js` - Copies pure Node.js hooks to `hooks/dist/` for distribution

**Files:**
- `.planning/config.json` - Project-level GSD configuration (parallelization, gates, safety settings)
- `settings.json` in runtime config directory - Runtime-specific hooks and statusline configuration
- `.gsd/brave_api_key` - Optional local fallback for Brave API key

## Platform Requirements

**Development:**
- Git repository (.git directory detection required)
- Node.js 16.7.0 or higher
- npm for package management and installation

**Installation Targets:**
- Claude Code: `~/.claude/` (global) or `./.claude/` (local)
- OpenCode: `~/.config/opencode/` (global, XDG-compliant) or `./.opencode/` (local)
- Gemini CLI: `~/.gemini/` (global) or `./.gemini/` (local)

**Production:**
- Supports multi-runtime deployment: Claude Code, OpenCode, Gemini CLI
- Installed as Node.js CLI tool via npm package `get-shit-done-cc`
- Execution targets: API tools (Read, Write, Edit, Bash, Glob, Grep), WebSearch, WebFetch, and MCP servers

## Architecture Notes

**Zero-Dependency Design:**
- All core functionality built with Node.js stdlib only
- Dependencies minimal: esbuild only for development/bundling
- Markdown + YAML frontmatter avoids need for specialized parsers

**Multi-Runtime Support:**
- Single codebase adapted to Claude Code, OpenCode, and Gemini CLI
- Tool name mapping layer: `claudeToOpencodeTools`, `claudeToGeminiTools` in install.js
- Frontmatter conversion: Claude format → OpenCode (tools object) or Gemini (array format)
- Separate hook paths for each runtime based on XDG specs and conventions

**Execution Model:**
- Commands, agents, and workflows are Markdown files with YAML frontmatter and role definitions
- Agents orchestrated by Claude Code/OpenCode/Gemini native agent systems
- CLI tools via `gsd-tools.cjs` handle state management, phase operations, git commits, and validation

---

*Stack analysis: 2026-02-16*
