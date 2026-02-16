# Project Research Summary

**Project:** gsd-tandem (MCP Review Broker)
**Domain:** Local agent-to-agent code review coordination tool
**Researched:** 2026-02-16
**Confidence:** HIGH

## Executive Summary

The gsd-tandem project is a novel local MCP server that brokers code review between AI agents (primarily Claude Code as proposer and Codex/human as reviewer). The research reveals this is best built as a lightweight Python FastMCP server with SQLite persistence, using Streamable HTTP transport to support multiple concurrent clients. The architecture follows established review tool patterns (GitHub PR, Gerrit) adapted for AI-to-AI interaction, with unified diffs as the exchange format and a deterministic state machine managing review lifecycle.

The recommended approach is Python 3.12+ with FastMCP 2.14.5 for the MCP server framework, aiosqlite for async SQLite access, and uv for package management. The system stores all data (reviews, messages, patches) in a single SQLite database under `.planning/` with WAL mode for concurrent reads. The proposer submits proposals with intent + unified diff, the reviewer claims and evaluates them, and back-and-forth discussion happens via message threads. The broker never executes code or touches the working tree — it only coordinates communication and stores state.

Key risks center on SQLite concurrency (DEFERRED transaction deadlocks, Windows file locking), MCP client timeouts (Claude Code's 60s default kills blocking waits), and tool response truncation (25K token limit breaks large diffs). These are all preventable with upfront design: use `BEGIN IMMEDIATE` transactions, implement poll-and-return patterns instead of blocking, and paginate large responses. The architecture must handle these constraints from day one — retrofitting after building on wrong assumptions requires full rewrites.

## Key Findings

### Recommended Stack

Python 3.12+ with FastMCP 2.14.5 provides the MCP server foundation, leveraging the standalone FastMCP 2.x project rather than the lower-level official SDK for its higher-level abstractions (lifespan, context injection, automatic schema generation). aiosqlite wraps SQLite for async access without blocking the event loop, critical for the Streamable HTTP transport. uv replaces pip/virtualenv/poetry as the 2026-standard Python package manager (10-100x faster, cross-platform, lockfile-based). SQLite with WAL mode handles concurrent reads from both proposer and reviewer without contention.

**Core technologies:**
- **Python 3.12-3.13**: Stable runtime floor for all dependencies, 3.13 has support through 2028
- **FastMCP 2.14.5**: MCP server framework with `@mcp.tool()` decorator, Streamable HTTP transport, lifespan management
- **aiosqlite 0.22.1**: Async SQLite bridge — prevents blocking the event loop on database operations
- **SQLite with WAL**: File-based persistence under `.planning/`, WAL mode allows readers + 1 writer concurrently
- **uv 0.10.3+**: Package/project manager, replaces pip/virtualenv, 10-100x faster, cross-platform

**Critical version constraints:**
- FastMCP pinned to `>=2.14,<3` (2.x is stable, 3.0 RC has potential breaking changes)
- Python floor is 3.12 (all deps require it), ceiling is 3.14 (avoid bleeding edge)
- SQLite PRAGMA settings non-negotiable: `journal_mode=WAL`, `busy_timeout=5000`, `synchronous=NORMAL`

### Expected Features

The feature landscape is synthesized from mature code review tools (GitHub, Gerrit, Phabricator, Reviewable) adapted to the AI-to-AI context. Table stakes features are those without which the broker is non-functional. Differentiators add value but aren't required for v1. Anti-features are deliberately excluded despite seeming appealing.

**Must have (table stakes):**
- Review lifecycle state machine (created → claimed → in_review → approved/rejected → closed)
- Proposal creation with intent description + unified diff
- Verdict submission (approve / request changes / comment)
- Agent identity tracking (agent_type, role, phase, plan, task)
- Blocking wait mechanism (proposer pauses until reviewer responds)
- Message exchange for back-and-forth discussion
- Review creation and claiming (proposer opens, reviewer takes ownership)
- Unified diff transport (standard format, git-compatible)
- SQLite persistence (reviews survive process restarts)
- MCP tool interface (10 tools across 4 groups: proposal, review, discussion, status)

**Should have (competitive differentiators):**
- Counter-patch submission by reviewer (attach alternative diff, not just comments)
- Configurable review granularity (per-task default, per-plan batching)
- Automatic conflict detection (`git apply --check` before submitting proposal)
- Review priority/urgency levels (triage multiple pending reviews)
- Notification/callback mechanism (push instead of poll, reduces latency)
- Review history/audit log (query past reviews for post-mortem analysis)

**Defer (v2+):**
- Optimistic execution mode (apply changes before approval, rollback on rejection — requires git stash infrastructure)
- Structured inline comments (line-specific annotations — high complexity for marginal AI-to-AI value)
- Review delegation/escalation (multi-reviewer support — conflicts with v1 single-reviewer simplification)
- Review metrics and analytics (approval rate, average time — needs data volume first)
- Review templates/checklists (standardize evaluation criteria — defer until quality variance observed)

**Anti-features (deliberately excluded):**
- Rich diff rendering (syntax highlighting, side-by-side) — agents read text, no visual UI needed
- Multi-reviewer consensus (quorum voting) — massive state complexity for unclear benefit
- Authentication/authorization — localhost trusted environment, zero value on 127.0.0.1
- Automatic merge/commit on approval — violates "Claude remains sole committer" constraint
- Real-time collaborative editing — wrong architecture, async message exchange is correct model
- Review bot/linting integration — broker is communication channel, not CI system

### Architecture Approach

The architecture follows a clean three-layer separation: Tool Layer (MCP endpoint), Service Layer (business logic + state machine), and Data Layer (SQLite persistence). The FastMCP server runs as a standalone process on localhost:8321, exposing 10 MCP tools organized by tag. Both clients (proposer and reviewer) connect via Streamable HTTP to the same endpoint with isolated sessions (tracked via `Mcp-Session-Id` header). The shared state is the SQLite database; session state is per-client. The proposer polls for review status, the reviewer polls for pending reviews — no server-push notifications needed, polling on localhost is simple and sufficient.

**Major components:**
1. **FastMCP Server** — HTTP endpoint at `http://127.0.0.1:8321/mcp`, session management, tool dispatch, lifespan-managed database connection
2. **Tool Layer** — 10 MCP tools grouped by tag (proposal, review, discussion, status), thin validation layer that delegates to services
3. **Service Layer** — ReviewService (state machine), MessageService (discussion threading), PatchService (diff storage/retrieval), all async pure Python
4. **Data Layer** — Single aiosqlite connection via lifespan, schema with 4 tables (reviews, messages, verdicts, patches), WAL mode for concurrent reads
5. **Proposer Client (Claude Code)** — Submits proposals, polls for status, reads messages/patches, applies approved diffs, closes reviews
6. **Reviewer Client (Codex/human)** — Polls for pending reviews, claims, posts messages, submits patches, submits verdict

**Key architectural patterns:**
- **State machine in ReviewService**: Valid transitions enforced, impossible states prevented
- **Tool-per-action with tag grouping**: Each tool does one thing, tags provide logical grouping
- **Poll-and-return instead of blocking**: Tools never block longer than 20-30s, caller loops with delays
- **Diffs stored in SQLite as TEXT**: Not on filesystem — transactional consistency, cross-platform, faster for <100KB
- **Lifespan-managed connection**: Single aiosqlite connection initialized at startup, closed on shutdown, shared across all requests

**Data flow (happy path):**
1. Proposer calls `submit_proposal(intent, diff)` → broker creates review (status=created), returns review_id
2. Proposer polls `get_review_status(review_id)` every 2-3s
3. Reviewer calls `list_reviews(status="created")` → discovers new proposal
4. Reviewer calls `claim_review(review_id)` → status=claimed
5. Reviewer calls `submit_verdict(review_id, decision="approved")` → status=approved
6. Proposer sees status=approved in next poll, applies diff locally
7. Proposer calls `close_review(review_id)` → status=closed

**Data flow (back-and-forth):**
- Reviewer calls `post_message(review_id, body, role="reviewer")` → status=in_discussion
- Proposer polls `get_messages(review_id)` → sees reviewer's comments
- Proposer responds via `post_message(review_id, body, role="proposer")`
- Reviewer supplies alternative via `submit_patch(review_id, diff, role="reviewer")`
- Proposer reads via `get_patch(review_id)`, evaluates, responds
- Eventually reviewer calls `submit_verdict(approved|rejected)` → terminal state

### Critical Pitfalls

Research identified 8 critical pitfalls, each with specific prevention strategies and phase implications. The top 5 are particularly important for roadmap planning:

1. **SQLite DEFERRED Transaction Deadlock** — Two processes start read transactions, both try to upgrade to write, SQLite returns SQLITE_BUSY immediately (busy_timeout ignored). **Prevention:** Use `BEGIN IMMEDIATE` for all write transactions, set `PRAGMA busy_timeout=5000`, consider application-level write mutex. **Must fix in Phase 1** — database layer design.

2. **MCP Tool Timeout Kills Blocking Waits** — Claude Code enforces 60s tool call timeout. A blocking `await_verdict` tool that waits for human reviewer exceeds timeout, tool call killed with MCP error -32001. **Prevention:** Never block longer than 20-30s in a tool call, implement poll-and-return pattern, set `MCP_TIMEOUT=60000` minimum in docs. **Must fix in Phase 1** — wait strategy design.

3. **MCP Tool Response Truncation** — Claude Code limits tool responses to 25K tokens (configurable via `MAX_MCP_OUTPUT_TOKENS`). Large unified diffs exceed limit, get truncated, proposer receives partial diff. **Prevention:** Paginate diff responses, return summary + first page, provide `get_diff_page` tool for rest. **Must fix in Phase 2** — diff transport design.

4. **FastMCP Host Binding Gotcha** — CLI `--host` flag doesn't work with streamable-http transport, server may bind to 0.0.0.0 instead of 127.0.0.1, exposing to network. **Prevention:** Set host programmatically in server script (`host="127.0.0.1"`), never use `fastmcp run` CLI, add startup assertion. **Must fix in Phase 1** — server startup.

5. **Mutual Wait Deadlock** — Proposer waits for verdict, reviewer polls but sees no proposals (race condition or timing gap), both idle. **Prevention:** Implement TTL on proposals (timeout after N seconds), track reviewer liveness (last poll time), add `broker_status` diagnostic tool. **Must fix in Phase 1** — state machine + Phase 3 observability.

**Other critical pitfalls:**
- Windows file locking holds SQLite database after connection close (WAL `-shm`/`-wal` files) — run `PRAGMA wal_checkpoint(TRUNCATE)` before close
- Python `difflib.unified_diff` produces invalid patches (missing `\\ No newline at end of file`) — use `git diff` subprocess or post-process output
- MCP configuration confusion between Claude Code and Claude Desktop (different config files) — provide `.mcp.json` template, test setup on clean machine

## Implications for Roadmap

Based on combined research, the suggested phase structure follows dependency ordering and risk mitigation:

### Phase 1: Foundation + Core Broker
**Rationale:** Everything depends on database schema, models, and the basic server lifecycle. State machine and wait strategy must be correct from day one — retrofitting after building on wrong assumptions is expensive. Critical pitfalls (SQLite DEFERRED deadlock, MCP timeout, host binding, mutual wait) must all be addressed in this phase.

**Delivers:**
- SQLite schema (4 tables: reviews, messages, verdicts, patches) with WAL mode
- Pydantic models (Review, Message, Verdict, Patch)
- FastMCP server with lifespan-managed aiosqlite connection, bound to 127.0.0.1:8321
- ReviewService with state machine (created → claimed → in_discussion → approved/rejected → closed)
- Basic tools: `submit_proposal`, `get_proposal`, `claim_review`, `submit_verdict`, `close_review`, `list_reviews`
- Poll-and-return wait strategy (no blocking tool calls)
- TTL on proposals to prevent mutual wait deadlock

**Addresses features:**
- Review lifecycle state machine (table stakes)
- Proposal creation (table stakes)
- Verdict submission (table stakes)
- Review creation and claiming (table stakes)
- Agent identity tracking (table stakes)
- SQLite persistence (table stakes)
- Blocking wait mechanism via polling (table stakes)

**Avoids pitfalls:**
- Pitfall 1: `BEGIN IMMEDIATE` for all writes, `PRAGMA busy_timeout=5000`
- Pitfall 2: Poll-and-return pattern, no tool call blocks longer than 30s
- Pitfall 4: Host set programmatically, startup assertion verifies 127.0.0.1 binding
- Pitfall 5: TTL on proposals, reviewer liveness tracking, `broker_status` diagnostic tool stub

**Research flag:** Standard patterns, well-documented. Skip `/gsd:research-phase`.

---

### Phase 2: Discussion + Patch Protocol
**Rationale:** The happy path (propose → approve → apply) must work before adding discussion complexity. This phase adds the message threading and diff storage that enable back-and-forth between proposer and reviewer. Must handle MCP response truncation (Pitfall 3) and invalid diffs (Pitfall 7).

**Delivers:**
- MessageService (threaded discussion on reviews)
- PatchService (diff storage, retrieval, validation)
- Discussion tools: `post_message`, `get_messages`, `submit_patch`, `get_patch`
- Diff pagination (summary + first page, `get_diff_page` for rest)
- Diff validation (`git apply --check` before storing)
- Counter-patch support (reviewer supplies alternative unified diff)

**Addresses features:**
- Message exchange (table stakes)
- Diff transport in unified format (table stakes)
- Counter-patch submission (differentiator)
- Automatic conflict detection (differentiator)

**Avoids pitfalls:**
- Pitfall 3: Paginated diff responses, `MAX_MCP_OUTPUT_TOKENS=50000` in docs
- Pitfall 7: Use `git diff` subprocess or post-process `difflib` output, round-trip tests

**Uses stack:**
- aiosqlite for storing patches as TEXT
- Git subprocess for diff validation
- Pydantic for message/patch models

**Implements architecture:**
- MessageService (discussion threading)
- PatchService (diff storage/retrieval)
- Tool Layer: discussion group

**Research flag:** May need deeper dive on unified diff edge cases (files without trailing newlines, binary files, symlinks, submodules). Consider `/gsd:research-phase` if complexity emerges.

---

### Phase 3: Observability + Human Interface
**Rationale:** With core review loop working end-to-end, add visibility for the human observer. This phase makes the system debuggable and understandable when things go wrong.

**Delivers:**
- `broker_status` tool (queue depth, pending reviews, last reviewer poll, active connections)
- Review history query tools (`list_reviews` with filtering, pagination)
- Enhanced error messages (structured responses with actionable info)
- Audit log export (all reviews, messages, verdicts for a project)
- Reviewer liveness tracking (detect when reviewer disconnected)
- Cancellation support (`cancel_proposal` tool)

**Addresses features:**
- Review history/audit log (differentiator)
- Review priority (differentiator, if time permits)

**Avoids pitfalls:**
- Pitfall 5: Full implementation of reviewer liveness detection
- UX pitfalls: Visibility into why Claude is "stuck", error messages hide internal state

**Research flag:** Standard patterns. Skip `/gsd:research-phase`.

---

### Phase 4: Cross-Platform Validation + Polish
**Rationale:** The broker targets both Windows (PowerShell) and macOS/Linux (bash). Platform-specific issues (Windows file locking, path separators, subprocess shell differences) must be verified on real systems, not assumed.

**Delivers:**
- Windows-specific testing (file locking, path separators, subprocess calls)
- Cross-platform subprocess calls (`shell=False` with argument lists)
- Line ending normalization (CRLF vs LF)
- Path handling via `pathlib.Path` (no hardcoded `/` or `\\`)
- Comprehensive integration tests (two concurrent clients, Windows CI, macOS CI)
- Setup documentation with `.mcp.json` template, verification steps

**Addresses:**
- Cross-platform support (implicit requirement from PROJECT.md context)

**Avoids pitfalls:**
- Pitfall 6: Windows file locking on connection close, checkpoint WAL before shutdown
- Pitfall 8: MCP configuration confusion, provide `.mcp.json` template and setup verification
- Integration gotchas: Test on actual Windows, not WSL

**Research flag:** Standard patterns, but actual Windows testing required. Skip research, focus on validation.

---

### Phase 5: GSD Integration + End-to-End Workflow
**Rationale:** With the broker fully functional and tested, integrate into GSD workflow. This means updating gsd-executor and gsd-planner agents to use the broker tools, adding checkpoint logic, and documenting the review workflow.

**Delivers:**
- gsd-executor updated: submit proposals before applying changes, wait for approval, apply approved diffs
- gsd-planner updated: optionally submit plan-level diffs for review (configurable granularity)
- Checkpoint integration: save/restore review state across agent restarts
- Review workflow documentation (how proposer and reviewer interact, what tools to call when)
- Configuration guide (how to set up Codex or human reviewer as MCP client)
- End-to-end smoke tests (full GSD planning → execution → review cycle)

**Addresses features:**
- MCP tool interface (table stakes, now tested in full GSD context)
- Configurable review granularity (differentiator)

**Research flag:** GSD-specific integration. May need `/gsd:research-phase` to understand gsd-executor checkpoint logic and how to inject review step into execution flow without breaking existing workflow.

---

### Phase Ordering Rationale

The phase order follows these principles:

1. **Foundation before features** — Phase 1 must get the database, state machine, and wait strategy right because everything else builds on it. Retrofitting `BEGIN IMMEDIATE` or poll-and-return after building on wrong patterns is a full rewrite.

2. **Happy path before complexity** — Phase 2 adds discussion/patches only after the simple approve/reject flow works. This enables early validation of the core value proposition before adding complexity.

3. **Observability after functionality** — Phase 3 makes the system debuggable, but only after there's something to observe. Building diagnostics before the thing being diagnosed exists is premature.

4. **Platform validation before integration** — Phase 4 validates cross-platform behavior in isolation before integrating into the larger GSD system. This prevents "works on my Mac, breaks on Windows in production" scenarios.

5. **GSD integration last** — Phase 5 integrates into GSD workflow only after the broker is known to work correctly in isolation. This minimizes blast radius — if integration fails, the problem is known to be in the integration layer, not the broker.

**Critical path dependencies:**
- Phase 2 depends on Phase 1 (discussion requires working state machine)
- Phase 3 depends on Phase 2 (observability requires full review cycle)
- Phase 5 depends on Phase 4 (GSD integration requires cross-platform validation)

**Parallel opportunities:**
- Phase 3 (observability) and Phase 4 (cross-platform) can partially overlap — observability tools don't depend on Windows testing
- Phase 2 discussion tools and Phase 2 patch tools can be developed in parallel — they share the data layer but are functionally independent

### Research Flags

**Phases likely needing deeper research during planning:**

- **Phase 2 (Diff/Patch Protocol):** Unified diff edge cases are numerous (binary files, symlinks, submodules, file renames, permission changes, files without trailing newlines). Consider `/gsd:research-phase` if automated round-trip testing reveals failures. Research topics: Git diff format specification, `git apply` behavior, handling non-text files.

- **Phase 5 (GSD Integration):** The gsd-executor checkpoint mechanism and execution flow are GSD-specific. Research needed: How does gsd-executor currently handle file changes? Where does the review step inject into the execution loop? How to save/restore review state across agent restarts? Can use `/gsd:research-phase` with focus on "GSD executor checkpoint integration."

**Phases with standard patterns (skip research-phase):**

- **Phase 1 (Foundation + Core Broker):** FastMCP lifespan, aiosqlite, SQLite WAL, state machines — all well-documented. Official FastMCP docs, SQLite docs, and aiosqlite examples cover everything needed.

- **Phase 3 (Observability + Human Interface):** Standard API design patterns (status endpoints, query filters, pagination, audit logs). No novel concepts.

- **Phase 4 (Cross-Platform Validation):** Not a research problem, it's a testing/validation problem. The patterns are known (pathlib, shell=False, line ending normalization), just need to be applied and verified on actual Windows.

## Confidence Assessment

| Area | Confidence | Notes |
|------|------------|-------|
| Stack | HIGH | All technologies have official docs, active maintenance, and verified examples. FastMCP 2.14.5 is stable (pinned to 2.x), aiosqlite is stdlib wrapper, SQLite WAL mode is battle-tested. Python 3.12+ floor verified against dependency requirements. |
| Features | MEDIUM | Table stakes features are HIGH confidence (synthesized from GitHub, Gerrit, Phabricator — established review tool patterns). Differentiators and their prioritization are MEDIUM confidence — AI-to-AI review is novel, so relative value is inferred rather than observed. Anti-features list is opinionated but defensible. |
| Architecture | HIGH | Architecture follows established MCP server patterns (FastMCP official examples), standard three-layer separation (tool/service/data), and polling-based coordination (proven in MCP ecosystem). State machine design is textbook. SQLite-as-shared-state with per-client sessions is the correct MCP multi-client model per spec. |
| Pitfalls | HIGH | All 8 critical pitfalls verified against official docs (SQLite locking docs, FastMCP GitHub issues, Claude Code GitHub issues, CPython bug tracker). Each pitfall has concrete reproduction case or GitHub issue number. Prevention strategies tested in production systems. |

**Overall confidence:** HIGH

The research is grounded in official documentation and verified examples. The novel aspect (AI-to-AI review) doesn't change the technical fundamentals — it's still an MCP server coordinating two clients via SQLite state. The confidence level reflects that the stack, architecture, and pitfalls are all knowable and documented, not speculative.

### Gaps to Address

**Gap 1: Actual Windows behavior under concurrent load**

Research documents Windows file locking pitfalls (Pitfall 6) based on SQLite docs and third-party reports, but hasn't been validated on actual Windows with two concurrent MCP clients. **Resolution:** Phase 4 must include real Windows testing with Claude Code + Codex both connected, running full review cycles, verifying clean shutdown and restart without lock errors. Consider Windows CI in GitHub Actions or AppVeyor.

**Gap 2: Claude Code MCP client timeout configuration**

Research identifies that `MCP_TIMEOUT` is configurable, but documentation on where this is set (environment variable? Claude settings file? per-project?) is unclear from official docs. **Resolution:** Phase 1 setup documentation must empirically determine the correct method and test it. Fallback: assume 60s default, design all tools to complete within 30s to have safety margin.

**Gap 3: Codex MCP client capabilities**

Research assumes Codex supports Streamable HTTP MCP transport with similar capabilities to Claude Code, but this hasn't been verified. Token limits, timeout behavior, and tool discovery may differ. **Resolution:** Phase 1 must include early testing with actual Codex client. If Codex isn't available, document as "reviewer-agnostic" and test with a second Claude Code instance as stand-in.

**Gap 4: GSD executor checkpoint mechanism**

Research doesn't cover how gsd-executor currently handles checkpoints or where the review step should inject into the execution flow. This is GSD-specific internal logic. **Resolution:** Phase 5 planning should include `/gsd:research-phase` focused on gsd-executor code review to understand checkpoint structure and injection points.

**Gap 5: Optimal polling interval**

Research recommends 2-3 second polling based on "localhost is fast" reasoning, but optimal interval depends on actual review latency (human reviewer may take minutes, Codex may respond in seconds). **Resolution:** Phase 3 could implement adaptive polling (start at 2s, back off to 10s if no activity, snap back on status change). Not critical for v1, fixed 3s interval is acceptable.

## Sources

### Primary (HIGH confidence)

- **FastMCP Official Documentation** — [gofastmcp.com](https://gofastmcp.com/) — Server context, tool patterns, lifespan management, Streamable HTTP transport configuration
- **MCP Specification (2025-03-26)** — [modelcontextprotocol.io](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports) — Transport layer, session management, tool protocols
- **SQLite Official Documentation** — [sqlite.org](https://sqlite.org/) — WAL mode, locking behavior, performance benchmarks (35% faster than filesystem for <100KB blobs)
- **Python Official Docs** — [python.org](https://www.python.org/) — aiosqlite, asyncio patterns, subprocess best practices
- **Claude Code MCP Documentation** — [code.claude.com](https://code.claude.com/docs/en/mcp) — MCP client configuration, `.mcp.json` format, tool discovery
- **GitHub PR Reviews API** — [docs.github.com](https://docs.github.com/en/rest/pulls/reviews) — Review states, threading model, verdict types
- **FastMCP GitHub Issues** — [github.com/jlowin/fastmcp](https://github.com/jlowin/fastmcp) — Host binding issue (#873), lifespan patterns, known bugs

### Secondary (MEDIUM confidence)

- **Gerrit Code Review Documentation** — [gerrit.wikimedia.org](https://gerrit.wikimedia.org/r/Documentation/) — Label-based voting, patchset workflow, multi-reviewer patterns
- **Phabricator Differential Guide** — [secure.phabricator.com](https://secure.phabricator.com/book/phabricator/article/differential/) — Revision workflow, inline comments, acceptance criteria
- **Reviewable Documentation** — [docs.reviewable.io](https://docs.reviewable.io/) — Discussion threading, disposition tracking, review state management
- **SQLite Concurrency Analysis** — [tenthousandmeters.com](https://tenthousandmeters.com/blog/sqlite-concurrent-writes-and-database-is-locked-errors/) — DEFERRED vs IMMEDIATE transaction behavior, busy_timeout gotchas
- **Claude Code GitHub Issues** — [github.com/anthropics/claude-code](https://github.com/anthropics/claude-code) — MCP timeout (#424), response truncation (#2638), tool name length (#2579), config confusion

### Tertiary (LOW confidence, needs validation)

- **AI Agent Protocols 2026 Overview** — [ruh.ai](https://www.ruh.ai/blogs/ai-agent-protocols-2026-complete-guide) — General A2A and MCP landscape, trends, not specific to this architecture
- **Microsoft Developer Blog: Agent2Agent on MCP** — [developer.microsoft.com](https://developer.microsoft.com/blog/can-you-build-agent2agent-communication-on-mcp-yes) — Conceptual overview, not implementation details
- **MCP Broker Pattern** — [byteplus.com](https://www.byteplus.com/en/topic/542191) — General broker architecture, not MCP-specific
- **MCP Long-Running Tasks Discussion** — [GitHub issue #982](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/982) — Community discussion on polling vs streaming, not resolved guidance

---
*Research synthesis completed: 2026-02-16*
*Ready for roadmap creation: yes*
*All research files committed and ready for gsd-roadmapper agent consumption*
