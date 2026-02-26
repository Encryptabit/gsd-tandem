# Codebase Concerns

**Analysis Date:** 2026-02-16

## Tech Debt

**Context Overflow Risk at 100+ Phases:**
- Issue: Milestone archival system was implemented to handle context overflow when projects exceed ~100 phases
- Files: `get-shit-done/templates/milestone-archive.md` (line 75), `get-shit-done/templates/phase-prompt.md` (line 249)
- Impact: Without careful phase aggregation into milestones, single-session context will be consumed entirely by phase history, degrading Claude's reasoning quality
- Fix approach: Enforce milestone archival after every 15-20 phases; implement automatic context size monitoring in workflows; consider secondary storage for historical phases beyond current milestone

**Quality Degradation Curve:**
- Issue: Context quality follows predictable degradation — plans using >50% context show efficiency mode, >70% shows rushed/minimal work
- Files: `agents/gsd-planner.md` (lines 74-83)
- Impact: Executor/planner quality drops as context window fills; plans become less thorough; assumptions go unverified
- Fix approach: Enforce strict 50% context budget per plan; add telemetry to track context usage; fail fast when approaching limits

**Large JSON Payload Truncation:**
- Issue: Discovered in v1.19.2 — large JSON payloads were being truncated in tool calls
- Files: `CHANGELOG.md` (line 57), `bin/install.js` (payload handling sections)
- Impact: Complex configurations, large requirement sets, or multi-phase summaries could be silently truncated, causing data loss
- Fix approach: Already partially fixed (temp files for JSON) — verify this applies to ALL agent-to-orchestrator handoffs; add validation to reject incomplete payloads

## Known Bugs & Issues

**Meta-Debugging: Internal Code Bias:**
- Problem: GSD agents (particularly executor and planner) are fighting their own mental models when debugging/planning their own workflows
- Files: `agents/gsd-debugger.md` (lines 42-57)
- Cause: Familiarity with design decisions makes bugs "feel obviously correct"; agents remember intent, not actual implementation
- Current mitigation: Debugging discipline documented in debugger agent; scientific method approach reduces confirmation bias
- Recommendation: Add unit tests for agent prompt parsing; verify output structure against schema before processing

**Stub Detection Gaps:**
- Problem: Incomplete implementations (placeholder components, empty functions, unwired data flows) are hard to catch in verification
- Files: `agents/gsd-verifier.md` (lines 488-537, "stub_detection_patterns"), `agents/gsd-executor.md` (line 707)
- Cause: Verifier checks existence but not "substantive implementation"; component can render without displaying real data
- Current mitigation: Goal-backward verification methodology; three-level checks (exists, substantive, wired); regex patterns for common stubs
- Recommendation: Require verification summary to include sample output from each artifact; enforce stricter "done" criteria that include data flow validation

**Incorrect Plan Claims Not Caught:**
- Problem: SUMMARY.md documents what Claude SAID it did, not what actually exists in code
- Files: `agents/gsd-verifier.md` (line 13), `agents/gsd-executor.md` (Task completion verification)
- Cause: Executors can mark tasks complete based on file creation (not actual functionality)
- Current mitigation: Verifier explicitly does NOT trust SUMMARY claims; re-verifies all artifacts against codebase
- Recommendation: Add executor checklist that requires running verification BEFORE committing; fail builds if verification would fail

**Placeholder Language Not Caught:**
- Problem: Comments like "TODO: implement", "FIXME: add fields", "will be implemented later" slip through to verified code
- Files: `agents/gsd-verifier.md` (line 285), `get-shit-done/references/verification-patterns.md` (line 533)
- Cause: Grepping for patterns catches regex matches but not semantic incompleteness
- Current mitigation: Verifier checks for placeholder patterns
- Recommendation: Make placeholder detection deterministic — fail verification if ANY file contains TODO/FIXME/PLACEHOLDER; require explicit removal before phase completion

## Security Considerations

**API Key Leak Prevention:**
- Risk: GSD agents (particularly codebase-mapper) could accidentally commit `.env` files or credentials via code analysis outputs
- Files: `agents/gsd-codebase-mapper.md` (lines 735-755, "forbidden_files" section), `CHANGELOG.md` (line 208 - CRITICAL fix in v1.11.1)
- Current mitigation: Strict forbidden_files list; agents explicitly forbidden from reading/quoting credential files
- Recommendations:
  - Add git hook that refuses commits containing API key patterns (sk-*, AKIA*, etc.)
  - Audit all `.env*` files are in `.gitignore`
  - Add pre-commit validation to gsd-tools.cjs to scan staged files for secret patterns

**Installation Script Vulnerability:**
- Risk: `bin/install.js` (1806 lines) handles file system operations, config directory creation, and hook template injection
- Files: `bin/install.js` (extensive filesystem operations without full validation)
- Potential issues:
  - Hardcoded path expansions could have edge cases on Windows/exotic paths
  - Attribution string substitution uses regex with `$$$$` escaping (line 280) — could have injection vectors
  - Config directory creation doesn't validate all paths are within expected hierarchy
- Current mitigation: Forward slashes used for cross-platform compatibility; explicit tilde expansion; attribution regex escaping
- Recommendations:
  - Add path validation to reject suspicious directory traversals (../../../ patterns)
  - Use `path.normalize()` to resolve all paths to canonical form
  - Test installation on Windows with paths containing spaces and special characters
  - Audit `processAttribution()` substitution to ensure injection isn't possible

**Missing Authorization Patterns:**
- Risk: Executor's Rule 2 (auto-fix missing critical functionality) includes "no auth on protected routes" but doesn't validate detection
- Files: `agents/gsd-executor.md` (lines 102-106)
- Current mitigation: Documented as explicit fix rule; executor should detect and fix
- Recommendations:
  - Add integration test checking executor correctly identifies missing auth
  - Create security template for common auth patterns (JWT, session, API keys)
  - Flag unprotected endpoints in verification as blocker, not warning

## Performance Bottlenecks

**Executor Auto-fix Loop:**
- Problem: Executor can attempt up to 3 auto-fixes per task before giving up (line 150); each fix rebuild/test cycle burns context
- Files: `agents/gsd-executor.md` (lines 149-154)
- Cause: Some tasks may genuinely need multiple iterations (e.g., dependency resolution, build tweaks)
- Impact: Slow task completion; wasted context on failed fix attempts
- Improvement path:
  - Add exponential backoff (fix 1 = quick, fix 2 = investigate, fix 3 = give up)
  - Log auto-fix attempts to metrics; alert if single task needs >1 fix consistently
  - Consider delegating to debugger after first fix fails instead of retrying locally

**Verifier Re-verification Optimization:**
- Problem: Verifier does full three-level checks even for previously-passed items on re-verification
- Files: `agents/gsd-verifier.md` (lines 44-46)
- Impact: Verification can take 2-3x longer on retry cycles
- Improvement path:
  - Cache passing verification results with content hash; only re-check if files changed
  - Skip regression checks for unchanged artifacts
  - Prioritize failed items for full 3-level verification; quick-check passing items

**Large Phase History Processing:**
- Problem: Planners load full history digest to select previous decisions; at 50+ phases this becomes expensive
- Files: `agents/gsd-planner.md`, `agents/gsd-project-researcher.md`
- Impact: Planning time increases linearly with phase count
- Improvement path:
  - Implement LRU cache of last N phase summaries (10-15 phases)
  - Use gsd-tools `history-digest` to return structured JSON instead of full content
  - Consider tiered history: recent (full detail), mid-range (summary), old (omitted unless referenced)

## Fragile Areas

**Executor Scope Boundary Enforcement:**
- Files: `agents/gsd-executor.md` (lines 143-154)
- Why fragile: Executor must distinguish between "issues caused by current task" vs "pre-existing failures in unrelated files"
- Current approach: Log out-of-scope to `deferred-items.md`; don't fix pre-existing issues
- Risk: If executor misclassifies, either (A) pre-existing issues get fixed — hiding actual problems, or (B) task-related failures marked as out-of-scope — blocking progress
- Safe modification:
  - Add clear logging of what's in-scope vs out-of-scope before starting each task
  - Require executor to explain reasoning for out-of-scope classification
  - Have plan-checker review scope boundary definitions

**Stub-vs-Feature Detection:**
- Files: `agents/gsd-verifier.md` (lines 19, 488-537)
- Why fragile: Task completion and goal achievement are different. A file can exist and be "complete" while being functionally useless (placeholder component)
- Current approach: Three-level verification (exists, substantive, wired); regex patterns for stubs
- Risk: Heuristics catch common cases but miss domain-specific stubs (e.g., "component renders but state doesn't persist")
- Safe modification:
  - Require each verification to include sample execution output (not just "component exists")
  - Add checklist in done criteria: "User can see [behavior]" not just "file created"
  - Have auditor review phase summary before marking complete

**Context Fidelity Enforcement:**
- Files: `agents/gsd-planner.md` (lines 28-55), `agents/gsd-plan-checker.md` (lines 21-38)
- Why fragile: Planner MUST honor user decisions from CONTEXT.md (locked decisions are NON-NEGOTIABLE); but nothing prevents planner from reinterpreting or ignoring
- Current approach: Self-check before returning; plan-checker verifies compliance
- Risk: If planner misses a locked decision, executor builds wrong thing; two agents verify but it's still interpretation-dependent
- Safe modification:
  - Add boolean dimension to plan-checker: "All locked decisions present in plans?" (FAIL if not)
  - Require planner to explicitly map each locked decision to task(s)
  - Add automated check comparing CONTEXT.md decisions to PLAN frontmatter

**Auto-Mode Runaway Prevention:**
- Files: `agents/gsd-executor.md` (lines 171-178), `CHANGELOG.md` (line 29, v1.20.1)
- Why fragile: Auto-mode can chain multiple phases without human intervention; `auto_advance` config persists across context resets
- Current approach: Auto-advance clears on milestone complete; checkpoints still work as gates
- Risk: Config corruption or setting misunderstanding could enable infinite loop
- Safe modification:
  - Add confirmation prompt before entering auto mode
  - Log each phase transition when auto-mode active
  - Implement watchdog: if >5 phases execute in <1 hour with no manual intervention, alert user

## Scaling Limits

**Phase Numbering Scalability:**
- Current capacity: Decimal phase system (1, 1.1, 1.2, 2, 2.1, etc.) works to insert phases without full renumbering
- Limit: After ~50-100 decimal phases, renumbering for milestone completion becomes complex
- Scaling path: Implement three-tier numbering (milestone.phase.revision) or switch to semantic versioning (v1.5.2)

**Requirement Tracking Across Phases:**
- Current capacity: REQUIREMENTS.md can track requirements across unlimited phases, but correlation to plans is manual
- Limit: At 30+ phases, orphaned requirements (in REQUIREMENTS.md but unclaimed by any plan) become hard to detect manually
- Scaling path: Automated orphan detection (added in v1.20.2); consider requirement tiering (critical vs nice-to-have)

**Markdown File Size:**
- Current capacity: Single ROADMAP.md works to ~100+ phases
- Limit: Beyond 100 phases, loading and parsing single large markdown file causes performance issues
- Scaling path: Already addressed with milestone archival; could further split by introducing per-milestone roadmap files

## Dependencies at Risk

**Training Data Staleness:**
- Risk: Claude's training data is 6-18 months stale; agents may recommend obsolete libraries, patterns, or APIs
- Impact: Researchers can confidently recommend deprecated packages; newer, better alternatives exist but aren't known
- Files: `agents/gsd-phase-researcher.md` (lines 54-56), `agents/gsd-project-researcher.md` (line 30)
- Current mitigation: Agents treat pre-existing knowledge as hypothesis; Brave Search integration for researcher (requires BRAVE_API_KEY)
- Recommendations:
  - Make knowledge staleness explicit in research output ("as of Feb 2025, this is current")
  - Require researchers to validate recommendations against official docs (via WebFetch)
  - Add deprecation warning system for known stale patterns

**CommonJS Issues with Edge Runtime:**
- Risk: Using `jsonwebtoken` package causes CommonJS conflicts with Edge runtime (Next.js App Router)
- Files: `agents/gsd-planner.md` (line 139), `README.md` (line 399)
- Impact: Projects using Edge runtime will fail at runtime if executor uses jsonwebtoken
- Current mitigation: Pattern documented; planner instructed to use `jose` instead
- Recommendations:
  - Add automatic detection: if Next.js 13+ App Router detected, enforce jose in auth plans
  - Add test for every auth implementation to verify compatible with both Node and Edge
  - Create template showing correct ESM-compatible JWT patterns

**OpenCode/Gemini Format Conversion Risks:**
- Risk: `bin/install.js` converts Claude Code format to OpenCode/Gemini, but format drift could break conversions
- Files: `bin/install.js` (lines 309-348, tool mappings), `CHANGELOG.md` (v1.9.6 and later)
- Impact: If OpenCode/Gemini tool names change, installer silently generates invalid configs
- Current mitigation: Explicit tool mapping dictionaries; attribution handling for all runtimes
- Recommendations:
  - Add schema validation that tool names match runtime specifications
  - Test all three runtime conversions in CI pipeline
  - Create fallback: if tool not found in mapping, warn and preserve original name

## Missing Critical Features

**Deterministic Plan Quality Measurement:**
- Problem: Plan checker flags quality issues (too many tasks, too much context) but no objective scoring
- Impact: Plans with "3 complex tasks using 60% context" get same approval as "2 simple tasks using 40%"
- Blocks: Can't automatically reject low-quality plans or enforce standards across team
- Recommendation: Implement plan quality score combining: task count, context usage, dependency complexity, scope clarity

**Audit Trail for Decision Changes:**
- Problem: CONTEXT.md decisions can be modified by user, but no history of what changed or why
- Impact: Executor might implement old decision; no way to trace decision evolution
- Blocks: Can't answer "why did we choose this library?" without re-reading discussion history
- Recommendation: Add immutable decision log with timestamps; require approval for decision reversals

**Cross-Requirement Dependencies:**
- Problem: REQUIREMENTS.md tracks requirements per phase, but can't express "REQ-05 depends on REQ-02"
- Impact: Plans might try to implement blocking requirements before their prerequisites
- Blocks: Complex projects with deep requirement dependencies can't model dependencies explicitly
- Recommendation: Add requirement dependency graph similar to plan dependencies

**Verification Simulation Mode:**
- Problem: Verifier can only check completed code; no way to validate verification strategy before execution
- Impact: After 5 tasks fail verification, realize verification criteria were wrong (wasted context)
- Blocks: Can't iterate on verification approach without burning execution context
- Recommendation: Add dry-run mode where planner/checker can validate verification approach on sample code

## Test Coverage Gaps

**Executor Deviation Rule Auto-fix:**
- Untested area: Executor's three deviation rules (Rules 1-3) are complex; unclear how often they trigger or if they work correctly
- Files: `agents/gsd-executor.md` (lines 92-114)
- Risk: If auto-fix doesn't work as expected, executor could silently produce broken code or miss critical fixes
- Priority: **HIGH** — affects every executed plan
- Recommendation: Create test projects with intentional bugs/missing features; verify executor applies correct rules

**Plan Checker Dimension Coverage:**
- Untested area: Plan checker has 6+ verification dimensions; coverage of real-world plan defects is unknown
- Files: `agents/gsd-plan-checker.md` (verification_dimensions section, ~200 lines)
- Risk: Checker might miss common plan issues; plans fail during execution that should have been caught
- Priority: **HIGH** — plan-checker is critical gate before execution
- Recommendation: Catalog real plan failures and verify each would be caught by plan-checker

**Verifier Goal-Backward Logic:**
- Untested area: Verifier's three-level verification (truths, artifacts, key_links) is sophisticated; no test suite validates it catches stubs correctly
- Files: `agents/gsd-verifier.md` (lines 63-150)
- Risk: Stubs slip through to verified code; goals marked complete while implementation is placeholder
- Priority: **CRITICAL** — if verifier misses stubs, whole system's guarantee (goal achievement) fails
- Recommendation: Create comprehensive stub detection test suite with intentional placeholder implementations

**Auto-Mode State Persistence:**
- Untested area: Auto-mode config survives context compaction by persisting to disk; complex serialization/deserialization logic
- Files: `agents/gsd-executor.md` (lines 171-178), `CHANGELOG.md` (v1.20.1)
- Risk: Config corruption could enable auto-mode runaway or disable auto-mode unexpectedly
- Priority: **MEDIUM** — only affects users using auto-mode flag
- Recommendation: Add config validation on load; round-trip test (write, reload, verify unchanged)

---

*Concerns audit: 2026-02-16*
