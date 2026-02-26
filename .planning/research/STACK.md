# Stack Research

**Domain:** MCP Review Broker Server (Python, local dev tool)
**Researched:** 2026-02-16
**Confidence:** HIGH

---

## Recommended Stack

### Core Technologies

| Technology | Version | Purpose | Why Recommended | Confidence |
|------------|---------|---------|-----------------|------------|
| Python | >=3.12, <3.15 | Runtime | 3.12 is the stable floor for all dependencies (FastMCP, aiosqlite). 3.13 has extended support through 2028. 3.14 is available but library ecosystem is still catching up. Pin `.python-version` to 3.13. | HIGH |
| FastMCP | ~=2.14.5 (pin `fastmcp>=2.14,<3`) | MCP server framework | The standard framework powering ~70% of MCP servers. Provides `@mcp.tool()` decorator, automatic schema generation from type hints, Streamable HTTP transport, Context dependency injection, and lifespan management. FastMCP 1.0 was incorporated into the official `mcp` SDK, but the standalone FastMCP 2.x project is the actively maintained, production-ready framework with higher-level abstractions. | HIGH |
| aiosqlite | ~=0.22.1 | Async SQLite access | Provides asyncio bridge to stdlib `sqlite3`. Required because FastMCP tools are async functions and blocking the event loop with synchronous `sqlite3` calls would stall the Streamable HTTP transport. Lightweight wrapper, no new dependencies. | HIGH |
| uv | >=0.10.3 | Package/project manager | The 2026 standard for Python packaging. 10-100x faster than pip. Manages virtualenvs, lockfiles (`uv.lock`), and Python versions. Cross-platform (Windows, macOS, Linux). Replaces pip, pip-tools, virtualenv, and pyenv in a single tool. | HIGH |

### Supporting Libraries

| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | >=2.0 (transitive via FastMCP) | Data validation & models | Define review thread, proposal, verdict schemas as Pydantic models. FastMCP already depends on it so it's zero-cost to use. Use `BaseModel` for all domain objects persisted to SQLite. |
| uvicorn | (transitive via FastMCP) | ASGI server | FastMCP uses uvicorn internally for Streamable HTTP transport. No need to install separately. Mentioned here because you can configure it via FastMCP's `mcp.run()` params. |
| ruff | >=0.15.0 | Linter + formatter | Replaces flake8, isort, black in one tool. Written in Rust, extremely fast. Configure in `pyproject.toml` under `[tool.ruff]`. |
| pytest | >=8.0 | Test framework | Standard Python testing. Use with `pytest-asyncio` for async tool handler tests. |
| pytest-asyncio | >=0.24 | Async test support | Required for testing async MCP tool handlers. Set `asyncio_mode = "auto"` in pyproject.toml. |

### Development Tools

| Tool | Purpose | Notes |
|------|---------|-------|
| uv | Project management, dependency resolution, virtualenv | `uv sync` to install, `uv run` to execute, `uv add` to manage deps. Lockfile (`uv.lock`) goes in version control. |
| ruff | Lint + format | `ruff check --fix .` and `ruff format .` Replace all of flake8/black/isort. Config in `pyproject.toml`. |
| pytest | Testing | `uv run pytest` to execute. Use `pytest-asyncio` for async tests. |
| sqlite3 CLI | Database inspection | Ships with Python/SQLite. Use for debugging: `sqlite3 .planning/codex_review_broker.sqlite3 ".tables"` |

---

## Transport & Connectivity

### FastMCP Streamable HTTP Setup

```python
from fastmcp import FastMCP

mcp = FastMCP(
    name="gsd-review-broker",
    instructions="Review broker for GSD tandem pairing. Manages review threads between a proposer (Claude Code) and a reviewer (Codex or another agent).",
)

# Tools defined with @mcp.tool() decorator (see Tool Patterns below)

if __name__ == "__main__":
    mcp.run(
        transport="streamable-http",  # NOT "sse" (deprecated) or "stdio"
        host="127.0.0.1",             # localhost only, never 0.0.0.0
        port=8321,                    # pick a distinctive port
    )
```

The server endpoint becomes: `http://127.0.0.1:8321/mcp`

### Claude Code Connection

```bash
# Add the broker as an MCP server (local scope, HTTP transport)
claude mcp add --transport http gsd-review-broker http://127.0.0.1:8321/mcp
```

Or in `.mcp.json` (project scope, checked into git):
```json
{
  "mcpServers": {
    "gsd-review-broker": {
      "type": "http",
      "url": "http://127.0.0.1:8321/mcp"
    }
  }
}
```

### Codex CLI Connection

In `~/.codex/config.toml` or `.codex/config.toml` (project scope):
```toml
[mcp_servers.gsd-review-broker]
url = "http://127.0.0.1:8321/mcp"
```

Both Claude Code and Codex support Streamable HTTP transport natively. Both connect to the same `http://127.0.0.1:8321/mcp` endpoint. Multiple concurrent clients are supported by the transport.

---

## Tool Definition Patterns

### Basic Tool Pattern

```python
from fastmcp import FastMCP

mcp = FastMCP("gsd-review-broker")

@mcp.tool()
def create_review(
    title: str,
    diff: str,
    proposer_agent: str,
    phase: str,
    task_number: int | None = None,
) -> dict:
    """Create a new review thread with a proposed change.

    The proposer submits a unified diff for review. Returns the
    review thread ID for subsequent operations.
    """
    # ...implementation...
    return {"review_id": review_id, "status": "pending"}
```

Key rules:
- Always use `@mcp.tool()` with parentheses (not `@mcp.tool`)
- Type hints on all parameters -- they become the MCP input schema
- Docstring becomes the tool description shown to AI agents
- Return type determines output schema

### Async Tool with Context

```python
from fastmcp import FastMCP
from fastmcp.server.context import Context

mcp = FastMCP("gsd-review-broker")

@mcp.tool()
async def submit_verdict(
    review_id: str,
    verdict: str,  # "approved" | "changes_requested" | "rejected"
    notes: str = "",
    patch: str | None = None,
    ctx: Context = None,  # Auto-injected, hidden from MCP schema
) -> dict:
    """Submit a review verdict on a pending review thread."""
    if ctx:
        await ctx.info(f"Verdict '{verdict}' submitted for review {review_id}")
    # ...implementation...
    return {"review_id": review_id, "verdict": verdict}
```

The `Context` parameter is automatically injected by FastMCP and excluded from the MCP schema. Clients never see it.

### Lifespan Pattern for Database Connection

```python
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
import aiosqlite
from fastmcp import FastMCP

@dataclass
class AppContext:
    db: aiosqlite.Connection

@asynccontextmanager
async def app_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize SQLite with WAL mode at server startup."""
    db = await aiosqlite.connect(".planning/codex_review_broker.sqlite3")
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    try:
        yield AppContext(db=db)
    finally:
        await db.close()

mcp = FastMCP("gsd-review-broker", lifespan=app_lifespan)

@mcp.tool()
async def list_reviews(status: str = "pending", ctx: Context = None) -> list[dict]:
    """List review threads filtered by status."""
    db = ctx.lifespan_context.db  # type: AppContext
    async with db.execute(
        "SELECT * FROM reviews WHERE status = ?", (status,)
    ) as cursor:
        rows = await cursor.fetchall()
    return [dict(row) for row in rows]
```

---

## SQLite Persistence

### Configuration Pragmas (Non-Negotiable)

```sql
PRAGMA journal_mode=WAL;        -- Write-Ahead Logging: readers never block writers
PRAGMA busy_timeout=5000;        -- Wait up to 5s for locks instead of failing immediately
PRAGMA synchronous=NORMAL;       -- Safe with WAL, avoids fsync on every commit
PRAGMA foreign_keys=ON;          -- Enforce referential integrity
PRAGMA wal_autocheckpoint=500;   -- Checkpoint more frequently for multi-client scenario
```

### Why WAL Mode

- **Concurrency**: Multiple MCP clients (Claude Code + Codex) read simultaneously without blocking
- **Write safety**: SQLite still allows only one writer at a time, but WAL ensures readers are never blocked by a write
- **Performance**: Avoids fsync on every commit with `synchronous=NORMAL`
- **Simplicity**: No connection pooling needed for this workload (low write frequency, short transactions)

### Concurrency Strategy

| Concern | Strategy |
|---------|----------|
| Multiple readers | WAL mode handles this natively. No contention. |
| Concurrent writes | SQLite serializes writes. `busy_timeout=5000` prevents SQLITE_BUSY errors under normal load. |
| Long transactions | Avoid. Keep write transactions short (single INSERT/UPDATE). |
| Connection sharing | Use ONE `aiosqlite` connection per server instance (via lifespan). aiosqlite serializes operations on a single background thread. |
| Schema migrations | Run at startup in the lifespan before yielding. Use `CREATE TABLE IF NOT EXISTS`. |
| Row factory | Set `db.row_factory = aiosqlite.Row` for dict-like access to rows. |

### What NOT to Do with SQLite

- **Do NOT use multiple write connections.** aiosqlite uses a single background thread per connection. One connection is sufficient and avoids lock contention.
- **Do NOT use an ORM (SQLAlchemy, Tortoise).** Massive overkill for ~5 tables. Raw SQL with parameterized queries is simpler, faster, and fully auditable.
- **Do NOT skip WAL mode.** Default journal mode will cause `SQLITE_BUSY` errors immediately when two clients connect.
- **Do NOT store large blobs.** Diffs should be stored as TEXT. If diffs exceed ~1MB, store them as files and reference the path.

---

## Python Packaging

### pyproject.toml Structure

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

### Project Layout

```
tools/gsd-review-broker/
  pyproject.toml
  uv.lock                    # Committed to git
  .python-version            # Contains "3.13"
  src/
    gsd_review_broker/
      __init__.py
      server.py              # FastMCP app, tool definitions, main()
      db.py                  # Schema, migrations, query helpers
      models.py              # Pydantic models for review, proposal, verdict
  tests/
    test_tools.py            # MCP tool handler tests
    test_db.py               # SQLite query tests
    conftest.py              # Shared fixtures (in-memory SQLite, etc.)
```

### Key Commands

```bash
# Install all dependencies (creates venv automatically)
uv sync

# Install with dev dependencies
uv sync --all-extras

# Run the server
uv run gsd-review-broker

# Or directly
uv run python -m gsd_review_broker.server

# Run tests
uv run pytest

# Lint and format
uv run ruff check --fix .
uv run ruff format .

# Add a new dependency
uv add <package>
uv add --dev <package>
```

---

## Cross-Platform Considerations

### Windows PowerShell + macOS/Linux Bash

| Concern | Solution |
|---------|----------|
| Path separators | Use `pathlib.Path` everywhere in Python. Never hardcode `/` or `\\`. |
| SQLite path | `Path(".planning") / "codex_review_broker.sqlite3"` resolves correctly on both platforms. |
| Server startup | `uv run gsd-review-broker` works identically on both platforms. |
| Environment variables | Use `os.environ.get()` in Python. For shell scripts, provide both `.ps1` and `.sh` variants (or rely solely on `uv run` which is cross-platform). |
| Line endings | Configure `.gitattributes` with `* text=auto` and `*.py text eol=lf`. |
| Port binding | `127.0.0.1:8321` works identically on Windows and macOS/Linux. |
| Process signals | `SIGINT` (Ctrl+C) works on both. Avoid `SIGTERM`/`SIGHUP` handling (Windows doesn't have them). Use `asyncio.Event` for graceful shutdown instead. |
| File locking | SQLite handles this natively on both platforms. No application-level locking needed. |
| uv installation | Windows: `powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 \| iex"` / macOS-Linux: `curl -LsSf https://astral.sh/uv/install.sh \| sh` |

### Windows-Specific Gotchas

- **Claude Code MCP with npx**: On native Windows, stdio MCP servers using `npx` require `cmd /c` wrapper. This does NOT apply to HTTP transport servers (our case), since the broker runs as its own process.
- **SQLite WAL on network drives**: WAL mode requires shared-memory primitives. Never put the SQLite database on a network drive. `.planning/` is local by design so this is not an issue.
- **Long paths**: Enable long paths in Windows if the project is nested deeply. Python 3.12+ handles this well.

---

## Installation

```bash
# Core (in tools/gsd-review-broker/)
uv sync

# Dev dependencies
uv sync --all-extras

# Verify
uv run fastmcp version
uv run python -c "import aiosqlite; print(aiosqlite.__version__)"
```

---

## Alternatives Considered

| Recommended | Alternative | When to Use Alternative |
|-------------|-------------|-------------------------|
| FastMCP 2.x (`fastmcp>=2.14,<3`) | Official `mcp` SDK (`from mcp.server.fastmcp import FastMCP`) | If you need zero third-party deps beyond Anthropic's SDK. But you lose the higher-level abstractions, lifespan management, and the standalone FastMCP's richer Context API. Not recommended. |
| FastMCP 2.x | FastMCP 3.0 RC (`fastmcp>=3.0.0rc1`) | If you need 3.0 features (component versioning, granular authorization, OpenTelemetry). Currently RC with potential breaking changes. Pin to 2.x for stability; upgrade when 3.0 goes GA. |
| aiosqlite | `sqlite3` (stdlib, synchronous) | If all tool handlers were synchronous. But FastMCP runs on asyncio, so blocking calls stall the event loop. Not recommended. |
| aiosqlite | SQLAlchemy async | If you have 20+ tables or complex joins. Massive overkill for ~5 tables. Adds complexity, ORM mental model, and migration tooling. |
| uv | pip + venv + pip-tools | If uv is not installable (extremely rare). pip works but is 10-100x slower and requires manual venv management. |
| uv | poetry | If you are already on a poetry-managed monorepo. For a new tool, uv is faster, simpler, and more standards-compliant (PEP 621). |
| Streamable HTTP | stdio transport | If only one client connects at a time. Stdio can't support two simultaneous clients (Claude Code + Codex). HTTP is the only viable transport for multi-client. |
| Streamable HTTP | SSE transport | SSE is deprecated in the MCP spec. Streamable HTTP is its successor. Do not use SSE for new projects. |
| ruff | flake8 + black + isort | If you need flake8 plugins that ruff hasn't re-implemented (rare). For typical projects, ruff replaces all three with one tool. |

---

## What NOT to Use

| Avoid | Why | Use Instead |
|-------|-----|-------------|
| `mcp` SDK directly (low-level) | FastMCP 1.0 was folded into `mcp.server.fastmcp`, but the standalone FastMCP 2.x has a much richer API. Using the bare SDK means reimplementing lifespan, context, dependency injection, and transport configuration manually. | `fastmcp>=2.14,<3` |
| FastMCP 3.0 RC in production | Release candidate as of 2026-02-14. May include breaking changes. The install docs say "pin to v2" for stability. | `fastmcp>=2.14,<3` |
| SSE transport | Deprecated by the MCP specification. Claude Code docs explicitly state "SSE transport is deprecated. Use HTTP servers instead." | `transport="streamable-http"` |
| SQLAlchemy / Tortoise ORM | Adds hundreds of KB of dependencies, migration tooling, and ORM complexity for a ~5 table database. Raw SQL with parameterized queries is sufficient and fully auditable. | `aiosqlite` + raw SQL |
| `sqlite3` stdlib (sync) | Blocks the asyncio event loop. Every synchronous database call freezes the HTTP transport, stalling all connected MCP clients. | `aiosqlite` |
| Docker / containers | This is a local dev tool, not a deployed service. Containers add startup latency and complexity. The broker should be a `uv run` command. | `uv run gsd-review-broker` |
| Flask / Django | Full web frameworks are overkill. FastMCP handles the HTTP transport internally via Starlette/uvicorn. Adding another web framework creates conflicts. | FastMCP's built-in HTTP transport |
| `asyncpg` / PostgreSQL | Adds a dependency on a running Postgres server. SQLite is file-based, zero-config, and aligns with the `.planning/` directory model. | `aiosqlite` + SQLite |
| pip (directly) | Slower, no lockfile, no automatic venv management. uv is the 2026 standard. | `uv` |
| poetry | Slower resolution, non-standard lockfile format, heavier than uv. For new projects in 2026, uv has won. | `uv` |
| Multiple SQLite write connections | Causes SQLITE_BUSY errors and potential data corruption. SQLite supports only one writer at a time. | Single `aiosqlite` connection via lifespan |

---

## Stack Patterns by Variant

**If only Claude Code connects (no Codex reviewer yet):**
- Still use Streamable HTTP transport (not stdio)
- Because: you'll add the second client later, and switching transport is a breaking change
- The HTTP endpoint also enables human observation via browser/curl

**If FastMCP 3.0 goes GA during development:**
- Evaluate upgrading from 2.x to 3.0
- The decorator API (`@mcp.tool()`) is unchanged
- New features (component versioning, authorization) may benefit the broker
- Wait for at least 3.0.1 (first patch) before adopting

**If the SQLite database grows beyond 100MB:**
- This should not happen (review threads are text, not binary)
- If it does: implement periodic archival of closed reviews
- Do NOT switch to PostgreSQL -- the `.planning/` locality constraint means SQLite is the right choice

**If a third client needs to connect:**
- Streamable HTTP already supports unlimited concurrent clients
- No architecture changes needed

---

## Version Compatibility

| Package | Compatible With | Notes |
|---------|-----------------|-------|
| fastmcp ~=2.14.5 | Python >=3.10, mcp >=1.26 | FastMCP 2.x depends on the `mcp` SDK internally. Both require Python >=3.10. |
| aiosqlite ~=0.22.1 | Python >=3.8, sqlite3 (stdlib) | No external native dependencies. Uses stdlib sqlite3 via threading. |
| uv >=0.10.3 | Python 3.8-3.14, all platforms | Standalone binary, no Python dependency for uv itself. |
| ruff >=0.15.0 | Python 3.9-3.14 | Standalone binary (Rust). No Python dependency for ruff itself. |
| pytest >=8.0 | Python >=3.8 | Works with all supported Python versions. |
| pytest-asyncio >=0.24 | pytest >=8.0, Python >=3.9 | Set `asyncio_mode = "auto"` in pyproject.toml config. |

---

## Sources

- [PyPI: fastmcp](https://pypi.org/project/fastmcp/) -- Version 2.14.5 (stable, 2026-02-03), 3.0.0rc2 (pre-release, 2026-02-14)
- [PyPI: mcp](https://pypi.org/project/mcp/) -- Version 1.26.0 (2026-01-24), official Anthropic SDK
- [FastMCP Documentation](https://gofastmcp.com/) -- Server context, tool patterns, transport configuration
- [FastMCP: Running Your Server](https://gofastmcp.com/deployment/running-server) -- Streamable HTTP setup
- [FastMCP: Context API](https://gofastmcp.com/servers/context) -- Dependency injection, logging, session state
- [FastMCP GitHub](https://github.com/jlowin/fastmcp) -- Source, issues, lifespan discussion (#1763)
- [Claude Code MCP Docs](https://code.claude.com/docs/en/mcp) -- `claude mcp add --transport http`, `.mcp.json` format
- [Codex MCP Docs](https://developers.openai.com/codex/mcp/) -- `config.toml` format, streamable HTTP `url` field
- [PyPI: aiosqlite](https://pypi.org/project/aiosqlite/) -- Version 0.22.1
- [SQLite WAL Documentation](https://sqlite.org/wal.html) -- Concurrency model, checkpointing
- [uv Documentation](https://docs.astral.sh/uv/) -- Version 0.10.3 (2026-02-16), project management
- [uv: Platform Support](https://docs.astral.sh/uv/reference/policies/platforms/) -- Windows, macOS, Linux support
- [Python Developer's Guide: Versions](https://devguide.python.org/versions/) -- 3.12-3.14 support timelines
- [Ruff Documentation](https://docs.astral.sh/ruff/) -- Configuration, pyproject.toml integration
- [PyPI: pytest-asyncio](https://pypi.org/project/pytest-asyncio/) -- Async test support

---
*Stack research for: MCP Review Broker Server (Python, local dev tool)*
*Researched: 2026-02-16*
