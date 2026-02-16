# Phase 1: Core Broker Server - Research

**Researched:** 2026-02-16
**Domain:** Python FastMCP server with SQLite persistence, state machine, poll-based coordination
**Confidence:** HIGH

## Summary

Phase 1 delivers the foundation for the entire GSD Tandem system: a FastMCP Streamable HTTP server backed by SQLite that implements a review lifecycle state machine. Two MCP clients (proposer and reviewer) connect to the same server, create reviews, claim them, and drive them to terminal states via polling. This phase addresses requirements FOUND-01, FOUND-02, FOUND-04, PROTO-01, PROTO-04, INTER-01, and INTER-02.

The standard approach is well-documented: FastMCP 2.14+ provides the MCP server framework with `@mcp.tool` decorators, lifespan-managed resources, and Streamable HTTP transport. aiosqlite bridges async Python to SQLite with WAL mode for concurrent reads. The state machine is pure Python with a transition table. The wait strategy is poll-and-return: tools never block longer than 30 seconds, and the proposer loops externally.

Three critical pitfalls must be addressed from day one: (1) SQLite `BEGIN IMMEDIATE` transactions prevent deadlocks that `busy_timeout` cannot resolve with DEFERRED transactions, requiring `isolation_level=None` on the aiosqlite connection; (2) MCP tool calls must return within 30 seconds to avoid Claude Code's timeout (default 60s); (3) the server must bind to `127.0.0.1` programmatically since CLI flags have had issues with streamable-http transport.

**Primary recommendation:** Build a three-layer architecture (tools -> services -> data) with FastMCP lifespan for the database connection, explicit `BEGIN IMMEDIATE` transactions via `isolation_level=None`, and a poll-and-return pattern for all blocking operations.

## Standard Stack

The established libraries/tools for this domain:

### Core
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| Python | 3.12-3.13 (pin 3.13) | Runtime | Stable floor for FastMCP and aiosqlite. 3.13 supported through 2028. |
| FastMCP | `>=2.14,<3` | MCP server framework | `@mcp.tool` decorator, automatic schema from type hints, Streamable HTTP transport, lifespan management, Context injection. Standalone FastMCP 2.x is the actively maintained production framework. |
| aiosqlite | `>=0.22,<1` | Async SQLite access | Async bridge to stdlib `sqlite3`. Required because FastMCP tools are async -- blocking `sqlite3` calls stall the event loop and freeze HTTP transport. |
| uv | `>=0.10.3` | Package/project manager | 2026 Python standard. 10-100x faster than pip. Manages virtualenvs, lockfiles, Python versions. Cross-platform. |

### Supporting
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | `>=2.0` (transitive via FastMCP) | Data validation & models | Define Review, Verdict, AgentIdentity as Pydantic models. Zero-cost since FastMCP depends on it. |
| uvicorn | (transitive via FastMCP) | ASGI server | FastMCP uses uvicorn internally for HTTP transport. No separate install needed. |
| pytest | `>=8.0` | Test framework | Standard Python testing for tool handlers and services. |
| pytest-asyncio | `>=0.24` | Async test support | Required for testing async tool handlers. Set `asyncio_mode = "auto"`. |
| ruff | `>=0.15.0` | Linter + formatter | Replaces flake8, isort, black in one tool. Configure in `pyproject.toml`. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| FastMCP 2.x | FastMCP 3.0 RC | 3.0 RC available (2026-02-14) but may have breaking changes. Pin to 2.x; upgrade after 3.0.1. |
| FastMCP 2.x | Official `mcp` SDK directly | Loses higher-level abstractions (lifespan, context injection, auto schema). More manual work. Not recommended. |
| aiosqlite | Synchronous `sqlite3` | Blocks event loop, freezes all connected MCP clients. Not viable. |
| aiosqlite | SQLAlchemy async | Massive overkill for 2-3 tables in Phase 1. Adds ORM complexity and migration tooling. |
| uv | pip + venv | 10-100x slower, no lockfile, manual venv management. |

**Installation:**
```bash
# In tools/gsd-review-broker/
uv sync              # Install all dependencies
uv sync --all-extras # Include dev dependencies
```

## Architecture Patterns

### Recommended Project Structure
```
tools/gsd-review-broker/
  pyproject.toml              # Package metadata, deps, tool config
  uv.lock                     # Committed to git
  .python-version             # Contains "3.13"
  src/
    gsd_review_broker/
      __init__.py
      server.py               # FastMCP app, lifespan, main(), tool imports
      tools.py                # All MCP tool definitions (Phase 1 has ~6 tools)
      models.py               # Pydantic models: Review, AgentIdentity
      state_machine.py         # State transitions, validation
      db.py                   # Schema DDL, connection setup, query helpers
  tests/
    conftest.py               # Shared fixtures (in-memory SQLite, test client)
    test_tools.py             # MCP tool handler tests
    test_state_machine.py     # State transition tests
    test_db.py                # Schema and query tests
```

**Phase 1 simplification:** Keep tools in a single `tools.py` file (only ~6 tools). Split into `tools/proposals.py`, `tools/reviews.py`, `tools/status.py` in Phase 2 when discussion tools are added. Premature splitting creates import complexity without benefit.

### Pattern 1: FastMCP Lifespan for Database Connection
**What:** Initialize aiosqlite connection once at server startup via FastMCP's `lifespan` parameter. Connection persists across all tool calls, closed on shutdown.
**When to use:** Always. This is the correct pattern for server-scoped resources in FastMCP.

```python
# Source: FastMCP official docs (gofastmcp.com/python-sdk/fastmcp-server-context)
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from pathlib import Path
import aiosqlite
from fastmcp import FastMCP

DB_PATH = Path(".planning") / "codex_review_broker.sqlite3"

@dataclass
class AppContext:
    db: aiosqlite.Connection

@asynccontextmanager
async def broker_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize SQLite with WAL mode at server startup."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(
        str(DB_PATH),
        isolation_level=None,  # CRITICAL: enables manual BEGIN IMMEDIATE
    )
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await _ensure_schema(db)
    try:
        yield AppContext(db=db)
    finally:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await db.close()

mcp = FastMCP(
    "gsd-review-broker",
    instructions="Review broker for GSD tandem pairing.",
    lifespan=broker_lifespan,
)
```

**Confidence:** HIGH -- lifespan pattern verified in FastMCP official docs and GitHub discussions (#1763).

### Pattern 2: Tool Definition with Context Access
**What:** Use `@mcp.tool` decorator (no parentheses for basic tools, with parentheses when passing options like `tags`). Access database via `ctx.lifespan_context`.
**When to use:** Every tool handler.

```python
# Source: FastMCP docs (gofastmcp.com/servers/tools, gofastmcp.com/python-sdk/fastmcp-server-context)
from fastmcp import FastMCP, Context

@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    ctx: Context = None,
) -> dict:
    """Create a new review for a proposed change. Returns review_id and status."""
    app: AppContext = ctx.lifespan_context
    # ... use app.db for database operations
    return {"review_id": review_id, "status": "pending"}
```

**Key rules verified against official docs:**
- `@mcp.tool` (no parens) for basic tools; `@mcp.tool(tags={"review"})` when passing options
- `ctx: Context = None` -- Context is auto-injected by FastMCP, hidden from MCP schema
- Access lifespan context via `ctx.lifespan_context` (NOT `ctx.request_context.lifespan_context`)
- Type hints on all parameters become the MCP input schema
- Docstring becomes tool description visible to AI agents

**Confidence:** HIGH -- verified against gofastmcp.com official docs (2026-02-16 fetch).

### Pattern 3: Explicit Transaction Management with BEGIN IMMEDIATE
**What:** Use `isolation_level=None` on aiosqlite connection and explicitly issue `BEGIN IMMEDIATE` for all write transactions.
**When to use:** Every database write operation.

```python
# Source: SQLite docs (sqlite.org/wal.html), aiosqlite source (github.com/omnilib/aiosqlite)
async def create_review_row(db: aiosqlite.Connection, review: Review) -> None:
    await db.execute("BEGIN IMMEDIATE")
    try:
        await db.execute(
            """INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase, plan, task, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (review.id, review.status, review.intent, review.agent_type,
             review.agent_role, review.phase, review.plan, review.task),
        )
        await db.execute("COMMIT")
    except Exception:
        await db.execute("ROLLBACK")
        raise
```

**Why `isolation_level=None` is required:** Python's `sqlite3` module (which aiosqlite wraps) auto-issues `BEGIN DEFERRED` unless `isolation_level=None`. DEFERRED transactions cause deadlocks when two processes try to upgrade from read to write -- `busy_timeout` is ignored in this case. Setting `isolation_level=None` puts the connection in autocommit mode and lets us issue `BEGIN IMMEDIATE` explicitly, which acquires the write lock upfront and respects `busy_timeout`.

**Confidence:** HIGH -- verified against SQLite locking docs and aiosqlite source code on GitHub.

### Pattern 4: State Machine with Transition Table
**What:** Review states and valid transitions defined as a dict. Service layer validates before executing.
**When to use:** All state changes on reviews.

```python
# PROTO-01 state machine: pending -> claimed -> in_review -> approved/changes_requested -> closed
from enum import StrEnum

class ReviewStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    CLOSED = "closed"

VALID_TRANSITIONS: dict[ReviewStatus, set[ReviewStatus]] = {
    ReviewStatus.PENDING:           {ReviewStatus.CLAIMED},
    ReviewStatus.CLAIMED:           {ReviewStatus.IN_REVIEW, ReviewStatus.APPROVED, ReviewStatus.CHANGES_REQUESTED},
    ReviewStatus.IN_REVIEW:         {ReviewStatus.APPROVED, ReviewStatus.CHANGES_REQUESTED},
    ReviewStatus.APPROVED:          {ReviewStatus.CLOSED},
    ReviewStatus.CHANGES_REQUESTED: {ReviewStatus.CLOSED, ReviewStatus.PENDING},  # resubmit
    ReviewStatus.CLOSED:            set(),  # terminal
}

def validate_transition(current: ReviewStatus, target: ReviewStatus) -> None:
    if target not in VALID_TRANSITIONS[current]:
        raise ValueError(f"Invalid transition: {current} -> {target}")
```

**Alignment with PROTO-01:** The requirement specifies `pending -> claimed -> in_review -> approved/changes_requested -> closed`. The architecture research used `created` and `in_discussion` as state names. Phase 1 MUST use the requirement's names (`pending`, `claimed`, `in_review`, `approved`, `changes_requested`, `closed`) since they are locked in REQUIREMENTS.md.

**Confidence:** HIGH -- standard state machine pattern, names aligned to PROTO-01 requirement.

### Pattern 5: Poll-and-Return Wait Strategy
**What:** Tools return immediately with current status. The proposer agent loops externally with delays between calls.
**When to use:** For INTER-02 (proposer blocks via polling).

```python
@mcp.tool
async def get_review_status(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Check the current status of a review. Call repeatedly to poll for changes.
    Returns immediately -- does NOT block waiting for reviewer action.
    Poll every 3-5 seconds for responsive experience."""
    app: AppContext = ctx.lifespan_context
    async with app.db.execute(
        "SELECT id, status, intent, claimed_by, updated_at FROM reviews WHERE id = ?",
        (review_id,),
    ) as cursor:
        row = await cursor.fetchone()
    if not row:
        return {"error": f"Review {review_id} not found"}
    return {
        "review_id": row["id"],
        "status": row["status"],
        "intent": row["intent"],
        "claimed_by": row["claimed_by"],
        "updated_at": row["updated_at"],
    }
```

**Why poll-and-return, not blocking:** Claude Code enforces a tool call timeout (default 60s, configured via `MCP_TIMEOUT` env var for startup, unclear for tool execution). A blocking `await_verdict` tool that waits minutes for a reviewer will be killed. The poll-and-return pattern keeps every tool call under 1 second on localhost, well within any timeout. The proposer's checkpoint logic handles the loop.

**Confidence:** HIGH -- verified against Claude Code MCP timeout behavior (GitHub issue #424).

### Anti-Patterns to Avoid
- **Blocking tool calls:** Never `await asyncio.sleep(N)` inside a tool handler waiting for external state. Always return immediately.
- **DEFERRED transactions:** Never use bare `BEGIN` or rely on Python's auto-BEGIN. Always `BEGIN IMMEDIATE`.
- **Multiple write connections:** Use ONE aiosqlite connection via lifespan. aiosqlite serializes operations on a single background thread.
- **God tools:** No `manage_review(action="create"|"claim"|"approve")`. One tool per action.
- **`fastmcp run` CLI for production:** Use `python -m gsd_review_broker.server` or `uv run gsd-review-broker` directly for reliable host/port binding.
- **`shell=True` in subprocess:** Never. Use `shell=False` with argument lists. Critical for Windows compatibility.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| MCP server framework | Custom HTTP server with JSON-RPC | FastMCP 2.x | Handles session management, tool schema, transport, content types |
| Async SQLite access | `run_in_executor(sqlite3)` | aiosqlite | Handles threading, connection lifecycle, row factory |
| Data validation | Manual dict checking | Pydantic models (via FastMCP) | Type-safe, serializable, auto-schema for MCP |
| UUID generation | Custom ID schemes | `uuid.uuid4()` | Standard, no coordination needed between clients |
| Cross-platform paths | String concatenation with `/` or `\\` | `pathlib.Path` | Resolves correctly on Windows and Unix |
| Package management | pip + virtualenv + requirements.txt | uv | Lockfile, fast resolution, cross-platform, Python version management |

**Key insight:** FastMCP handles the entire MCP protocol layer (session management, content negotiation, transport). The broker's custom code is ONLY the business logic: state machine, database queries, agent identity. Everything else is provided.

## Common Pitfalls

### Pitfall 1: SQLite DEFERRED Transaction Deadlock
**What goes wrong:** Two clients (proposer polling, reviewer claiming) both open DEFERRED transactions, both try to upgrade to write. SQLite returns SQLITE_BUSY immediately -- `busy_timeout` is completely ignored for lock upgrades.
**Why it happens:** Python's `sqlite3` default `isolation_level` auto-issues `BEGIN DEFERRED`. The upgrade from read lock to write lock fails instantly (by SQLite design) when another connection holds any lock.
**How to avoid:** Pass `isolation_level=None` to `aiosqlite.connect()`. Use explicit `BEGIN IMMEDIATE` for every write operation. This acquires the write lock upfront so `busy_timeout` works correctly.
**Warning signs:** Intermittent `SQLITE_BUSY` errors under concurrent load that don't reproduce in single-process tests.

### Pitfall 2: MCP Tool Timeout Kills Blocking Waits
**What goes wrong:** A tool that blocks for >60 seconds waiting for reviewer action gets killed by Claude Code's MCP client with `MCP error -32001: Request timed out`.
**Why it happens:** Claude Code's MCP client enforces a tool call timeout. `MCP_TIMEOUT` is documented as a startup/connection timeout, not a per-tool-call timeout, but the behavior kills long-running calls.
**How to avoid:** Never block in a tool call for more than a few seconds. Return current status immediately. The proposer's agent logic loops with delays between poll calls.
**Warning signs:** Tools that succeed in testing (fast reviewer) but fail in real usage (slow reviewer).

### Pitfall 3: FastMCP Host Binding Defaults to 0.0.0.0
**What goes wrong:** Server binds to all interfaces instead of localhost only, potentially exposing the broker to the network.
**Why it happens:** FastMCP's CLI `--host` flag had issues with streamable-http transport (GitHub issue #873). Default may be `0.0.0.0` depending on version.
**How to avoid:** Set host programmatically: pass `host="127.0.0.1"` to `mcp.run()`. Add a startup assertion verifying the binding. Never use `fastmcp run` CLI -- use `python -m` or `uv run` directly.
**Warning signs:** `netstat` shows broker listening on `0.0.0.0` instead of `127.0.0.1`.

### Pitfall 4: Windows File Locking on SQLite WAL
**What goes wrong:** On Windows, WAL mode holds file locks on `.db-wal` and `.db-shm` files beyond `connection.close()`. Broker restart fails with "database is locked".
**Why it happens:** Windows file locking semantics differ from POSIX. Memory-mapped I/O for WAL's shared-memory file holds handles longer. Antivirus software compounds the issue.
**How to avoid:** Run `PRAGMA wal_checkpoint(TRUNCATE)` before closing. Set `PRAGMA mmap_size=0` if problems persist. Test the full open-use-close-reopen cycle on actual Windows.
**Warning signs:** "database is locked" on broker restart (not during operation). `.db-wal`/`.db-shm` files persist after all processes exit.

### Pitfall 5: MCP Configuration File Confusion
**What goes wrong:** Broker configured in wrong file. Claude Code ignores `~/.claude/settings.json` for MCP servers. Tools appear "connected" in broker logs but Claude Code doesn't list them.
**Why it happens:** Claude Code uses `.mcp.json` at project root (or `claude mcp add` via CLI). Claude Desktop uses `claude_desktop_config.json`. Different systems, same name.
**How to avoid:** Use project-level `.mcp.json` with `type: "http"` and `url` pointing to broker endpoint. Provide both CLI command and JSON template. Test with `claude mcp list` to verify.
**Warning signs:** Broker logs show connections but `claude mcp list` doesn't show the server.

### Pitfall 6: Mutual Wait Deadlock Between Proposer and Reviewer
**What goes wrong:** Proposer waiting for verdict, reviewer polling but sees no proposals (race condition or reviewer started before proposal committed). Both idle indefinitely.
**Why it happens:** Timing gap between proposal INSERT commit and reviewer's next poll. No mechanism to detect stalled state.
**How to avoid:** Keep write transactions short (single INSERT). Return the review_id immediately after creation so the proposer can share it out-of-band if needed. Consider adding a `broker_status` tool that reports queue depth and last activity.
**Warning signs:** Both agents idle with no errors. Proposals in `pending` status with no claims.

## Code Examples

### Complete Server Startup
```python
# Source: FastMCP docs, verified 2026-02-16
from pathlib import Path
from fastmcp import FastMCP

from gsd_review_broker.db import broker_lifespan

mcp = FastMCP(
    "gsd-review-broker",
    instructions=(
        "Review broker for GSD tandem pairing. "
        "Manages review threads between a proposer (Claude Code) and a reviewer."
    ),
    lifespan=broker_lifespan,
)

# Import tools to register them with @mcp.tool
from gsd_review_broker import tools  # noqa: F401, E402

def main():
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8321,
    )

if __name__ == "__main__":
    main()
```

### Pydantic Models for Phase 1
```python
from pydantic import BaseModel, Field
from datetime import datetime
from enum import StrEnum
import uuid

class ReviewStatus(StrEnum):
    PENDING = "pending"
    CLAIMED = "claimed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    CLOSED = "closed"

class AgentIdentity(BaseModel):
    agent_type: str = Field(description="e.g. 'gsd-executor', 'gsd-planner'")
    agent_role: str = Field(description="'proposer' or 'reviewer'")
    phase: str = Field(description="e.g. '1', '3.2'")
    plan: str | None = Field(default=None, description="Plan name, if applicable")
    task: str | None = Field(default=None, description="Task number, if applicable")

class Review(BaseModel):
    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: ReviewStatus = ReviewStatus.PENDING
    intent: str
    agent_type: str
    agent_role: str
    phase: str
    plan: str | None = None
    task: str | None = None
    claimed_by: str | None = None
    parent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
```

### SQLite Schema (Phase 1 Subset)
```sql
-- Phase 1 only needs reviews table. Messages/patches/verdicts added in Phase 2+.
CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','claimed','in_review',
                                     'approved','changes_requested','closed')),
    intent          TEXT NOT NULL,
    agent_type      TEXT NOT NULL,
    agent_role      TEXT NOT NULL,
    phase           TEXT NOT NULL,
    plan            TEXT,
    task            TEXT,
    claimed_by      TEXT,
    verdict_reason  TEXT,
    parent_id       TEXT REFERENCES reviews(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_parent ON reviews(parent_id);
```

**Phase 1 schema simplification:** Only the `reviews` table is needed. Verdicts are stored as `status` + `verdict_reason` columns on the review itself. Separate `messages`, `verdicts`, and `patches` tables will be added in Phase 2 when discussion and diff protocol are introduced. This keeps Phase 1 minimal while supporting the full state machine.

### .mcp.json Configuration Template
```json
{
  "mcpServers": {
    "gsdreview": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp"
    }
  }
}
```

**Tool naming:** Tools registered as `create_review` become `mcp__gsdreview__create_review` in Claude Code. Keep tool names short and descriptive. Claude Code normalizes the server name from `.mcp.json`.

### pyproject.toml Template
```toml
[project]
name = "gsd-review-broker"
version = "0.1.0"
description = "MCP review broker for GSD tandem pairing"
requires-python = ">=3.12"
dependencies = [
    "fastmcp>=2.14,<3",
    "aiosqlite>=0.22,<1",
]

[project.optional-dependencies]
dev = [
    "pytest>=8.0",
    "pytest-asyncio>=0.24",
    "ruff>=0.15",
]

[project.scripts]
gsd-review-broker = "gsd_review_broker.server:main"

[tool.ruff]
target-version = "py312"
line-length = 100

[tool.ruff.lint]
select = ["E", "F", "I", "UP", "B", "SIM"]

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

### Test Fixture with In-Memory SQLite
```python
# tests/conftest.py
import pytest
import aiosqlite
from gsd_review_broker.db import ensure_schema

@pytest.fixture
async def db():
    """In-memory SQLite database for tests."""
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await ensure_schema(conn)
    yield conn
    await conn.close()
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| SSE transport for MCP servers | Streamable HTTP transport | MCP spec 2025-03-26 | SSE deprecated. Use `transport="streamable-http"` or `transport="http"`. |
| `mcp.server.fastmcp` (SDK built-in) | Standalone `fastmcp>=2.14` package | FastMCP 2.0 (2025) | Standalone package has richer API. Install `fastmcp`, not `mcp`. |
| pip + virtualenv | uv | 2025-2026 | uv is 10-100x faster, manages Python versions, has lockfiles. |
| `@mcp.tool()` always with parens | `@mcp.tool` without parens for basic | FastMCP 2.x docs | No parens needed unless passing `tags`, `name`, `description`, etc. |

**Deprecated/outdated:**
- SSE transport: Deprecated by MCP spec. Claude Code docs state "SSE transport is deprecated. Use HTTP servers instead."
- `from mcp.server.fastmcp import FastMCP`: The official SDK's built-in FastMCP is lower-level than standalone `fastmcp` package. Use `from fastmcp import FastMCP`.
- FastMCP 3.0 RC: Available but not stable. Pin to `>=2.14,<3` for production use.

## Open Questions

1. **MCP_TIMEOUT vs tool execution timeout**
   - What we know: `MCP_TIMEOUT` env var configures startup/connection timeout for Claude Code MCP clients. Claude Code docs mention it as "MCP server startup timeout."
   - What's unclear: Whether there is a separate per-tool-call timeout, what its default is, and whether it is configurable independently.
   - Recommendation: Design all tools to complete in <5 seconds. Test with a 120s simulated reviewer delay to verify the poll-and-return pattern works. Set `MCP_TIMEOUT=10000` in setup docs for a generous startup window.

2. **Codex MCP client capabilities**
   - What we know: Codex configuration documented with `[mcp_servers.name]` / `url = "..."` in config.toml. Streamable HTTP should be supported.
   - What's unclear: Codex's tool call timeout, response size limits, and whether it handles the same tool schemas as Claude Code.
   - Recommendation: Test with a second Claude Code instance as reviewer stand-in. Document Codex configuration but defer Codex-specific testing until access is available.

3. **FastMCP transport parameter: "http" vs "streamable-http"**
   - What we know: FastMCP docs show both `transport="http"` and `transport="streamable-http"` in different examples. Both appear to use the Streamable HTTP protocol.
   - What's unclear: Whether these are aliases or distinct transports.
   - Recommendation: Use `transport="streamable-http"` to be explicit. If it fails, fall back to `transport="http"`. Test during scaffolding.

4. **Optimal polling interval**
   - What we know: 2-3 seconds is recommended for localhost. Adaptive polling (start fast, back off when idle) is ideal but more complex.
   - What's unclear: What interval minimizes token waste while maintaining responsiveness.
   - Recommendation: Use fixed 3-second interval for Phase 1. Document as configurable. Add adaptive polling in later phase if needed.

## Phase 1 Tool Inventory

The server exposes 6 tools in Phase 1:

| Tool | Caller | Purpose | INTER-01 |
|------|--------|---------|----------|
| `create_review` | proposer | Create review with intent + agent identity | Full identity required |
| `list_reviews` | either | List reviews filtered by status | N/A |
| `claim_review` | reviewer | Take ownership of a pending review | Reviewer identity recorded |
| `submit_verdict` | reviewer | Approve or request changes, with optional reason | N/A |
| `get_review_status` | proposer | Poll for current status (poll-and-return) | N/A |
| `close_review` | proposer | Close an approved/rejected review | N/A |

**Phase 2 additions** (NOT Phase 1): `post_message`, `get_messages`, `submit_patch`, `get_patch`, `get_proposal`.

## Cross-Platform Validation (FOUND-04)

| Concern | Solution | Phase 1 Action |
|---------|----------|----------------|
| Path separators | `pathlib.Path` everywhere | Use `Path(".planning") / "codex_review_broker.sqlite3"` |
| Server startup | `uv run gsd-review-broker` | Works identically on both platforms |
| Port binding | `127.0.0.1:8321` | Same on both platforms |
| Process signals | Avoid `SIGTERM`/`SIGHUP` | Only handle `KeyboardInterrupt` (Ctrl+C) |
| SQLite file locking | WAL mode + checkpoint on close | Run `PRAGMA wal_checkpoint(TRUNCATE)` in lifespan finally block |
| Line endings | `.gitattributes` with `* text=auto` | Add to `tools/gsd-review-broker/.gitattributes` |
| uv installation | Cross-platform installer | Document both PowerShell and bash install commands |

## Sources

### Primary (HIGH confidence)
- [FastMCP Official Docs - Context](https://gofastmcp.com/python-sdk/fastmcp-server-context) -- lifespan context access pattern (`ctx.lifespan_context`)
- [FastMCP Official Docs - Running Server](https://gofastmcp.com/deployment/running-server) -- `mcp.run(transport="streamable-http", host, port)` configuration
- [FastMCP Official Docs - Tools](https://gofastmcp.com/servers/tools) -- `@mcp.tool` decorator, tags, Context injection
- [Claude Code MCP Docs](https://code.claude.com/docs/en/mcp) -- `.mcp.json` format (`type: "http"`, `url`), `MCP_TIMEOUT`, `MAX_MCP_OUTPUT_TOKENS`, scope hierarchy
- [SQLite WAL Documentation](https://sqlite.org/wal.html) -- Concurrency model, checkpointing
- [SQLite Locking v3](https://sqlite.org/lockingv3.html) -- DEFERRED vs IMMEDIATE transaction behavior
- [aiosqlite API](https://aiosqlite.omnilib.dev/en/stable/api.html) -- connect(), isolation_level, Connection class
- [aiosqlite Source](https://github.com/omnilib/aiosqlite/blob/main/aiosqlite/core.py) -- `isolation_level` kwarg forwarded to `sqlite3.connect()`
- [FastMCP GitHub Issue #873](https://github.com/jlowin/fastmcp/issues/873) -- Host binding gotcha with streamable-http
- [Claude Code GitHub Issue #424](https://github.com/anthropics/claude-code/issues/424) -- MCP timeout behavior

### Secondary (MEDIUM confidence)
- [FastMCP GitHub Discussion #1763](https://github.com/jlowin/fastmcp/discussions/1763) -- Lifespan usage patterns
- [SQLite Busy Timeout Analysis](https://berthub.eu/articles/posts/a-brief-post-on-sqlite3-database-locked-despite-timeout/) -- busy_timeout ignored for DEFERRED lock upgrades
- [Codex MCP Docs](https://developers.openai.com/codex/mcp/) -- config.toml format, streamable HTTP URL field

### Tertiary (LOW confidence)
- FastMCP 3.0 RC release notes (pre-release, API may change)
- Codex MCP client behavior (unverified -- flagged as Research Gap 3 in STATE.md)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- All libraries verified via official docs and PyPI. Versions pinned with rationale.
- Architecture: HIGH -- Three-layer pattern (tools/services/data) standard for MCP servers. Lifespan verified in FastMCP docs.
- State machine: HIGH -- PROTO-01 defines states explicitly. Standard transition table pattern.
- Database: HIGH -- SQLite WAL mode, `BEGIN IMMEDIATE`, aiosqlite patterns all verified against official sources.
- Pitfalls: HIGH -- All 6 pitfalls verified against official docs, GitHub issues, or SQLite specification.
- Poll-and-return: HIGH -- Verified against Claude Code timeout behavior (GitHub issue #424).
- Cross-platform: MEDIUM -- Patterns are standard (pathlib, WAL checkpoint), but actual Windows behavior needs empirical testing.

**Research date:** 2026-02-16
**Valid until:** 2026-03-16 (30 days -- stack is stable, FastMCP 2.x pinned)
