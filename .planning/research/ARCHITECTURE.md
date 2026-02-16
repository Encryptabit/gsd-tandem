# Architecture Research

**Domain:** Local MCP review broker (inter-agent code review via Model Context Protocol)
**Researched:** 2026-02-16
**Confidence:** HIGH

## Standard Architecture

### System Overview

```
┌─────────────────────────────────────────────────────────────────────────┐
│                         MCP Clients                                     │
│  ┌──────────────────────┐          ┌──────────────────────┐             │
│  │  Claude Code          │          │  Reviewer             │            │
│  │  (proposer)           │          │  (Codex / Claude /    │            │
│  │                       │          │   human MCP client)   │            │
│  │  - gsd-executor       │          │                       │            │
│  │  - gsd-planner        │          │  - claims reviews     │            │
│  │  - submits proposals  │          │  - posts messages     │            │
│  │  - polls for verdicts │          │  - submits verdicts   │            │
│  │  - applies patches    │          │  - supplies patches   │            │
│  └──────────┬───────────┘          └──────────┬───────────┘             │
│             │ POST /mcp                        │ POST /mcp              │
│             │ Mcp-Session-Id: aaa...           │ Mcp-Session-Id: bbb... │
└─────────────┼──────────────────────────────────┼────────────────────────┘
              │                                  │
              ▼                                  ▼
┌─────────────────────────────────────────────────────────────────────────┐
│                    FastMCP Streamable HTTP Server                        │
│                    http://127.0.0.1:8321/mcp                            │
│                                                                         │
│  ┌──────────────────────────────────────────────────────────────────┐   │
│  │                      Tool Layer                                  │   │
│  │  ┌──────────┐  ┌──────────────┐  ┌────────────┐  ┌───────────┐ │   │
│  │  │ Proposal │  │ Review Mgmt  │  │ Discussion │  │ Status /  │ │   │
│  │  │ Tools    │  │ Tools        │  │ Tools      │  │ Query     │ │   │
│  │  └────┬─────┘  └──────┬───────┘  └─────┬──────┘  └─────┬─────┘ │   │
│  └───────┼───────────────┼─────────────────┼───────────────┼───────┘   │
│          │               │                 │               │           │
│  ┌───────┴───────────────┴─────────────────┴───────────────┴───────┐   │
│  │                    Service Layer                                 │   │
│  │  ┌──────────────┐  ┌──────────────┐  ┌──────────────────────┐   │   │
│  │  │ ReviewService│  │ MessageService│  │ PatchService         │   │   │
│  │  │ (lifecycle)  │  │ (discussion) │  │ (diff storage/apply) │   │   │
│  │  └──────┬───────┘  └──────┬───────┘  └──────────┬───────────┘   │   │
│  └─────────┼────────────────┼──────────────────────┼───────────────┘   │
│            │                │                      │                   │
│  ┌─────────┴────────────────┴──────────────────────┴───────────────┐   │
│  │                    Data Layer (aiosqlite)                        │   │
│  │                                                                  │   │
│  │  .planning/codex_review_broker.sqlite3                           │   │
│  │  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌──────────┐           │   │
│  │  │ reviews  │ │ messages │ │ verdicts │ │ patches  │           │   │
│  │  └──────────┘ └──────────┘ └──────────┘ └──────────┘           │   │
│  └──────────────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────────────┘
```

### Component Responsibilities

| Component | Responsibility | Typical Implementation |
|-----------|----------------|------------------------|
| FastMCP Server | HTTP endpoint, session management, tool dispatch | `FastMCP("gsd-review-broker")` with `transport="streamable-http"` on `127.0.0.1:8321` |
| Tool Layer | Expose MCP tools, validate inputs, route to services | `@mcp.tool` decorated async functions grouped by tag |
| Service Layer | Business logic, state machine transitions, validation | Pure async Python classes, no MCP dependency |
| Data Layer | SQLite persistence, schema migrations, queries | `aiosqlite` connection pool via FastMCP lifespan context |
| Proposer Client | Claude Code and sub-agents submitting proposals | MCP client calling `mcp__gsdreview__*` tools |
| Reviewer Client | Codex/Claude/human claiming and reviewing proposals | MCP client calling `mcp__gsdreview__*` tools |

## Recommended Project Structure

```
tools/gsd-review-broker/
├── pyproject.toml              # Package metadata, dependencies
├── README.md                   # Usage instructions
├── src/
│   └── gsd_review_broker/
│       ├── __init__.py
│       ├── server.py           # FastMCP server init, lifespan, run()
│       ├── tools/
│       │   ├── __init__.py
│       │   ├── proposals.py    # submit_proposal, get_proposal
│       │   ├── reviews.py      # claim_review, submit_verdict, close_review
│       │   ├── discussion.py   # post_message, get_messages
│       │   ├── patches.py      # submit_patch, get_patch
│       │   └── status.py       # list_reviews, get_review_status, list_pending
│       ├── services/
│       │   ├── __init__.py
│       │   ├── review.py       # ReviewService — lifecycle state machine
│       │   ├── message.py      # MessageService — threaded discussion
│       │   └── patch.py        # PatchService — diff storage and retrieval
│       ├── db/
│       │   ├── __init__.py
│       │   ├── connection.py   # aiosqlite connection via lifespan
│       │   ├── schema.py       # DDL statements, migrations
│       │   └── queries.py      # Named query functions
│       └── models.py           # Pydantic models for Review, Message, Verdict, Patch
└── tests/
    ├── conftest.py             # Shared fixtures, in-memory SQLite
    ├── test_tools.py           # Tool-level integration tests
    ├── test_services.py        # Service unit tests
    └── test_db.py              # Schema and query tests
```

### Structure Rationale

- **tools/**: One module per tool group. Each file registers tools with `@mcp.tool` using tags for logical grouping. Keeps tool definitions thin -- they validate input then delegate to services.
- **services/**: Business logic isolated from MCP transport. The ReviewService owns the state machine. Services are testable without running an MCP server.
- **db/**: Database concerns isolated. `connection.py` provides the lifespan-managed `aiosqlite` connection. `schema.py` owns DDL. `queries.py` provides named async functions so SQL is never inline in services.
- **models.py**: Pydantic models define the data contracts. Used for validation at tool boundaries and as return types from services.

## Architectural Patterns

### Pattern 1: FastMCP Lifespan for Database Connection

**What:** Use FastMCP's `lifespan` parameter to initialize the `aiosqlite` connection once at server startup. The connection persists across all requests and is closed on shutdown.
**When to use:** Always. This is the correct pattern for any server-scoped resource in FastMCP.
**Trade-offs:** Single connection means serialized writes (acceptable for SQLite). Simple. No connection pool needed for local-only use.

**Example:**
```python
from contextlib import asynccontextmanager
from collections.abc import AsyncIterator
from dataclasses import dataclass
from fastmcp import FastMCP
import aiosqlite

@dataclass
class AppContext:
    db: aiosqlite.Connection

@asynccontextmanager
async def broker_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    db = await aiosqlite.connect(".planning/codex_review_broker.sqlite3")
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await _ensure_schema(db)
    try:
        yield AppContext(db=db)
    finally:
        await db.close()

mcp = FastMCP(
    "gsd-review-broker",
    lifespan=broker_lifespan,
)
```

**Confidence:** HIGH -- this pattern is documented in FastMCP official docs and the MCP Python SDK examples.

### Pattern 2: Tool-per-Action with Tag Grouping

**What:** Each MCP tool maps to a single discrete action. Tools are grouped by tag (e.g., `proposal`, `review`, `discussion`, `status`) rather than by namespace prefix. Tool names use a consistent `verb_noun` convention.
**When to use:** Always in FastMCP. Tags enable selective visibility and filtering.
**Trade-offs:** More tools means more tokens in tool discovery listings. Mitigation: keep tool count under 15 and descriptions concise.

**Example:**
```python
@mcp.tool(tags={"proposal"})
async def submit_proposal(
    intent: str,
    diff: str,
    agent_type: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    ctx: Context = CurrentContext(),
) -> dict:
    """Submit a code change proposal with intent and unified diff for review."""
    db = ctx.lifespan_context.db
    review_service = ReviewService(db)
    review = await review_service.create(intent, diff, agent_type, phase, plan, task)
    return review.model_dump()
```

**Confidence:** HIGH -- FastMCP docs explicitly support `tags` parameter on `@mcp.tool`.

### Pattern 3: State Machine in Service Layer

**What:** The ReviewService enforces a deterministic state machine for review lifecycle. State transitions are validated before execution. Invalid transitions raise clear errors.
**When to use:** Any time you have a lifecycle with defined states and transitions.
**Trade-offs:** More upfront design, but prevents impossible states and makes debugging trivial.

**State machine:**
```
                   ┌─────────┐
                   │ created │
                   └────┬────┘
                        │ claim_review()
                        ▼
                   ┌─────────┐
              ┌────│ claimed  │────┐
              │    └─────────┘    │
              │ post_message()    │ submit_verdict(approved)
              ▼                   │
        ┌──────────────┐         │
        │ in_discussion │─────────┤
        └──────┬───────┘         │
               │                  │
               │ submit_verdict() │
               ▼                  ▼
        ┌──────────┐       ┌──────────┐
        │ rejected │       │ approved │
        └────┬─────┘       └────┬─────┘
             │                  │
             │ close_review()   │ close_review()
             ▼                  ▼
        ┌──────────────────────────┐
        │         closed           │
        └──────────────────────────┘
```

**Valid transitions:**

| From | To | Trigger | Who |
|------|----|---------|-----|
| `created` | `claimed` | `claim_review()` | reviewer |
| `claimed` | `in_discussion` | `post_message()` | either |
| `claimed` | `approved` | `submit_verdict(approved)` | reviewer |
| `claimed` | `rejected` | `submit_verdict(rejected)` | reviewer |
| `in_discussion` | `in_discussion` | `post_message()` (stays in same state) | either |
| `in_discussion` | `approved` | `submit_verdict(approved)` | reviewer |
| `in_discussion` | `rejected` | `submit_verdict(rejected)` | reviewer |
| `approved` | `closed` | `close_review()` | proposer |
| `rejected` | `created` | resubmit (new proposal, links to previous) | proposer |
| `rejected` | `closed` | `close_review()` | proposer |

**Example:**
```python
VALID_TRANSITIONS = {
    "created":       {"claimed"},
    "claimed":       {"in_discussion", "approved", "rejected"},
    "in_discussion": {"in_discussion", "approved", "rejected"},
    "approved":      {"closed"},
    "rejected":      {"closed", "created"},  # created = resubmit as new
}

class ReviewService:
    def __init__(self, db: aiosqlite.Connection):
        self.db = db

    async def transition(self, review_id: str, new_status: str) -> Review:
        review = await self._get(review_id)
        if new_status not in VALID_TRANSITIONS[review.status]:
            raise ValueError(
                f"Cannot transition from '{review.status}' to '{new_status}'"
            )
        await self._update_status(review_id, new_status)
        return await self._get(review_id)
```

**Confidence:** HIGH -- standard state machine pattern, no external dependency.

## Data Flow

### Proposal Flow (Happy Path)

```
Claude Code (proposer)                    Broker                         Reviewer
       │                                    │                               │
       │  submit_proposal(intent, diff)     │                               │
       │ ──────────────────────────────────► │                               │
       │                                    │  creates review (status=created)
       │  ◄── { review_id, status }         │                               │
       │                                    │                               │
       │  get_review_status(review_id)      │                               │
       │ ──────────────────────────────────► │                               │
       │  ◄── { status: "created" }         │                               │
       │                                    │                               │
       │         (polls periodically)       │   list_pending()              │
       │                                    │ ◄──────────────────────────── │
       │                                    │  ──► [{ review_id, intent }]  │
       │                                    │                               │
       │                                    │   claim_review(review_id)     │
       │                                    │ ◄──────────────────────────── │
       │                                    │   status → claimed            │
       │                                    │                               │
       │                                    │   submit_verdict(approved)    │
       │                                    │ ◄──────────────────────────── │
       │                                    │   status → approved           │
       │                                    │                               │
       │  get_review_status(review_id)      │                               │
       │ ──────────────────────────────────► │                               │
       │  ◄── { status: "approved" }        │                               │
       │                                    │                               │
       │  (applies diff locally)            │                               │
       │                                    │                               │
       │  close_review(review_id)           │                               │
       │ ──────────────────────────────────► │                               │
       │                                    │   status → closed             │
```

### Back-and-Forth Discussion Flow

```
Claude Code                              Broker                          Reviewer
       │                                    │                               │
       │  submit_proposal(intent, diff_v1)  │                               │
       │ ──────────────────────────────────► │                               │
       │                                    │                               │
       │                                    │   claim_review(review_id)     │
       │                                    │ ◄──────────────────────────── │
       │                                    │                               │
       │                                    │   post_message(               │
       │                                    │     "change X in file Y",     │
       │                                    │     role="reviewer")          │
       │                                    │ ◄──────────────────────────── │
       │                                    │   status → in_discussion      │
       │                                    │                               │
       │  get_messages(review_id)           │                               │
       │ ──────────────────────────────────► │                               │
       │  ◄── [{ role: reviewer, body }]    │                               │
       │                                    │                               │
       │  post_message(                     │                               │
       │    "counter-proposal: ...",        │                               │
       │    role="proposer")                │                               │
       │ ──────────────────────────────────► │                               │
       │                                    │                               │
       │                                    │   submit_patch(               │
       │                                    │     review_id,                │
       │                                    │     patch_diff)               │
       │                                    │ ◄──────────────────────────── │
       │                                    │                               │
       │  get_patch(review_id)              │                               │
       │ ──────────────────────────────────► │                               │
       │  ◄── { patch_diff }               │                               │
       │                                    │                               │
       │  post_message(                     │                               │
       │    "accepted reviewer patch",      │                               │
       │    role="proposer")                │                               │
       │ ──────────────────────────────────► │                               │
       │                                    │                               │
       │                                    │   submit_verdict(approved)    │
       │                                    │ ◄──────────────────────────── │
       │                                    │                               │
       │  (applies reviewer's patch)        │                               │
       │  close_review(review_id)           │                               │
       │ ──────────────────────────────────► │                               │
```

### Key Data Flows

1. **Proposal submission:** Proposer calls `submit_proposal` with intent description + unified diff. Broker creates a review row (status=`created`), stores the diff in the `patches` table, returns review_id. Proposer enters poll loop.
2. **Review claiming:** Reviewer calls `list_pending` to discover new reviews, then `claim_review(review_id)` to take ownership. Broker transitions to `claimed`.
3. **Discussion round-trip:** Either party calls `post_message` to add commentary. Reviewer can call `submit_patch` to attach an alternative diff. Proposer reads messages and patches, responds. Broker stays in `in_discussion`.
4. **Verdict:** Reviewer calls `submit_verdict(approved|rejected)`. If approved, proposer applies the final diff (original or reviewer's patch) and calls `close_review`. If rejected, proposer may resubmit with a new proposal that links to the original.
5. **Polling loop:** Proposer polls `get_review_status` on a 2-3 second interval. This is simple, predictable, and sufficient for localhost latency. See "Polling vs Notification" section below for detailed analysis.

## SQLite Schema Design

### Schema

```sql
-- Reviews: the core entity
CREATE TABLE reviews (
    id              TEXT PRIMARY KEY,          -- UUID v4
    status          TEXT NOT NULL DEFAULT 'created'
                    CHECK(status IN ('created','claimed','in_discussion',
                                     'approved','rejected','closed')),
    intent          TEXT NOT NULL,             -- what the proposer wants to do
    agent_type      TEXT NOT NULL,             -- e.g. 'gsd-executor', 'gsd-planner'
    phase           TEXT NOT NULL,             -- e.g. '3.2'
    plan            TEXT,                      -- plan name, nullable
    task            TEXT,                      -- task number, nullable
    claimed_by      TEXT,                      -- reviewer session identifier
    parent_id       TEXT REFERENCES reviews(id), -- links resubmissions to original
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_reviews_status ON reviews(status);
CREATE INDEX idx_reviews_parent ON reviews(parent_id);

-- Messages: threaded discussion on a review
CREATE TABLE messages (
    id              TEXT PRIMARY KEY,          -- UUID v4
    review_id       TEXT NOT NULL REFERENCES reviews(id),
    role            TEXT NOT NULL CHECK(role IN ('proposer','reviewer')),
    body            TEXT NOT NULL,             -- markdown text
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_messages_review ON messages(review_id);

-- Verdicts: formal approval or rejection
CREATE TABLE verdicts (
    id              TEXT PRIMARY KEY,          -- UUID v4
    review_id       TEXT NOT NULL REFERENCES reviews(id),
    decision        TEXT NOT NULL CHECK(decision IN ('approved','rejected')),
    reason          TEXT,                      -- optional explanation
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_verdicts_review ON verdicts(review_id);

-- Patches: unified diffs (original proposals and reviewer-supplied alternatives)
CREATE TABLE patches (
    id              TEXT PRIMARY KEY,          -- UUID v4
    review_id       TEXT NOT NULL REFERENCES reviews(id),
    role            TEXT NOT NULL CHECK(role IN ('proposer','reviewer')),
    diff            TEXT NOT NULL,             -- unified diff content
    description     TEXT,                      -- optional note about the patch
    created_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX idx_patches_review ON patches(review_id);
```

### Schema Rationale

- **Patches stored as TEXT in SQLite, not on filesystem.** Unified diffs are plain text, typically 1-50 KB. SQLite is 35% faster than filesystem for blobs under 100 KB (per SQLite's own benchmarks). Storing diffs in the database preserves transactional consistency -- a review, its messages, and its patches are always in a consistent state. No orphaned files, no path management, no cross-platform path issues.
- **Separate `verdicts` table** rather than a column on `reviews`. A review may be rejected then resubmitted, accumulating multiple verdicts over its lifetime. The separate table preserves full history.
- **Separate `patches` table** rather than embedding diff in `reviews`. A review starts with the proposer's diff, but the reviewer may submit alternative patches during discussion. Multiple patches per review is a first-class concept.
- **`parent_id` self-reference.** When a rejected proposal is resubmitted, the new review links to the original via `parent_id`. This preserves the full review chain for observability.
- **ISO 8601 timestamps as TEXT.** SQLite has no native datetime type. TEXT with `datetime('now')` is the standard SQLite pattern and sorts correctly.
- **UUID primary keys.** Both clients generate IDs independently. No auto-increment coordination needed across sessions.
- **WAL mode.** Set via `PRAGMA journal_mode=WAL` at connection time. Allows concurrent reads while a write is in progress -- critical when both proposer and reviewer are querying simultaneously.

**Confidence:** HIGH -- SQLite best practices, WAL mode, and TEXT blob storage are well-documented.

## Polling vs Notification Analysis

### Decision: Proposer polls, reviewer polls. No server-push needed.

**Rationale:**

| Approach | Complexity | Latency | Reliability |
|----------|-----------|---------|-------------|
| Proposer polls `get_review_status` every 2-3s | LOW | 2-3s avg | HIGH -- simple HTTP GET |
| MCP SSE stream for notifications | MEDIUM | <100ms | MEDIUM -- stream can disconnect |
| MCP `notifications/resources/list_changed` | HIGH | <100ms | LOW -- not all clients support it |

**Why polling wins for this use case:**

1. **Localhost latency is negligible.** A poll every 2-3 seconds on localhost costs microseconds of network time. The bottleneck is the reviewer thinking, not the transport.
2. **MCP notification support is inconsistent.** Claude Code and Codex may not support `notifications/resources/list_changed` or SSE GET streams reliably. Polling via tool calls is universally supported by every MCP client.
3. **Simplicity.** No SSE stream management, no reconnection logic, no message loss concerns. The proposer calls `get_review_status(review_id)` in a loop. If the status is still `created` or `claimed`, it waits and polls again.
4. **Back-pressure is natural.** If the reviewer is slow, the proposer just keeps polling. No growing queue of notifications.

**Implementation in the proposer (Claude Code side):**
```python
# This runs in Claude Code's agent, not in the broker
# The agent calls the MCP tool in a loop
while True:
    status = await call_tool("mcp__gsdreview__get_review_status", review_id=rid)
    if status["status"] in ("approved", "rejected"):
        break
    if status["status"] == "in_discussion":
        messages = await call_tool("mcp__gsdreview__get_messages", review_id=rid)
        # handle new messages from reviewer
    await sleep(3)
```

**For the reviewer:** Similarly polls `list_pending()` to discover new proposals. In practice, the reviewer (Codex, another Claude) will be prompted to check for pending reviews as part of its system instructions.

**Confidence:** HIGH -- polling is the proven pattern for MCP tool-based coordination. The MCP spec explicitly supports this model.

## MCP Tool Design

### Complete Tool Inventory

The server exposes 10 tools organized into 4 groups via tags:

#### Proposal Tools (tag: `proposal`)

| Tool | Caller | Purpose |
|------|--------|---------|
| `submit_proposal` | proposer | Create a new review with intent + diff |
| `get_proposal` | either | Retrieve proposal details (intent, original diff, metadata) |

#### Review Management Tools (tag: `review`)

| Tool | Caller | Purpose |
|------|--------|---------|
| `claim_review` | reviewer | Take ownership of a pending review |
| `submit_verdict` | reviewer | Approve or reject with reason |
| `close_review` | proposer | Mark an approved/rejected review as closed |

#### Discussion Tools (tag: `discussion`)

| Tool | Caller | Purpose |
|------|--------|---------|
| `post_message` | either | Add a message to the review thread |
| `get_messages` | either | Retrieve all messages for a review |
| `submit_patch` | reviewer | Attach an alternative unified diff |
| `get_patch` | either | Retrieve patches for a review |

#### Status/Query Tools (tag: `status`)

| Tool | Caller | Purpose |
|------|--------|---------|
| `list_reviews` | either | List reviews filtered by status, with pagination |

### Tool Design Principles

1. **One tool, one action.** No multi-purpose tools. `submit_proposal` does not also claim or approve.
2. **Role is implicit from context, not enforced.** The broker is reviewer-agnostic. It does not authenticate callers. The `role` field on messages/patches is self-declared. In a trusted localhost environment, this is acceptable.
3. **Return structured data.** Every tool returns a dict (JSON-serializable). Pydantic models are serialized via `.model_dump()`.
4. **Idempotent where possible.** `get_review_status`, `get_messages`, `get_proposal`, `get_patch` are pure reads. `claim_review` is idempotent if already claimed by the same session.
5. **Under 15 tools total.** LLM context windows have limits. 10 tools is manageable. Each tool has a clear, descriptive docstring that the LLM uses for tool selection.

**Confidence:** HIGH -- follows MCP tool design best practices from the official specification.

## Multi-Client Connection Model

### How Two MCP Clients Connect to the Same Server

The Streamable HTTP transport is specifically designed for multi-client scenarios. Here is how it works:

1. **Server starts once.** The broker runs as an independent process: `python -m gsd_review_broker --port 8321`. It binds to `127.0.0.1:8321` and serves the MCP endpoint at `/mcp`.

2. **Each client initializes independently.** Claude Code sends `POST /mcp` with an `InitializeRequest`. The server responds with `InitializeResult` and an `Mcp-Session-Id` header (e.g., `Mcp-Session-Id: aaa-111`). Codex/reviewer sends its own `POST /mcp` with `InitializeRequest` and gets a different session ID (e.g., `Mcp-Session-Id: bbb-222`).

3. **Session isolation via header.** All subsequent requests from each client include their `Mcp-Session-Id`. The server routes requests to the correct session context. Session state (via `ctx.set_state`/`ctx.get_state`) is isolated per client.

4. **Shared database, isolated sessions.** Both sessions read from and write to the same SQLite database. The database is the shared state. Session state is per-client. This is the correct boundary: review data is shared, client identity is per-session.

5. **Client configuration:**

For Claude Code (in `.claude/settings.json` or `.mcp.json`):
```json
{
  "mcpServers": {
    "gsdreview": {
      "type": "streamable-http",
      "url": "http://127.0.0.1:8321/mcp"
    }
  }
}
```

For the reviewer (Codex or another MCP client), the same URL is configured in its respective MCP client configuration.

**Confidence:** HIGH -- the MCP Streamable HTTP specification (2025-03-26 revision) explicitly defines this session management model via the `Mcp-Session-Id` header.

## Handling the Back-and-Forth

The back-and-forth between proposer and reviewer is modeled as a message thread on a review, not as separate tools or state machine states.

### Protocol

1. **Proposer submits** `submit_proposal(intent, diff)`. Review created at `created`.
2. **Reviewer claims** `claim_review(review_id)`. Review moves to `claimed`.
3. **Reviewer requests changes** `post_message(review_id, body="change X", role="reviewer")`. Review moves to `in_discussion`.
4. **Proposer reads** `get_messages(review_id)`. Sees reviewer's request.
5. **Proposer responds** `post_message(review_id, body="counter-proposal: ...", role="proposer")`. Stays in `in_discussion`.
6. **Reviewer supplies patch** `submit_patch(review_id, diff="...", role="reviewer")`. Stays in `in_discussion`.
7. **Proposer reads patch** `get_patch(review_id)`. Reviews the reviewer's diff.
8. **Proposer accepts or negotiates** `post_message(review_id, body="accepted", role="proposer")`.
9. **Reviewer approves** `submit_verdict(review_id, decision="approved")`. Review moves to `approved`.
10. **Proposer applies and closes** Applies the agreed diff locally, then calls `close_review(review_id)`.

### Key Design Choice: Messages Are Append-Only

Messages are never edited or deleted. The full conversation is always available. This is critical for observability -- the human developer can see exactly what was discussed and why a decision was made.

### Key Design Choice: Proposer Reviews Reviewer Patches

When the reviewer submits a patch via `submit_patch`, Claude does not blindly apply it. Claude reads the patch via `get_patch`, evaluates it against its own understanding, and either accepts it (posts an acceptance message, then the reviewer approves) or negotiates further (posts a counter-message). This prevents the reviewer from making changes that break Claude's mental model.

**Confidence:** HIGH -- this is application-level protocol design, not dependent on external libraries.

## File/Patch Handling

### Decision: All diffs stored in SQLite, not on disk

**Rationale:**

| Factor | SQLite TEXT | Filesystem |
|--------|-----------|-----------|
| Transactional consistency | YES -- review + patches atomic | NO -- can have orphaned files |
| Cross-platform paths | N/A -- no paths to manage | Must handle Windows vs Unix paths |
| Cleanup | DELETE review cascades | Must track and delete files separately |
| Performance (< 100KB) | 35% faster than filesystem (SQLite benchmarks) | Slower due to open/close syscalls |
| Queryability | Can search diffs with SQL LIKE | Must read files to search |
| Backup | Copy one .sqlite3 file | Copy directory tree |
| Size limit concern | Unified diffs rarely exceed 50KB | Not a concern |

**Implementation:**

- The `patches` table stores unified diffs as `TEXT`.
- Each review has at least one patch (the proposer's original diff, stored when `submit_proposal` is called).
- The reviewer may add additional patches via `submit_patch`.
- Patches are retrieved via `get_patch(review_id)` which returns all patches for a review, ordered by `created_at`.
- The proposer determines which patch to apply (original or reviewer's alternative) based on the discussion thread.

**Confidence:** HIGH -- SQLite's own documentation recommends in-database storage for blobs under 100KB. Unified diffs are text and typically well under this threshold.

## Scaling Considerations

| Scale | Architecture Adjustments |
|-------|--------------------------|
| 1 proposer + 1 reviewer (target) | Single SQLite, single server process. WAL mode for concurrent reads. Sufficient for all use cases. |
| Multiple sub-agents proposing simultaneously | WAL mode handles concurrent reads. Writes are serialized by SQLite but are fast (< 1ms for inserts). No adjustment needed. |
| Large diffs (> 100KB) | Extremely rare for per-task reviews. If encountered, SQLite handles TEXT columns up to 1GB. No adjustment needed. |
| Long-running reviews accumulating thousands of messages | Add pagination to `get_messages`. Already planned with LIMIT/OFFSET in queries. |

### Scaling Priorities

1. **First bottleneck: SQLite write contention.** With WAL mode, reads never block. Writes are serialized but sub-millisecond for typical inserts. Would only become an issue with dozens of concurrent sub-agents submitting simultaneously -- unlikely in practice.
2. **Second bottleneck: Tool call latency from polling.** If poll interval is too aggressive (< 1s), it wastes LLM tokens. If too conservative (> 10s), it adds perceived latency. 2-3 second interval is the sweet spot for localhost.

## Anti-Patterns

### Anti-Pattern 1: SSE Push Notifications for Review Status

**What people do:** Use MCP's SSE streaming (GET endpoint) to push status updates to the proposer in real-time.
**Why it's wrong:** Not all MCP clients reliably support long-lived SSE streams or `notifications/resources/list_changed`. Adds reconnection logic, message ordering concerns, and message loss risk. All for saving 2-3 seconds of latency on localhost.
**Do this instead:** Proposer polls `get_review_status` every 2-3 seconds. Simple, universal, reliable.

### Anti-Pattern 2: Storing Diffs on Filesystem with Path References in SQLite

**What people do:** Write diffs to `.planning/patches/review-id.diff` and store the path in SQLite.
**Why it's wrong:** Creates two sources of truth. File can be deleted without updating SQLite. Path separators differ across OS. Cleanup requires scanning both SQLite and filesystem. No transactional atomicity between file write and row insert.
**Do this instead:** Store diffs as TEXT in the `patches` table. SQLite is faster than filesystem for this size range, and consistency is guaranteed.

### Anti-Pattern 3: Multi-Purpose God Tools

**What people do:** Create a single `manage_review` tool with a `action` parameter that switches between create, claim, approve, reject, close.
**Why it's wrong:** LLMs are better at selecting from a list of clearly-named tools than at constructing the correct `action` parameter. Tool descriptions become vague. Error handling becomes a switch statement.
**Do this instead:** One tool per action. `submit_proposal`, `claim_review`, `submit_verdict`, `close_review`. Each with a focused description and typed parameters.

### Anti-Pattern 4: Enforcing Caller Identity in the Broker

**What people do:** Add authentication/authorization to verify that only "proposers" can submit proposals and only "reviewers" can claim reviews.
**Why it's wrong:** This is a trusted localhost environment. Both clients are controlled by the developer. Adding auth adds complexity without security benefit. The `role` field is self-declared and sufficient for message attribution.
**Do this instead:** Trust the callers. Use `role` fields for attribution, not authorization.

## Integration Points

### External Services

| Service | Integration Pattern | Notes |
|---------|---------------------|-------|
| Claude Code | MCP client via Streamable HTTP | Configured in `.mcp.json` or Claude settings |
| Codex / Reviewer | MCP client via Streamable HTTP | Same URL, different session |
| SQLite | `aiosqlite` via lifespan context | WAL mode, single connection |
| GSD .planning/ | Filesystem co-location | Broker DB lives alongside GSD artifacts |

### Internal Boundaries

| Boundary | Communication | Notes |
|----------|---------------|-------|
| Tool Layer → Service Layer | Direct async function calls | Tools import services, pass db from context |
| Service Layer → Data Layer | Async query functions | Services never write raw SQL -- use `db/queries.py` |
| Proposer → Broker | MCP tool calls over HTTP | Proposer never reads SQLite directly |
| Reviewer → Broker | MCP tool calls over HTTP | Reviewer never reads SQLite directly |
| Broker → Filesystem | None | Broker never touches working tree files. Claude applies diffs locally. |

## Build Order (Dependency Chain)

The components have clear dependency ordering. This informs the roadmap phase structure:

```
Phase 1: Foundation
  ├── db/schema.py          (DDL, migrations — everything depends on this)
  ├── db/connection.py      (aiosqlite lifespan — server depends on this)
  └── models.py             (Pydantic models — tools and services depend on this)
          │
Phase 2: Core Server + Basic Tools
  ├── server.py             (FastMCP init, lifespan, transport config)
  ├── services/review.py    (state machine — tools depend on this)
  ├── db/queries.py         (named queries — services depend on this)
  ├── tools/proposals.py    (submit_proposal, get_proposal)
  ├── tools/reviews.py      (claim_review, submit_verdict, close_review)
  └── tools/status.py       (list_reviews)
          │
Phase 3: Discussion + Patches
  ├── services/message.py   (message threading)
  ├── services/patch.py     (diff storage, retrieval)
  ├── tools/discussion.py   (post_message, get_messages)
  └── tools/patches.py      (submit_patch, get_patch)
          │
Phase 4: GSD Integration
  ├── commands/gsd/plan-phase.md    (add MCP tool permissions + checkpoint logic)
  ├── commands/gsd/execute-phase.md (add MCP tool permissions + checkpoint logic)
  ├── agents/gsd-executor.md        (add proposal submission behavior)
  └── agents/gsd-planner.md         (add proposal submission behavior)
          │
Phase 5: Polish + Observability
  ├── Error handling, retries, edge cases
  ├── Human observability (review chain history)
  └── End-to-end integration testing
```

**Build order rationale:**
- **Phase 1 first** because every other component depends on the schema and models.
- **Phase 2 before Phase 3** because the basic happy path (propose → claim → approve → close) must work before adding discussion/patch complexity.
- **Phase 3 before Phase 4** because GSD integration needs the full tool set available.
- **Phase 4 before Phase 5** because polish and observability require the complete system to exist.

## Sources

- [FastMCP Official Documentation — Server Context](https://gofastmcp.com/servers/context) (HIGH confidence)
- [FastMCP Official Documentation — Running Your Server](https://gofastmcp.com/deployment/running-server) (HIGH confidence)
- [FastMCP Official Documentation — Tools](https://gofastmcp.com/servers/tools) (HIGH confidence)
- [FastMCP GitHub Repository](https://github.com/jlowin/fastmcp) (HIGH confidence)
- [MCP Specification — Transports (2025-03-26)](https://modelcontextprotocol.io/specification/2025-03-26/basic/transports) (HIGH confidence)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) (HIGH confidence)
- [SQLite — Internal vs External BLOBs](https://sqlite.org/intern-v-extern-blob.html) (HIGH confidence)
- [SQLite — 35% Faster Than Filesystem](https://sqlite.org/fasterthanfs.html) (HIGH confidence)
- [MCP Architecture Overview](https://modelcontextprotocol.io/docs/learn/architecture) (HIGH confidence)
- [Cloudflare — Streamable HTTP MCP Servers](https://blog.cloudflare.com/streamable-http-mcp-servers-python/) (MEDIUM confidence)
- [MCP Long-Running Tasks Discussion](https://github.com/modelcontextprotocol/modelcontextprotocol/issues/982) (MEDIUM confidence)

---
*Architecture research for: MCP Review Broker (GSD Tandem)*
*Researched: 2026-02-16*
