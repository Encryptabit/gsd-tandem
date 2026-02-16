# Coding Conventions

**Analysis Date:** 2026-02-16

## Naming Patterns

**Files:**
- Node.js scripts: lowercase with hyphens (`gsd-tools.cjs`, `gsd-check-update.js`, `gsd-statusline.js`)
- Test files: `.test.cjs` or `.spec.js` suffix (e.g., `gsd-tools.test.cjs`)
- Agent/command files: kebab-case prefixed with domain (`gsd-codebase-mapper.md`, `gsd-executor.md`)
- Phases/planning docs: UPPERCASE with semantic naming (`PLAN.md`, `SUMMARY.md`, `CONTEXT.md`, `VERIFICATION.md`)

**Functions:**
- camelCase for all function declarations: `parseJsonc()`, `expandTilde()`, `convertToolName()`, `buildHookCommand()`
- Helper functions with descriptive verb prefixes: `get*()`, `create*()`, `verify*()`, `clean*()`, `extract*()`
- Private/internal functions not distinguished by naming (no underscore prefix)

**Variables:**
- camelCase for local and module-level variables: `selectedRuntimes`, `tmpDir`, `settingsPath`, `frontmatter`
- Constants in UPPER_SNAKE_CASE: `PATCHES_DIR_NAME`, `MANIFEST_NAME`, `HOOKS_TO_COPY`, `MODEL_PROFILES`, `TOOLS_PATH`
- Color constants in camelCase: `cyan`, `green`, `yellow`, `dim`, `reset`

**Types/Objects:**
- Object keys use camelCase: `settingsPath`, `runtime`, `isGlobal`, `outputMode`, `command`
- Nested config keys preserve semantic meaning: `settings.hooks.SessionStart`, `config.permission.read`

## Code Style

**Formatting:**
- No explicit linter/formatter detected (no ESLint/Prettier config files)
- Semicolons used consistently throughout
- Indentation: 2 spaces (observed in all files)
- Line length: pragmatic, some lines exceed 100 characters in string templates and function calls

**Linting:**
- Not enforced by configuration
- Code follows conventional Node.js patterns (node:* imports)

## Import Organization

**Order:**
1. Node.js built-ins: `const fs = require('fs')`, `const path = require('path')`, `const { execSync } = require('child_process')`
2. External packages (rarely used, project has minimal dependencies)
3. Local modules/relative imports

**Example from `bin/install.js` (lines 3-7):**
```javascript
const fs = require('fs');
const path = require('path');
const os = require('os');
const readline = require('readline');
const crypto = require('crypto');
```

**Path Aliases:**
- Not used; all paths relative or absolute
- Paths templated at installation time for runtime compatibility (see `bin/install.js` path replacement logic)

## Error Handling

**Patterns:**
- Try-catch blocks wrap risky operations with silent fallback (graceful degradation):
  ```javascript
  try {
    return JSON.parse(fs.readFileSync(settingsPath, 'utf8'));
  } catch (e) {
    return {};  // Return empty object on parse failure
  }
  ```

- Process exit on critical errors (e.g., invalid arguments):
  ```javascript
  if (!nextArg || nextArg.startsWith('-')) {
    console.error(`  ${yellow}--config-dir requires a path argument${reset}`);
    process.exit(1);
  }
  ```

- Explicit error object inspection with fallback: `err.status ?? 1`, `err.stdout ?? ''`

- Silent failures for non-critical operations (statusline, async hooks):
  ```javascript
  child.unref();  // Fire and forget, no error handling
  ```

## Logging

**Framework:** `console` (built-in Node.js)

**Patterns:**
- Colored output using ANSI escape codes: `${cyan}text${reset}`
- Prefix-based severity: `${yellow}⚠${reset}` for warnings, `${green}✓${reset}` for success
- Dim/italic for secondary info: `${dim}path${reset}`
- All logs use `console.log()` or `console.error()` with formatted strings
- Output to statusline via `process.stdout.write()` for compact formatting
- Async hook output redirected to `/dev/null` to avoid background noise

**Example from `bin/install.js` (line 149):**
```javascript
console.error(`  ${yellow}--config-dir requires a path argument${reset}`);
```

## Comments

**When to Comment:**
- JSDoc for significant functions (documented approach to complex logic)
- Inline comments for non-obvious algorithm steps or state management
- Section dividers for logical grouping: `// ─── Model Profile Table ─────────────────────────`

**JSDoc/TSDoc:**
- Used for major functions with parameters and return documentation
- Example from `bin/install.js` (lines 50-54):
  ```javascript
  /**
   * Get the config directory path relative to home directory for a runtime
   * Used for templating hooks that use path.join(homeDir, '<configDir>', ...)
   * @param {string} runtime - 'claude', 'opencode', or 'gemini'
   * @param {boolean} isGlobal - Whether this is a global install
   */
  function getConfigDirFromHome(runtime, isGlobal) {
  ```

- Applied to complex YAML parsing and frontmatter manipulation functions

## Function Design

**Size:**
- Generally 20-80 lines for utility functions
- Complex logic (YAML parsing, frontmatter reconstruction) split into dedicated functions
- Install/uninstall workflows use longer functions (100+ lines) due to multi-step nature

**Parameters:**
- Positional parameters for required arguments
- Configuration objects for optional/multiple parameters
- Callback pattern for async operations (see `promptRuntime()`, `promptLocation()`)

**Return Values:**
- Explicit return values for simple operations: strings, objects, booleans
- Object return for multi-value results: `{ success: true, output: result, error: null }`
- Silent return/undefined for side-effect operations
- Error signals via `{ exitCode: 1, stderr: '...' }` object structure

## Module Design

**Exports:**
- No explicit exports; scripts are executable CLI entry points via shebang (`#!/usr/bin/env node`)
- Helper functions defined in same file as invocation logic
- No class-based architecture; functions composed for workflows

**Barrel Files:**
- Not used in this codebase

**File Organization:**
- `bin/install.js` — 1807 lines, monolithic install script with helper functions grouped by concern
- `get-shit-done/bin/gsd-tools.cjs` — ~5000 line tool with model profiles, helpers, and command dispatch
- `hooks/*.js` — Single-purpose scripts, ~60-90 lines each
- `scripts/build-hooks.js` — Simple build script, 42 lines

## Type Safety & Validation

**Pattern:** Runtime validation with early returns
- Check file existence before operations: `if (!fs.existsSync(path))`
- Validate YAML parsing with try-catch: `JSON.parse()` wrapped
- Validate frontmatter structure by presence of expected keys
- No TypeScript; duck typing with defensive checks

**Example from `gsd-tools.cjs` (lines 200-211):**
```javascript
try {
  // Load and validate config structure
  return {
    // ...validated fields
  };
} catch {
  return defaults;  // Fallback to safe defaults
}
```

## Async Patterns

**Callbacks:**
- Used for terminal prompts (readline interface): `rl.question(..., (answer) => { })`
- Used for event listeners: `process.stdin.on('data', chunk => ...)`

**Process Management:**
- Child process spawning with `spawn()` for fire-and-forget operations
- `execSync()` for synchronous operations with error handling
- No Promise/async-await (legacy Node.js pattern, maintains compatibility with older Node versions)

---

*Convention analysis: 2026-02-16*
