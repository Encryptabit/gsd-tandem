# Pitfalls Research

**Domain:** MCP Review Broker (local agent-to-agent review via FastMCP + SQLite)
**Researched:** 2026-02-16
**Confidence:** HIGH (verified against official docs, GitHub issues, and SQLite documentation)

## Critical Pitfalls

### Pitfall 1: SQLite DEFERRED Transaction Deadlock (busy_timeout Ignored)

**What goes wrong:**
Two processes (Claude Code agent and reviewer) both open DEFERRED read transactions, then attempt to upgrade to write transactions. SQLite returns `SQLITE_BUSY` immediately -- the `busy_timeout` setting is completely ignored because the upgrade violates serializability guarantees. Both sides get "database is locked" errors with no retry, even though `busy_timeout` is set to 5000ms.

**Why it happens:**
`BEGIN` (without IMMEDIATE) starts a deferred transaction. SQLite initially treats it as read-only. When a write statement appears later, SQLite tries to upgrade the lock. If another connection has already modified the database or holds a write lock, the upgrade is rejected instantly -- no waiting, no retry, no timeout. This is by design, not a bug.

**How to avoid:**
- Use `BEGIN IMMEDIATE` for every transaction that might write. This acquires the write lock upfront, before any reads, so `busy_timeout` works correctly.
- Set `PRAGMA busy_timeout=5000` (minimum) on every new connection -- it is a per-connection setting.
- Set `PRAGMA journal_mode=WAL` on every new connection (WAL mode is per-database but must be set before first use).
- Keep write transactions as short as possible -- single-statement writes where feasible.
- Consider an application-level write mutex (Python `asyncio.Lock`) wrapping all database writes to serialize them before they reach SQLite.

**Warning signs:**
- Intermittent `SQLITE_BUSY` errors that appear under load but not in single-process testing.
- Errors that occur despite having `busy_timeout` configured.
- Errors that only surface when both the proposer and reviewer poll/write within the same narrow time window.

**Phase to address:**
Phase 1 (Core Broker) -- database layer must use `BEGIN IMMEDIATE` from day one. Retrofitting this after building on DEFERRED transactions requires rewriting every transaction path.

---

### Pitfall 2: Claude Code MCP Tool Timeout Kills Blocking Review Waits

**What goes wrong:**
Claude Code's MCP client enforces a tool call timeout (default 60 seconds, configurable via `MCP_TIMEOUT`). If the broker's `await_verdict` tool blocks while waiting for a human or slow reviewer to respond, Claude Code kills the tool call with `MCP error -32001: Request timed out` or `Connection closed (-32000)`. The proposal is stranded -- Claude thinks it failed, the broker still has a pending review.

**Why it happens:**
The MCP protocol has a request-response model with timeouts. Claude Code's TypeScript MCP client does not reset the timeout based on progress notifications (as of early 2026). A blocking poll that waits 5 minutes for a human reviewer will always exceed the 60-second default. Even setting `MCP_TIMEOUT=300000` is fragile because it affects all MCP tools globally.

**How to avoid:**
- Never block in an MCP tool call for longer than 20-30 seconds. Instead, implement a poll-and-return pattern: `await_verdict` polls the database, returns immediately with status `"pending"` or `"approved"`, and Claude's checkpoint logic loops with short sleeps between calls.
- Set `MCP_TIMEOUT` to at least 60000 (60s) in project documentation/setup.
- Return progress information in each poll response (e.g., "waiting since 45s ago, reviewer connected at 12s ago") so Claude can make informed retry decisions.
- Document that `MCP_TOOL_TIMEOUT` (for tool execution) and `MCP_TIMEOUT` (for connection startup) are separate environment variables with different purposes.

**Warning signs:**
- Tool calls that succeed in testing (fast reviewer) but fail in real usage (slow human reviewer).
- `MCP error -32001` appearing in Claude Code output.
- Orphaned reviews in the database with no verdict.

**Phase to address:**
Phase 1 (Core Broker) -- the wait strategy must be non-blocking from the initial design. A blocking `await_verdict` tool is the single most likely cause of a full rewrite.

---

### Pitfall 3: MCP Tool Response Truncation Destroys Large Diffs

**What goes wrong:**
Claude Code enforces a 25,000-token default limit on MCP tool responses (`MAX_MCP_OUTPUT_TOKENS`). A unified diff for a large file or a multi-file change can easily exceed this. The response is silently truncated, meaning Claude receives a partial diff, applies it incorrectly, or fails to understand the full change. Additionally, there is a display-level truncation at ~700 characters that affects terminal rendering.

**Why it happens:**
The MCP client in Claude Code has both a token-level limit (configurable via `MAX_MCP_OUTPUT_TOKENS`) and a display buffer limit (Node.js `stdout maxBuffer`). Large tool responses hit one or both. This is a known issue (GitHub issue #2638) closed as "not planned" -- the expectation is that MCP servers paginate or summarize.

**How to avoid:**
- Paginate diff responses: return a summary (files changed, lines added/removed) with the first N hunks, and provide a `get_diff_page(review_id, page)` tool for fetching more.
- Store full diffs in the SQLite database; return only metadata + first page via the MCP tool.
- Set `MAX_MCP_OUTPUT_TOKENS=50000` in project setup documentation as a safety margin.
- For the reviewer-side, if using Codex or another MCP client, test its own response size limits independently.
- Consider compressing diff representation: return only the hunks that changed, not full file context.

**Warning signs:**
- Diffs that "work" in testing with small files but fail on real codebases.
- Claude applying partial patches or reporting confusion about incomplete diffs.
- Warning messages in Claude Code terminal about token limits.

**Phase to address:**
Phase 2 (Diff/Patch Protocol) -- must design the diff transport format with pagination from the start. Retrofitting pagination onto a "return the whole diff" API is painful.

---

### Pitfall 4: FastMCP Host Binding Gotcha on Streamable HTTP

**What goes wrong:**
The `--host` CLI flag does not work with `streamable-http` transport in FastMCP. Running `fastmcp run server.py --transport streamable-http --host 127.0.0.1` raises `FastMCP.run() got an unexpected keyword argument 'host'`. The server binds to the default host (often `0.0.0.0`) instead of localhost, potentially exposing the broker to the network.

**Why it happens:**
FastMCP's CLI argument parsing for `streamable-http` transport does not pass through the `host` parameter the same way SSE transport does. This was documented in GitHub issue #873 and resolved in v2.8.1+, but only for programmatic usage -- not the CLI runner in all versions.

**How to avoid:**
- Set host programmatically in the server script: `mcp = FastMCP("gsd-review-broker", host="127.0.0.1", port=3100)` followed by `mcp.run(transport="streamable-http")`.
- Alternatively, set the `FASTMCP_HOST=127.0.0.1` environment variable.
- Never use the `fastmcp run` CLI for production -- always use `python server.py` directly.
- Pin FastMCP version to a known-working release (2.8.1+ for this fix).
- Add a startup check that verifies the server is only listening on 127.0.0.1.

**Warning signs:**
- Server starts without error but binds to `0.0.0.0` (check with `netstat` or `ss`).
- Other machines on the network can reach the broker.
- Error messages about unexpected keyword arguments during startup.

**Phase to address:**
Phase 1 (Core Broker) -- server startup configuration. Must be correct from the first working prototype.

---

### Pitfall 5: Mutual Wait Deadlock Between Proposer and Reviewer

**What goes wrong:**
Claude (proposer) submits a proposal and blocks waiting for a verdict. The reviewer queries for pending reviews but the query fails or returns empty due to a race condition (proposal not yet committed to SQLite). The reviewer has nothing to review. Claude is waiting. The reviewer is waiting. Neither side progresses. The human watching sees both sides idle.

**Why it happens:**
This is a classic distributed deadlock through an intermediary. Specific causes:
- SQLite write not yet committed when reviewer polls (timing window).
- Reviewer's polling interval is longer than expected (e.g., 30s), so there is a long gap between proposal submission and reviewer discovery.
- The broker has no mechanism to notify the reviewer that a new proposal arrived.
- Network or process issues cause one side's connection to the broker to silently fail.

**How to avoid:**
- Implement proposal state machine with explicit states: `CREATED -> CLAIMED -> IN_REVIEW -> VERDICT_SUBMITTED -> CLOSED`.
- Add a TTL (time-to-live) on proposals: if no reviewer claims within N seconds, the proposer gets a `timeout` response and can proceed with a fallback strategy.
- Log all state transitions with timestamps so the human observer can identify where the pipeline stalled.
- The broker should track reviewer liveness: if no reviewer has polled in M seconds, return "no reviewer available" immediately rather than making Claude wait.
- Consider adding a `broker_status` tool that returns: pending proposals count, last reviewer poll time, active connections.

**Warning signs:**
- Both agents idle simultaneously with no errors.
- Proposals accumulating in the database with `CREATED` status but never transitioning to `CLAIMED`.
- Long gaps between proposal creation timestamp and claim timestamp.

**Phase to address:**
Phase 1 (Core Broker) -- the state machine and TTL must be part of the initial protocol design. Phase 3 (Observability) adds the human-visible dashboard.

---

### Pitfall 6: Windows File Locking Holds SQLite Database After Connection Close

**What goes wrong:**
On Windows, SQLite's WAL mode holds file locks on the `.db` file beyond the Python `connection.close()` call. The `-wal` and `-shm` auxiliary files may also remain locked. This prevents the broker from cleanly restarting, causes "database is locked" errors on reopening, and can prevent `.planning/` cleanup.

**Why it happens:**
Windows file locking semantics differ from POSIX. On POSIX, closing a file descriptor releases all locks immediately. On Windows, the OS may hold file handles longer, especially when memory-mapped I/O is involved (which WAL mode uses for the shared-memory `-shm` file). Antivirus software (Windows Defender) can also hold open handles on recently-written files, compounding the problem.

**How to avoid:**
- Run `PRAGMA wal_checkpoint(TRUNCATE)` before closing the connection to flush the WAL and release shared memory.
- Set `PRAGMA journal_mode=WAL` early and avoid switching modes mid-session.
- Add a small delay (1-2 seconds) after `connection.close()` before attempting to reopen or delete the database file on Windows.
- Disable memory-mapped I/O if problems persist: `PRAGMA mmap_size=0`.
- Test the full open-use-close-reopen cycle on Windows specifically, not just macOS/Linux.
- Consider adding Windows Defender exclusion for `.planning/` in setup documentation.

**Warning signs:**
- "database is locked" on broker restart (not during normal operation).
- `.db-wal` and `.db-shm` files that persist after all Python processes exit.
- File deletion failures during `.planning/` cleanup on Windows.

**Phase to address:**
Phase 1 (Core Broker) -- database open/close lifecycle. Phase 4 (Cross-Platform Testing) must verify on actual Windows.

---

### Pitfall 7: Python `difflib.unified_diff` Produces Invalid Patches

**What goes wrong:**
Python's built-in `difflib.unified_diff` does not emit the `\ No newline at end of file` marker that standard patch tools (GNU `patch`, `git apply`) require. If a file lacks a trailing newline, the generated diff is technically malformed and cannot be applied by standard tools. Additionally, `difflib` can produce diffs that confuse line counts in hunk headers when dealing with certain edge cases.

**Why it happens:**
This is a known, long-standing Python bug (CPython issue #2142, reported in 2008, never fixed). The Python documentation acknowledges it and suggests users handle it themselves. Most developers discover this only when their diffs fail to apply cleanly.

**How to avoid:**
- Post-process `difflib.unified_diff` output: for any line not ending in `\n`, append `\n` and then `\ No newline at end of file\n`.
- Alternatively, use the `unidiff` library for parsing and validation, and `python-patch-ng` for application.
- Better yet, delegate diff generation to `git diff` via subprocess where available -- it handles all edge cases correctly.
- Always validate generated diffs by attempting to apply them in a dry run before sending to the reviewer.
- Normalize all file content to end with a newline before diffing.

**Warning signs:**
- Diffs that look correct visually but fail when applied.
- Patch application errors mentioning "hunk failed" or "malformed patch" for files without trailing newlines.
- Inconsistencies between what the reviewer sees and what gets applied.

**Phase to address:**
Phase 2 (Diff/Patch Protocol) -- diff generation and validation pipeline. Must include automated round-trip tests (generate diff -> apply diff -> verify result matches).

---

### Pitfall 8: MCP Configuration Confusion Between Claude Code and Claude Desktop

**What goes wrong:**
Developers configure MCP servers in `~/.claude/settings.json` expecting them to work in Claude Code, but Claude Code ignores that file. Or they use `claude mcp add` which stores config internally and is not visible in the file system. The broker appears "connected" in logs but tools are not exposed to the assistant. Hours of debugging follow.

**Why it happens:**
Claude Code and Claude Desktop have completely different MCP configuration mechanisms despite similar names. Claude Code uses `.mcp.json` at the project root (or `claude mcp add` via CLI). Claude Desktop uses `claude_desktop_config.json`. The official documentation has historically been incorrect or ambiguous about this distinction.

**How to avoid:**
- Use project-level `.mcp.json` for the broker configuration -- this is the canonical method for Claude Code.
- Provide a setup script that runs `claude mcp add gsdreview --transport streamable-http --url http://127.0.0.1:3100/mcp` and verifies the tool appears.
- Include a `verify-mcp-connection` command or tool that the user can run to confirm the broker is reachable and tools are listed.
- Document the exact `.mcp.json` format in the project README with copy-paste examples.
- Test configuration on a clean machine (no pre-existing MCP config).

**Warning signs:**
- Broker server logs show connections but Claude Code does not list `mcp__gsdreview__*` tools.
- `claude mcp list` does not show the broker server.
- Tools work in Claude Desktop but not Claude Code (or vice versa).

**Phase to address:**
Phase 1 (Core Broker) -- setup and configuration. Must be validated before any other integration testing.

## Technical Debt Patterns

| Shortcut | Immediate Benefit | Long-term Cost | When Acceptable |
|----------|-------------------|----------------|-----------------|
| Single SQLite connection shared across async handlers | Simpler code, no connection pooling | Serializes all database access, bottleneck under load | Never -- use connection-per-request with `BEGIN IMMEDIATE` from day one |
| Returning full diff content in every tool response | Simple implementation, no pagination | Hits Claude Code's 25K token limit on real codebases | Only during Phase 1 prototyping with small test files |
| Hardcoded polling interval (e.g., `time.sleep(2)`) | Quick to implement | Wastes resources when idle, too slow when busy | Only for initial prototype; replace with adaptive polling in Phase 2 |
| Using `shell=True` in subprocess calls for git | Easier command construction | Security risk, breaks on Windows (different shell syntax) | Never -- always use `shell=False` with argument lists |
| Storing diffs as plain text blobs in SQLite | Simple schema, easy to query | No structured access to individual hunks, hard to paginate | Acceptable for v1 if combined with metadata columns (file count, total lines) |
| Skipping diff validation on receipt | Faster proposal creation | Malformed diffs discovered only at apply time, wasting review effort | Never -- validate on creation, reject malformed diffs immediately |

## Integration Gotchas

| Integration | Common Mistake | Correct Approach |
|-------------|----------------|------------------|
| Claude Code MCP Client | Assuming tool calls can block for minutes while waiting for review | Implement poll-and-return pattern; never block longer than 20s in a tool call |
| Claude Code MCP Client | Using `fastmcp run` CLI instead of `python server.py` | Launch server via `python -m gsd_review_broker.server` for reliable host/port binding |
| Claude Code MCP Client | Not setting `MCP_TIMEOUT` environment variable | Document `MCP_TIMEOUT=120000` in setup; add to `.env.example` |
| Claude Code Tool Naming | Tool names exceeding 64 characters | Keep tool names concise: `create_proposal`, `get_verdict`, not `create_review_proposal_with_unified_diff_and_metadata` |
| SQLite on Windows | Using network paths or OneDrive-synced directories for `.planning/` | Verify `.planning/` is on a local filesystem; add startup check |
| FastMCP Streamable HTTP | Assuming SSE transport config works for streamable-http | Test with `streamable-http` transport specifically; host binding, session management, and headers differ |
| Reviewer MCP Client | Assuming reviewer's MCP client has same capabilities as Claude Code | Test with actual reviewer client (Codex, etc.) early; token limits, timeout behavior, and tool discovery may differ |
| Git Subprocess | Using `git diff` with `shell=True` on Windows | Use `subprocess.run(["git", "diff", ...], shell=False, text=True, encoding="utf-8")` |

## Performance Traps

| Trap | Symptoms | Prevention | When It Breaks |
|------|----------|------------|----------------|
| Polling SQLite every 500ms from both proposer and reviewer | High CPU usage when idle, WAL file grows, laptop fans spin | Adaptive polling: start at 500ms, back off to 5s when idle, snap back on activity | Immediately visible with 2+ agents polling simultaneously |
| Storing full file content (before + after) in proposals instead of diffs | Database grows to hundreds of MB, queries slow down | Store only the unified diff; reconstruct full content from working tree + diff when needed | After ~50 proposals with large files |
| Not checkpointing WAL file | `-wal` file grows unbounded, reads slow as WAL grows | Set `PRAGMA wal_autocheckpoint=100` (default 1000 may be too high for small databases); manual checkpoint on clean shutdown | After ~1000 writes without checkpoint |
| Serializing all tool calls through a single async handler | Tool calls queue behind slow operations (especially `await_verdict` polls) | Use separate async handlers per tool; database operations should not block tool discovery/listing | When reviewer and proposer make concurrent tool calls |
| Loading all proposals into memory for status queries | Works fine with 10 proposals, OOM or slow with 10,000 | Paginate queries with `LIMIT/OFFSET`; archive completed proposals | After extended project with many review cycles |

## Security Mistakes

| Mistake | Risk | Prevention |
|---------|------|------------|
| Binding broker to `0.0.0.0` instead of `127.0.0.1` | Any machine on the network can submit proposals or verdicts, potentially injecting malicious diffs | Hardcode `host="127.0.0.1"` in server; add startup assertion that verifies binding |
| Applying reviewer-supplied patches without validation | Malicious or malformed patch could write to files outside the project directory (path traversal via `../`) | Validate all file paths in diffs are relative and within the project root; reject absolute paths and `../` traversal |
| Storing sensitive file content (`.env`, credentials) in proposal diffs | Review database becomes a credential store visible to anyone with file access | Filter proposals: refuse to create diffs for files matching `.gitignore` patterns; warn on known sensitive filenames |
| No rate limiting on proposal creation | A runaway agent loop could flood the database with thousands of proposals | Add per-minute rate limit on `create_proposal`; alert if more than N proposals per minute |
| Executing reviewer-supplied patches via `subprocess` shell commands | Shell injection if patch content contains shell metacharacters | Never pass diff content through a shell; use Python's file I/O and `patch` libraries directly |

## UX Pitfalls

| Pitfall | User Impact | Better Approach |
|---------|-------------|-----------------|
| No visibility into why Claude is "stuck" waiting | User sees Claude idle, does not know a review is pending, assumes it crashed | Add a `broker_status` tool that returns queue depth, pending reviews, last activity timestamp |
| Reviewer sees raw unified diff without file context | Reviewer cannot understand the change without seeing surrounding code | Include N lines of context in diffs (default: 3); provide file path and line numbers prominently |
| No way to skip/auto-approve low-risk changes | Every whitespace fix requires full review cycle, slowing workflow to a crawl | Support review granularity: `per-task` (default), `per-plan` (batch), `auto-approve` (skip for specified patterns) |
| Error messages expose internal state (SQL queries, stack traces) | User/reviewer sees confusing technical errors instead of actionable messages | Wrap all tool responses in a consistent envelope: `{status, message, data}` with human-readable messages |
| No way to cancel a pending review | Proposal submitted but user realizes it is wrong; no mechanism to withdraw | Add `cancel_proposal` tool; cancelled proposals return `CANCELLED` status to the waiting agent |
| Observer cannot intervene without being the reviewer | Human watches proposals flow but cannot comment or override without claiming the reviewer role | Add an `intervene` tool that lets the observer inject a comment or force-approve/reject without being the primary reviewer |

## "Looks Done But Isn't" Checklist

- [ ] **SQLite WAL setup:** Often missing `PRAGMA busy_timeout` on new connections -- verify every connection path sets all three PRAGMAs (journal_mode, busy_timeout, synchronous)
- [ ] **Cross-platform paths:** Often missing Windows backslash handling in diff file paths -- verify diffs use forward slashes consistently and normalize on read
- [ ] **MCP tool registration:** Often missing tool description/schema -- verify every tool has a docstring and typed parameters (FastMCP uses these for Claude's tool discovery)
- [ ] **Connection lifecycle:** Often missing graceful shutdown -- verify the broker handles SIGINT/SIGTERM, checkpoints WAL, and closes all connections cleanly
- [ ] **Error propagation:** Often missing error details in tool responses -- verify that database errors, timeout errors, and validation errors all return structured error responses, not bare exceptions
- [ ] **Diff encoding:** Often missing encoding specification -- verify all diff operations use UTF-8 explicitly (`encoding="utf-8"` in every `open()` call and subprocess invocation)
- [ ] **Reviewer disconnect:** Often missing liveness tracking -- verify the broker detects when a reviewer has stopped polling and reports "no reviewer" rather than queueing indefinitely
- [ ] **Windows line endings:** Often missing CRLF handling -- verify diffs normalize line endings before comparison (files may have `\r\n` on Windows but `\n` in git)
- [ ] **Tool call idempotency:** Often missing retry safety -- verify that calling `create_proposal` twice with the same content does not create duplicate proposals (use content hash as dedup key)
- [ ] **Database path creation:** Often missing parent directory creation -- verify the broker creates `.planning/` if it does not exist before opening SQLite

## Recovery Strategies

| Pitfall | Recovery Cost | Recovery Steps |
|---------|---------------|----------------|
| DEFERRED transaction deadlocks | MEDIUM | Change all `BEGIN` to `BEGIN IMMEDIATE`; grep codebase for bare `BEGIN`; add linting rule |
| MCP timeout kills pending review | LOW | Implement poll-and-return pattern; set `MCP_TIMEOUT=120000`; orphaned proposals auto-expire via TTL |
| Truncated diff in tool response | MEDIUM | Add pagination to diff responses; increase `MAX_MCP_OUTPUT_TOKENS`; store full diff in DB, return summary |
| FastMCP host binding to 0.0.0.0 | LOW | Change to programmatic host setting; add startup assertion; restart server |
| Mutual wait deadlock | MEDIUM | Add TTL to proposals; add reviewer liveness check; add `broker_status` diagnostic tool |
| Windows file locking on restart | LOW | Add `PRAGMA wal_checkpoint(TRUNCATE)` before close; add post-close delay; document manual `-wal`/`-shm` deletion |
| Invalid diffs from `difflib` | HIGH | Replace `difflib` with `git diff` subprocess or add comprehensive post-processing; retest all existing diff generation |
| MCP configuration wrong file | LOW | Provide `.mcp.json` template; add `verify-connection` tool; document exact setup steps |
| Runaway proposal flood | MEDIUM | Add rate limiting; add `cancel_all_pending` admin tool; manual database cleanup via SQLite CLI |

## Pitfall-to-Phase Mapping

| Pitfall | Prevention Phase | Verification |
|---------|------------------|--------------|
| SQLite DEFERRED deadlock | Phase 1: Core Broker | Integration test: two concurrent writers with `BEGIN IMMEDIATE`; zero SQLITE_BUSY errors |
| MCP tool timeout | Phase 1: Core Broker | Test `await_verdict` with 120s simulated reviewer delay; no timeout errors |
| Tool response truncation | Phase 2: Diff/Patch Protocol | Test diff response for 500-line file change; response fits within 25K tokens |
| FastMCP host binding | Phase 1: Core Broker | Startup test: `netstat` confirms only 127.0.0.1 binding |
| Mutual wait deadlock | Phase 1: Core Broker | Integration test: proposal with no reviewer; TTL expires; proposer gets timeout response |
| Windows file locking | Phase 1: Core Broker + Phase 4: Cross-Platform | Windows CI: open-write-close-reopen cycle passes; no file lock errors |
| Invalid `difflib` patches | Phase 2: Diff/Patch Protocol | Round-trip test: generate diff -> apply diff -> compare result to expected; files with/without trailing newlines |
| MCP configuration confusion | Phase 1: Core Broker | Setup test: fresh clone, follow README, `claude mcp list` shows broker |
| Polling resource waste | Phase 2: Wait Strategy | Monitor CPU usage during 5-minute idle period; below 2% CPU baseline |
| Path traversal in patches | Phase 2: Diff/Patch Protocol | Security test: patch with `../../../etc/passwd` path is rejected |
| Cross-platform subprocess | Phase 4: Cross-Platform | CI on Windows + macOS: all `git diff` subprocess calls succeed |
| Reviewer liveness detection | Phase 3: Observability | Test: reviewer stops polling for 60s; `broker_status` reports "no active reviewer" |
| Line ending normalization | Phase 2: Diff/Patch Protocol | Test: file with CRLF endings on Windows produces valid diff; diff applies cleanly |

## Sources

- [SQLite WAL Documentation](https://sqlite.org/wal.html) -- official WAL mode reference
- [SQLite File Locking and Concurrency](https://sqlite.org/lockingv3.html) -- official locking documentation
- [SQLite busy_timeout Ignored for Transaction Upgrades](https://berthub.eu/articles/posts/a-brief-post-on-sqlite3-database-locked-despite-timeout/) -- MEDIUM confidence, verified against SQLite docs
- [SQLite Concurrent Writes and "database is locked" Errors](https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/) -- MEDIUM confidence, comprehensive analysis with benchmarks
- [FastMCP Host Option Issue #873](https://github.com/jlowin/fastmcp/issues/873) -- HIGH confidence, official GitHub issue
- [FastMCP Running Your Server](https://gofastmcp.com/deployment/running-server) -- HIGH confidence, official documentation
- [Claude Code MCP Documentation](https://code.claude.com/docs/en/mcp) -- HIGH confidence, official Anthropic docs
- [Claude Code MCP Timeout Issue #424](https://github.com/anthropics/claude-code/issues/424) -- HIGH confidence, official GitHub issue
- [Claude Code Truncated MCP Responses Issue #2638](https://github.com/anthropics/claude-code/issues/2638) -- HIGH confidence, official GitHub issue
- [Claude Code MCP Configuration Bug](https://www.petegypps.uk/blog/claude-code-mcp-configuration-bug-documentation-error-november-2025) -- MEDIUM confidence, third-party blog verified against official docs
- [Claude Code Tool Name Length Issue #2579](https://github.com/anthropics/claude-code/issues/2579) -- HIGH confidence, official GitHub issue
- [Python difflib.unified_diff Invalid Patches (CPython Issue #2142/46395)](https://github.com/python/cpython/issues/46395) -- HIGH confidence, official CPython bug tracker
- [SQLite WAL File Locking on Windows (Bun Issue #25964)](https://github.com/oven-sh/bun/issues/25964) -- MEDIUM confidence, cross-verified with SQLite docs
- [MCP Broker Pattern Explained](https://www.byteplus.com/en/topic/542191) -- LOW confidence, general architecture reference
- [Agent-to-Agent Communication on MCP (Microsoft)](https://developer.microsoft.com/blog/can-you-build-agent2agent-communication-on-mcp-yes) -- MEDIUM confidence, Microsoft developer blog
- [Claude Code Lazy Loading for MCP Tools](https://jpcaparas.medium.com/claude-code-finally-gets-lazy-loading-for-mcp-tools-explained-39b613d1d5cc) -- LOW confidence, third-party blog

---
*Pitfalls research for: MCP Review Broker (gsd-tandem)*
*Researched: 2026-02-16*
