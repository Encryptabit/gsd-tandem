# Testing Patterns

**Analysis Date:** 2026-02-16

## Test Framework

**Runner:**
- Node.js built-in test runner (`node:test` module)
- No external framework dependencies (Jest, Vitest not used)
- Config: None required; uses Node.js native test discovery

**Assertion Library:**
- Node.js built-in `node:assert` module with `assert.strictEqual()`, `assert.deepStrictEqual()`, `assert.ok()`

**Run Commands:**
```bash
npm test                    # Run all tests (runs gsd-tools.test.cjs via package.json)
node --test <file>          # Run individual test file directly
```

**Explicit test script in `package.json` (line 46):**
```json
"test": "node --test get-shit-done/bin/gsd-tools.test.cjs"
```

## Test File Organization

**Location:**
- Co-located with implementation: `get-shit-done/bin/gsd-tools.test.cjs` alongside `gsd-tools.cjs`
- Single comprehensive test file (2,346 lines) covering CLI operations

**Naming:**
- Pattern: `[module].test.cjs` or `.spec.js` (test file uses `.test.cjs` to match source)
- Tests discovered and run by Node.js test runner automatically

**Structure:**
```
get-shit-done/bin/
├── gsd-tools.cjs           # CLI implementation
└── gsd-tools.test.cjs      # Comprehensive test suite (2,346 lines)
```

## Test Structure

**Suite Organization:**
```javascript
const { test, describe, beforeEach, afterEach } = require('node:test');
const assert = require('node:assert');

describe('history-digest command', () => {
  let tmpDir;

  beforeEach(() => {
    tmpDir = createTempProject();
  });

  afterEach(() => {
    cleanup(tmpDir);
  });

  test('empty phases directory returns valid schema', () => {
    // Arrange
    const result = runGsdTools('history-digest', tmpDir);

    // Assert
    assert.ok(result.success, `Command failed: ${result.error}`);
    const digest = JSON.parse(result.output);
    assert.deepStrictEqual(digest.phases, {}, 'phases should be empty object');
  });
});
```

**Patterns:**
- Setup: `beforeEach()` creates temp directories for isolated test execution
- Teardown: `afterEach()` removes temp directories via `fs.rmSync(tmpDir, { recursive: true })`
- Arrange-Act-Assert: Tests follow AAA pattern with clear phases
- Assertion messages: Descriptive failure messages to aid debugging

## Mocking

**Framework:** File system mocking via temporary directories

**Patterns:**
```javascript
// Create temp project structure
function createTempProject() {
  const tmpDir = fs.mkdtempSync(path.join(require('os').tmpdir(), 'gsd-test-'));
  fs.mkdirSync(path.join(tmpDir, '.planning', 'phases'), { recursive: true });
  return tmpDir;
}

// Use in test
beforeEach(() => {
  tmpDir = createTempProject();
});

// Run command against temp directory
const result = runGsdTools('history-digest', tmpDir);
```

**Helper Function:**
```javascript
function runGsdTools(args, cwd = process.cwd()) {
  try {
    const result = execSync(`node "${TOOLS_PATH}" ${args}`, {
      cwd,
      encoding: 'utf-8',
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    return { success: true, output: result.trim() };
  } catch (err) {
    return {
      success: false,
      output: err.stdout?.toString().trim() || '',
      error: err.stderr?.toString().trim() || err.message,
    };
  }
}
```

**What to Mock:**
- File system: Use `fs.mkdirSync()`, `fs.writeFileSync()` to create temp structures
- Child processes: Use `execSync()` to run CLI commands as subprocesses
- Environment: Create isolated temp directories with fixture content

**What NOT to Mock:**
- Core Node.js modules (fs, path, os) — use real implementations for integration testing
- CLI execution — run actual command to test full behavior including exit codes

## Fixtures and Factories

**Test Data:**
```javascript
// Fixture: YAML frontmatter with nested structure
const summaryContent = `---
phase: "01"
name: "Foundation Setup"
dependency-graph:
  provides:
    - "Database schema"
    - "Auth system"
  affects:
    - "API layer"
tech-stack:
  added:
    - "prisma"
    - "jose"
patterns-established:
  - "Repository pattern"
  - "JWT auth flow"
key-decisions:
  - "Use Prisma over Drizzle"
  - "JWT in httpOnly cookies"
---

# Summary content here
`;

fs.writeFileSync(path.join(phaseDir, '01-01-SUMMARY.md'), summaryContent);
```

**Location:**
- Inline in test functions (no separate fixture directory)
- Fixture content created in memory for each test
- Temp directories provide isolation

## Coverage

**Requirements:** Not enforced; no coverage configuration detected

**View Coverage:**
- Node.js test runner does not report coverage by default
- Coverage could be added with `--coverage` flag (Node.js 20.3+) but not currently used

## Test Types

**Unit Tests:**
- Scope: Individual CLI commands (e.g., `history-digest`, `phases list`)
- Approach: Isolate command logic by passing controlled input, verify output structure
- Example: Test that `history-digest` correctly parses nested YAML frontmatter

**Integration Tests:**
- Scope: Full CLI workflows with file system state
- Approach: Create realistic phase directory structures, run commands end-to-end, verify file system changes
- Example: Test that `phase add <description>` creates phase directory, updates ROADMAP, returns correct numbering

**E2E Tests:**
- Not present; CLI tests are effectively E2E since they spawn actual subprocesses

## Common Patterns

**Async Testing:**
```javascript
// CLI commands are synchronous via execSync - no async patterns needed
// However, child processes may complete in background
const result = runGsdTools('history-digest', tmpDir);  // Synchronous
assert.ok(result.success);
```

**Error Testing:**
```javascript
test('malformed SUMMARY.md skipped gracefully', () => {
  const phaseDir = path.join(tmpDir, '.planning', 'phases', '01-test');
  fs.mkdirSync(phaseDir, { recursive: true });

  // Create malformed YAML (broken array)
  fs.writeFileSync(
    path.join(phaseDir, '01-02-SUMMARY.md'),
    `---
broken: [unclosed
---
`
  );

  const result = runGsdTools('history-digest', tmpDir);

  // Command should succeed despite malformed files
  assert.ok(result.success, `Command should succeed despite malformed files: ${result.error}`);

  // Digest should contain valid data from other files
  const digest = JSON.parse(result.output);
  assert.ok(digest.phases['01'], 'Phase 01 should exist');
});
```

**Backward Compatibility Testing:**
```javascript
test('flat provides field still works (backward compatibility)', () => {
  // Test both new nested structure and old flat structure
  fs.writeFileSync(
    path.join(phaseDir, '01-01-SUMMARY.md'),
    `---
phase: "01"
provides:
  - "Direct provides"
---
`
  );

  const result = runGsdTools('history-digest', tmpDir);
  const digest = JSON.parse(result.output);

  assert.deepStrictEqual(
    digest.phases['01'].provides,
    ['Direct provides'],
    'Direct provides should work (backward compatibility)'
  );
});
```

**Test Organization by Feature:**
- `describe('history-digest command')` — Tests for history aggregation
- `describe('phases list command')` — Tests for phase listing and filtering
- Each `describe()` block has its own `beforeEach()` and `afterEach()` for isolation

## Test Execution Environment

**Node Version:**
- `package.json` specifies `"engines": { "node": ">=16.7.0" }`
- Tests use Node.js native test runner (available since v18.0.0)
- Tests use `execSync()` for subprocess execution

**Isolated Execution:**
- Each test creates new temp directory via `fs.mkdtempSync()`
- Each test cleans up via `fs.rmSync()` in `afterEach()`
- No test interdependencies or shared state

## Test Coverage & Gaps

**Tested Areas:**
- YAML frontmatter parsing with nested structures (provides, affects, tech-stack, patterns-established, key-decisions)
- Backward compatibility for flat array syntax vs. nested
- Inline array syntax: `provides: [Feature A, Feature B]`
- Multiple phases aggregation and merge behavior
- Error recovery for malformed SUMMARY.md files
- Phase list sorting (numeric and decimal phases)
- Phase type filtering (PLAN files, SUMMARY files, etc.)

**Notable Test Patterns:**
- Tests verify JSON output format, not intermediate state
- Tests check error messages and exit codes via `result.success` flag
- Tests validate data type conversions (YAML → JavaScript objects)

---

*Testing analysis: 2026-02-16*
