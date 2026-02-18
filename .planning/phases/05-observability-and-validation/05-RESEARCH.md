# Phase 5: Observability and Validation - Research

**Researched:** 2026-02-17
**Domain:** SQLite audit logging, aggregate queries, MCP tool design for observability, E2E workflow validation
**Confidence:** HIGH

## Summary

Phase 5 adds three capabilities to the existing review broker: (1) an activity feed that shows all reviews with recent message previews and filtering by status/category, (2) a dedicated append-only `audit_events` table with lifecycle and message events, plus a stats tool for workflow health, and (3) end-to-end validation tests proving the full GSD pipeline works with broker mediation.

The implementation requires no new libraries. All work is within the existing Python/aiosqlite/FastMCP stack. The primary additions are: a new `audit_events` SQLite table (via schema migration), 4-5 new MCP tools (activity feed, audit history, stats, and possibly a review timeline tool), and a comprehensive E2E test suite. The existing 147 tests and codebase patterns (MockContext, dict error returns, BEGIN IMMEDIATE transactions, write_lock serialization) carry forward unchanged.

The CONTEXT.md decision to use "absolute ISO 8601 timestamps throughout" introduces a formatting concern: existing data uses SQLite's `datetime('now')` which produces `YYYY-MM-DD HH:MM:SS` (space separator, no timezone). New observability tools should format timestamps as ISO 8601 in their output (using SQLite's `strftime` or Python-side formatting), while maintaining backward compatibility with existing stored data.

**Primary recommendation:** Add the `audit_events` table and 4-5 specialized MCP tools (one per concern: activity feed, audit log, stats, and optionally review timeline), with audit event insertion wired into existing tool handlers. Write E2E tests that exercise the full review lifecycle and verify observability tools surface the right data.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Full activity feed by default -- show all reviews (active + recent completed), not just a summary dashboard
- Filterable by status (pending, claimed, approved, rejected, closed) AND category (plan_review, code_change, handoff, verification)
- Absolute ISO 8601 timestamps throughout (not relative "3 min ago")
- Each review in the feed includes a truncated preview of the most recent message, plus message count and last_message_at
- Dedicated append-only `audit_events` table -- not derived from querying existing tables
- Log state transitions AND message exchanges (propose, request_changes, counter-patch, verdict, etc.)
- Diff validation results and notification events are NOT logged (only lifecycle + messages)
- History returns everything -- no time-range filtering needed (data volume is small per project)
- Output format: structured dict/JSON, consistent with existing broker tool response pattern
- Specialized tools (more tools, simpler each) rather than few tools with parameters
- Include a stats tool with counts + timing: total reviews, approval/rejection rates, reviews by category, average time-to-verdict, average review duration, time in each state

### Claude's Discretion
- Exact tool naming and parameter signatures
- Whether a dedicated review_timeline tool is warranted vs composing existing tools
- Audit event schema details (columns, indexes)
- E2E validation test structure and assertion strategy

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope
</user_constraints>

## Standard Stack

No new libraries needed. All work uses the existing stack.

### Core (Existing - No Changes)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastMCP | >=2.14,<3 | MCP server framework | Already used; `@mcp.tool` decorator for new tools |
| aiosqlite | >=0.22,<1 | Async SQLite access | Already used; all DB operations follow established patterns |
| pytest / pytest-asyncio | >=8.0 / >=0.24 | Test framework | Already configured with `asyncio_mode = "auto"` |

### Supporting (Existing - No Changes)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| unidiff | >=0.7.5,<1 | Diff parsing | Not needed for Phase 5 (no new diff operations) |

### No New Dependencies

Phase 5 adds zero new pip packages. All functionality is built with SQL queries (aggregate functions, datetime arithmetic) and Python formatting. The stats tool uses SQLite's built-in `avg()`, `count()`, `julianday()`, and `strftime()` functions.

## Architecture Patterns

### Recommended File Structure

```
tools/gsd-review-broker/src/gsd_review_broker/
  db.py              # Add audit_events schema + migration
  models.py          # Add AuditEventType StrEnum
  tools.py           # Add new MCP tools (activity feed, audit, stats, timeline)
  audit.py           # NEW: Audit event recording helper functions

tools/gsd-review-broker/tests/
  test_activity_feed.py    # NEW: Activity feed tool tests
  test_audit.py            # NEW: Audit logging tests
  test_stats.py            # NEW: Stats tool tests
  test_e2e_workflow.py     # NEW: End-to-end workflow validation
```

### Pattern 1: Append-Only Audit Events Table

**What:** A dedicated `audit_events` table that records every lifecycle event and message exchange. Events are inserted as a side-effect of existing tool handlers, not via triggers (to keep control explicit and testable).

**When to use:** Every time a review changes state or a message is exchanged.

**Schema:**

```sql
CREATE TABLE IF NOT EXISTS audit_events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    review_id   TEXT NOT NULL REFERENCES reviews(id),
    event_type  TEXT NOT NULL,
    actor       TEXT,
    old_status  TEXT,
    new_status  TEXT,
    metadata    TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
);
CREATE INDEX IF NOT EXISTS idx_audit_review ON audit_events(review_id);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type);
```

**Event types to record:**

| Event Type | When Fired | Actor | old_status | new_status | Metadata |
|------------|-----------|-------|------------|------------|----------|
| `review_created` | create_review (new) | agent_type | null | pending | `{"intent": "...", "category": "..."}` |
| `review_revised` | create_review (revision) | agent_type | changes_requested | pending | `{"round": N}` |
| `review_claimed` | claim_review | reviewer_id | pending | claimed | null |
| `review_auto_rejected` | claim_review (bad diff) | broker-validator | pending | changes_requested | `{"reason": "..."}` |
| `verdict_submitted` | submit_verdict | reviewer | claimed | approved/changes_requested | `{"verdict": "...", "has_counter_patch": bool}` |
| `verdict_comment` | submit_verdict (comment) | reviewer | claimed | claimed | `{"reason": "..."}` |
| `review_closed` | close_review | system | approved/changes_requested | closed | null |
| `counter_patch_accepted` | accept_counter_patch | proposer | - | - | null |
| `counter_patch_rejected` | reject_counter_patch | proposer | - | - | null |
| `message_sent` | add_message | sender_role | - | - | `{"round": N, "body_preview": "first 100 chars..."}` |

**Why not SQL triggers:** Triggers would auto-fire but (a) are harder to test in isolation, (b) don't have access to application-level context like actor identity, and (c) can't easily include business metadata. Explicit insertion within the tool handlers is more testable and consistent with the codebase's approach.

**Confidence:** HIGH -- This is a straightforward append-only table following standard audit log patterns. The existing codebase already uses similar SQL patterns (INSERT with datetime, parameterized queries).

### Pattern 2: Audit Event Recording via Helper Module

**What:** A small `audit.py` module with a single `record_event()` async function that inserts into `audit_events`. This is called from existing tool handlers alongside existing logic, inside the same transaction when possible.

**Example:**

```python
# audit.py
async def record_event(
    db: aiosqlite.Connection,
    review_id: str,
    event_type: str,
    actor: str | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Insert an audit event. Call within an existing transaction."""
    metadata_json = json.dumps(metadata) if metadata else None
    await db.execute(
        """INSERT INTO audit_events
           (review_id, event_type, actor, old_status, new_status, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
        (review_id, event_type, actor, old_status, new_status, metadata_json),
    )
```

**Integration point:** Call `record_event()` inside the `BEGIN IMMEDIATE ... COMMIT` block in each tool handler, right before the COMMIT. This ensures audit events are atomic with the operation they record.

**Confidence:** HIGH -- Follows the same transactional pattern already used throughout `tools.py`.

### Pattern 3: Activity Feed via Subquery with Message Preview

**What:** The activity feed tool returns all reviews (optionally filtered) with a truncated preview of the most recent message, message count, and last_message_at. This is a single SQL query using correlated subqueries.

**Query pattern:**

```sql
SELECT
    r.id, r.status, r.intent, r.agent_type, r.phase, r.plan, r.task,
    r.priority, r.category, r.verdict_reason,
    strftime('%Y-%m-%dT%H:%M:%fZ', r.created_at) AS created_at,
    strftime('%Y-%m-%dT%H:%M:%fZ', r.updated_at) AS updated_at,
    (SELECT COUNT(*) FROM messages m WHERE m.review_id = r.id) AS message_count,
    (SELECT strftime('%Y-%m-%dT%H:%M:%fZ', MAX(m.created_at))
     FROM messages m WHERE m.review_id = r.id) AS last_message_at,
    (SELECT SUBSTR(m2.body, 1, 120)
     FROM messages m2 WHERE m2.review_id = r.id
     ORDER BY m2.rowid DESC LIMIT 1) AS last_message_preview
FROM reviews r
WHERE 1=1
  [AND r.status = ?]
  [AND r.category = ?]
ORDER BY r.updated_at DESC, r.id DESC
```

**Why correlated subqueries over JOINs:** With small data volumes (stated in CONTEXT.md: "data volume is small per project"), correlated subqueries are simpler to read and produce the exact one-row-per-review output needed. A LEFT JOIN with GROUP BY would also work but is more complex for the same result.

**Confidence:** HIGH -- Standard SQL pattern. SQLite handles correlated subqueries efficiently at small scale.

### Pattern 4: Stats via Aggregate Queries

**What:** A stats tool that computes workflow health metrics from the `reviews` and `audit_events` tables.

**Metrics and SQL patterns:**

```sql
-- Total reviews and status counts
SELECT
    COUNT(*) AS total_reviews,
    SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END) AS approved_count,
    SUM(CASE WHEN status = 'changes_requested' THEN 1 ELSE 0 END) AS rejected_count,
    SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed_count,
    SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending_count,
    SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END) AS claimed_count
FROM reviews;

-- Reviews by category
SELECT category, COUNT(*) AS count
FROM reviews
WHERE category IS NOT NULL
GROUP BY category;

-- Average time-to-verdict (seconds from created to first verdict event)
SELECT AVG(
    (julianday(ae.created_at) - julianday(r.created_at)) * 86400
) AS avg_seconds_to_verdict
FROM reviews r
JOIN audit_events ae ON ae.review_id = r.id
    AND ae.event_type = 'verdict_submitted'
WHERE ae.id = (
    SELECT MIN(ae2.id) FROM audit_events ae2
    WHERE ae2.review_id = r.id AND ae2.event_type = 'verdict_submitted'
);

-- Average review duration (seconds from created to closed)
SELECT AVG(
    (julianday(closed_event.created_at) - julianday(r.created_at)) * 86400
) AS avg_seconds_to_close
FROM reviews r
JOIN audit_events closed_event ON closed_event.review_id = r.id
    AND closed_event.event_type = 'review_closed'
WHERE r.status = 'closed';
```

**Why `julianday() * 86400` for duration:** SQLite's `julianday()` returns fractional days. Multiplying by 86400 converts to seconds. This is the standard way to compute time differences in SQLite. The `unixepoch()` function (available since SQLite 3.38.0, 2022) could also be used but `julianday()` is more portable.

**Confidence:** HIGH -- Built-in SQLite aggregate functions (`AVG`, `COUNT`, `SUM`, `CASE`) are well-documented and work correctly with the existing schema.

### Pattern 5: ISO 8601 Timestamp Formatting

**What:** The CONTEXT.md requires "Absolute ISO 8601 timestamps throughout." Existing data uses `datetime('now')` which produces `YYYY-MM-DD HH:MM:SS` (no T, no timezone). New tools must format output as ISO 8601.

**Approach:** Format timestamps at the SQL query level for new tools using `strftime('%Y-%m-%dT%H:%M:%fZ', column)`. This converts existing stored values to ISO 8601 format in the query output without modifying stored data.

For the new `audit_events` table, store timestamps natively in ISO 8601 format using `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` as the DEFAULT.

**Backward compatibility:** Existing tools (create_review, list_reviews, etc.) continue using `datetime('now')` format. Only the new Phase 5 observability tools format output as ISO 8601. This avoids breaking existing tool consumers.

**Confidence:** HIGH -- SQLite's `strftime()` handles this conversion cleanly. The `%f` specifier includes fractional seconds (millisecond precision).

### Pattern 6: Review Timeline (Discretionary)

**What:** A dedicated `get_review_timeline` tool that returns a chronological sequence of all events for a single review: creation, claims, messages, verdicts, counter-patches, and closure.

**Recommendation: YES, implement it.** The value over composing `get_proposal` + `get_discussion` is significant:
1. `get_proposal` and `get_discussion` don't show state transitions (when was it claimed? when was verdict submitted?)
2. The audit_events table provides the timeline data naturally -- one query returns the complete story
3. Composing existing tools requires multiple round-trips and manual interleaving
4. A timeline view is the most natural way to understand "what happened to this review?"

**Query pattern:**

```sql
SELECT
    event_type, actor, old_status, new_status, metadata,
    strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AS timestamp
FROM audit_events
WHERE review_id = ?
ORDER BY id ASC
```

**Confidence:** HIGH -- Simple query on the audit table. The value proposition is clear.

### Anti-Patterns to Avoid

- **Derived audit from existing tables:** The CONTEXT.md explicitly says "Dedicated append-only audit_events table -- not derived from querying existing tables." Do NOT try to reconstruct event history from the reviews and messages tables.
- **Trigger-based audit logging:** While common in general SQLite patterns, triggers hide business logic and are harder to test with the MockContext pattern. Use explicit `record_event()` calls.
- **Relative timestamps:** The CONTEXT.md says "not relative '3 min ago'." Always return absolute ISO 8601 timestamps.
- **Modifying stored timestamp format:** Do NOT alter how existing tools store timestamps. Only format output from new tools.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Duration calculations | Python datetime arithmetic | SQLite `julianday() * 86400` | Keeps computation in SQL, avoids roundtrips |
| ISO 8601 formatting | Python `datetime.isoformat()` | SQLite `strftime('%Y-%m-%dT%H:%M:%fZ', col)` | Formats at query level, no Python post-processing |
| Message count per review | Python loop counting | SQL `COUNT(*)` subquery | Single query, no N+1 problem |
| Event ordering | Python sort | SQL `ORDER BY id ASC` | AUTOINCREMENT id guarantees insertion order |
| Approval rate calculation | Python division | SQL `SUM(CASE WHEN...)/COUNT(*)` | One query, handles edge cases (zero reviews) |
| Last message preview | Python truncation after full fetch | SQL `SUBSTR(body, 1, 120)` | Avoids fetching full message text for preview |

**Key insight:** SQLite's built-in functions (`strftime`, `julianday`, `SUBSTR`, aggregate functions) handle all the computation needed for observability. Keep logic in SQL for simplicity and efficiency.

## Common Pitfalls

### Pitfall 1: Audit Events Not Atomic with Operations

**What goes wrong:** If `record_event()` is called outside the transaction (after COMMIT), a crash between COMMIT and audit insertion loses the audit event, creating gaps in the audit log.

**Why it happens:** The temptation to add audit logging as an afterthought, outside the existing `BEGIN IMMEDIATE ... COMMIT` block.

**How to avoid:** Always call `record_event()` inside the same `BEGIN IMMEDIATE ... COMMIT` transaction as the operation being audited. The audit insert is a simple INSERT and adds negligible overhead.

**Warning signs:** Audit events that don't match the review state (e.g., a review is closed but no `review_closed` audit event exists).

### Pitfall 2: Timestamp Format Mismatch

**What goes wrong:** Existing `datetime('now')` produces `YYYY-MM-DD HH:MM:SS` but new tools output ISO 8601 `YYYY-MM-DDTHH:MM:SS.sssZ`. Duration calculations using `julianday()` work with both formats, but string comparisons may fail if formats are mixed in the same query.

**Why it happens:** The codebase has 4 phases of existing timestamp storage in one format, and Phase 5 introduces a different format requirement.

**How to avoid:**
1. New `audit_events` table uses ISO 8601 natively (via `strftime` in DEFAULT)
2. When querying across tables (reviews + audit_events), use `julianday()` for comparisons (it parses both formats)
3. Format all output from new tools using `strftime('%Y-%m-%dT%H:%M:%fZ', column)` regardless of stored format

**Warning signs:** Incorrect duration calculations or sorting anomalies when comparing timestamps from different tables.

### Pitfall 3: Activity Feed N+1 Query Pattern

**What goes wrong:** Fetching all reviews, then for each review separately querying messages for count and last_message -- results in N+1 queries.

**Why it happens:** The natural Python approach is a loop with per-review queries.

**How to avoid:** Use correlated subqueries in a single SQL query (as shown in Pattern 3). SQLite executes these efficiently for small datasets.

**Warning signs:** Activity feed tool taking noticeably longer than other tools, especially as review count grows.

### Pitfall 4: Stats Tool Division by Zero

**What goes wrong:** Calculating approval rate (approved / total) when total is 0, or average duration when no reviews have completed.

**Why it happens:** Edge case on fresh/empty databases.

**How to avoid:** Use `NULLIF` or `CASE WHEN` guards:
```sql
-- Safe approval rate
CASE WHEN COUNT(*) > 0
    THEN ROUND(100.0 * SUM(CASE WHEN status IN ('approved', 'closed') THEN 1 ELSE 0 END) / COUNT(*), 1)
    ELSE NULL
END AS approval_rate_pct
```
Return `null` for metrics that can't be computed, not 0 (which would be misleading).

**Warning signs:** Division-by-zero errors in stats queries, or stats showing 0% when they should show "no data."

### Pitfall 5: E2E Tests Depending on Real Broker Server

**What goes wrong:** E2E tests that require a running broker server process make the test suite fragile and slow.

**Why it happens:** The desire to test "truly end-to-end" leads to tests that start the server process.

**How to avoid:** E2E tests should use the same MockContext/in-memory SQLite pattern as existing tests. The "end-to-end" aspect is testing the full lifecycle sequence (create -> claim -> message -> verdict -> close) and verifying that observability tools (activity feed, audit log, stats) correctly reflect the state after each step. This validates the complete data flow without needing a running server.

**Warning signs:** Tests that use `subprocess` to start the server, or tests that fail when port 8321 is busy.

### Pitfall 6: Forgetting to Wire Audit Events into ALL Existing Handlers

**What goes wrong:** Only some tool handlers get audit event recording, leading to incomplete audit trails.

**Why it happens:** The audit wiring is spread across many functions in `tools.py` and it's easy to miss one.

**How to avoid:** Make a checklist of every state-changing operation in `tools.py` and verify each has an audit event:
- `create_review` (new): `review_created`
- `create_review` (revision): `review_revised`
- `claim_review` (success): `review_claimed`
- `claim_review` (auto-reject): `review_auto_rejected`
- `submit_verdict` (approved): `verdict_submitted`
- `submit_verdict` (changes_requested): `verdict_submitted`
- `submit_verdict` (comment): `verdict_comment`
- `close_review`: `review_closed`
- `accept_counter_patch`: `counter_patch_accepted`
- `reject_counter_patch`: `counter_patch_rejected`
- `add_message`: `message_sent`

**Warning signs:** Timeline tool showing gaps in the review history.

## Code Examples

### Example 1: Schema Migration for audit_events

```python
# In db.py SCHEMA_MIGRATIONS list, add:
SCHEMA_MIGRATIONS: list[str] = [
    # ... existing Phase 2-4 migrations ...
    # Phase 5 migrations -- audit_events table
    """CREATE TABLE IF NOT EXISTS audit_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id   TEXT NOT NULL REFERENCES reviews(id),
        event_type  TEXT NOT NULL,
        actor       TEXT,
        old_status  TEXT,
        new_status  TEXT,
        metadata    TEXT,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_review ON audit_events(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)",
]
```

Note: Using `CREATE TABLE IF NOT EXISTS` inside migrations (not `ALTER TABLE`) since this is a new table. The existing `ensure_schema` function runs migrations with idempotent error handling for duplicate columns; for CREATE TABLE IF NOT EXISTS, the statement itself is idempotent.

### Example 2: Audit Event Helper Module

```python
# audit.py
from __future__ import annotations
import json
import aiosqlite

async def record_event(
    db: aiosqlite.Connection,
    review_id: str,
    event_type: str,
    actor: str | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an audit event within the current transaction."""
    metadata_json = json.dumps(metadata) if metadata else None
    await db.execute(
        """INSERT INTO audit_events
           (review_id, event_type, actor, old_status, new_status, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
        (review_id, event_type, actor, old_status, new_status, metadata_json),
    )
```

### Example 3: Wiring Audit into create_review (New Review Path)

```python
# In tools.py, inside the create_review new review flow, before COMMIT:
from gsd_review_broker.audit import record_event

# ... existing INSERT INTO reviews ...

await record_event(
    app.db,
    review_id=new_review_id,
    event_type="review_created",
    actor=agent_type,
    old_status=None,
    new_status=str(ReviewStatus.PENDING),
    metadata={"intent": intent, "category": category},
)
await app.db.execute("COMMIT")
```

### Example 4: Activity Feed Tool

```python
@mcp.tool
async def get_activity_feed(
    status: str | None = None,
    category: str | None = None,
    ctx: Context = None,
) -> dict:
    """Get a live activity feed of all reviews with message previews.

    Returns all reviews sorted by most recently updated. Each entry includes
    a truncated preview of the most recent message, total message count,
    and the timestamp of the last message.

    Optionally filter by status and/or category.
    """
    app: AppContext = ctx.lifespan_context
    conditions: list[str] = []
    params: list[str] = []
    if status is not None:
        conditions.append("r.status = ?")
        params.append(status)
    if category is not None:
        conditions.append("r.category = ?")
        params.append(category)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    cursor = await app.db.execute(
        f"""SELECT
            r.id, r.status, r.intent, r.agent_type, r.phase, r.plan, r.task,
            r.priority, r.category, r.claimed_by, r.verdict_reason,
            strftime('%Y-%m-%dT%H:%M:%fZ', r.created_at) AS created_at,
            strftime('%Y-%m-%dT%H:%M:%fZ', r.updated_at) AS updated_at,
            (SELECT COUNT(*) FROM messages m WHERE m.review_id = r.id) AS message_count,
            (SELECT strftime('%Y-%m-%dT%H:%M:%fZ', MAX(m.created_at))
             FROM messages m WHERE m.review_id = r.id) AS last_message_at,
            (SELECT SUBSTR(m2.body, 1, 120)
             FROM messages m2 WHERE m2.review_id = r.id
             ORDER BY m2.rowid DESC LIMIT 1) AS last_message_preview
        FROM reviews r
        {where_clause}
        ORDER BY r.updated_at DESC, r.id DESC""",
        params,
    )
    rows = await cursor.fetchall()
    # ... format as list of dicts ...
```

### Example 5: Stats Tool

```python
@mcp.tool
async def get_review_stats(ctx: Context = None) -> dict:
    """Get workflow health statistics for the broker.

    Returns total reviews, approval/rejection rates, reviews by category,
    average time-to-verdict, and average review duration.
    """
    app: AppContext = ctx.lifespan_context

    # Status counts
    cursor = await app.db.execute("""
        SELECT
            COUNT(*) AS total,
            SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) AS pending,
            SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END) AS claimed,
            SUM(CASE WHEN status IN ('approved', 'closed') THEN 1 ELSE 0 END) AS approved,
            SUM(CASE WHEN status = 'changes_requested' THEN 1 ELSE 0 END) AS rejected,
            SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) AS closed
        FROM reviews
    """)
    counts = dict(await cursor.fetchone())

    # Approval rate (safe division)
    total = counts["total"]
    approval_rate = None
    if total > 0:
        # Count reviews that reached approved (including closed-after-approved)
        cursor = await app.db.execute("""
            SELECT COUNT(DISTINCT review_id) FROM audit_events
            WHERE event_type = 'verdict_submitted'
            AND json_extract(metadata, '$.verdict') = 'approved'
        """)
        approved_verdicts = (await cursor.fetchone())[0]
        cursor = await app.db.execute("""
            SELECT COUNT(DISTINCT review_id) FROM audit_events
            WHERE event_type = 'verdict_submitted'
        """)
        total_verdicts = (await cursor.fetchone())[0]
        if total_verdicts > 0:
            approval_rate = round(100.0 * approved_verdicts / total_verdicts, 1)

    # Category breakdown
    cursor = await app.db.execute("""
        SELECT COALESCE(category, 'uncategorized') AS cat, COUNT(*) AS count
        FROM reviews GROUP BY cat
    """)
    by_category = {row[0]: row[1] for row in await cursor.fetchall()}

    # Average time-to-verdict (seconds)
    cursor = await app.db.execute("""
        SELECT AVG(
            (julianday(ae.created_at) - julianday(r.created_at)) * 86400
        ) AS avg_seconds
        FROM reviews r
        JOIN audit_events ae ON ae.review_id = r.id
            AND ae.event_type = 'verdict_submitted'
        WHERE ae.id = (
            SELECT MIN(ae2.id) FROM audit_events ae2
            WHERE ae2.review_id = r.id AND ae2.event_type = 'verdict_submitted'
        )
    """)
    avg_to_verdict = (await cursor.fetchone())[0]

    # Average review duration (created to closed, seconds)
    cursor = await app.db.execute("""
        SELECT AVG(
            (julianday(ae.created_at) - julianday(r.created_at)) * 86400
        ) AS avg_seconds
        FROM reviews r
        JOIN audit_events ae ON ae.review_id = r.id
            AND ae.event_type = 'review_closed'
    """)
    avg_duration = (await cursor.fetchone())[0]

    return {
        "total_reviews": total,
        "by_status": {
            "pending": counts["pending"],
            "claimed": counts["claimed"],
            "approved": counts["approved"],
            "changes_requested": counts["rejected"],
            "closed": counts["closed"],
        },
        "by_category": by_category,
        "approval_rate_pct": approval_rate,
        "avg_time_to_verdict_seconds": round(avg_to_verdict, 1) if avg_to_verdict else None,
        "avg_review_duration_seconds": round(avg_duration, 1) if avg_duration else None,
    }
```

### Example 6: Review Timeline Tool

```python
@mcp.tool
async def get_review_timeline(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Get the complete chronological timeline for a single review.

    Returns all audit events in order: creation, claims, messages,
    verdicts, counter-patches, and closure. Each event includes
    its type, actor, status change, and timestamp.
    """
    app: AppContext = ctx.lifespan_context

    # Verify review exists
    cursor = await app.db.execute(
        "SELECT id, intent, status, category FROM reviews WHERE id = ?",
        (review_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"error": f"Review not found: {review_id}"}

    cursor = await app.db.execute(
        """SELECT event_type, actor, old_status, new_status, metadata,
                  strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AS timestamp
           FROM audit_events
           WHERE review_id = ?
           ORDER BY id ASC""",
        (review_id,),
    )
    events = []
    for event_row in await cursor.fetchall():
        event = {
            "event_type": event_row["event_type"],
            "actor": event_row["actor"],
            "timestamp": event_row["timestamp"],
        }
        if event_row["old_status"]:
            event["old_status"] = event_row["old_status"]
        if event_row["new_status"]:
            event["new_status"] = event_row["new_status"]
        if event_row["metadata"]:
            try:
                event["metadata"] = json.loads(event_row["metadata"])
            except (json.JSONDecodeError, TypeError):
                event["metadata"] = event_row["metadata"]
        events.append(event)

    return {
        "review_id": review_id,
        "intent": row["intent"],
        "current_status": row["status"],
        "category": row["category"],
        "events": events,
        "event_count": len(events),
    }
```

### Example 7: E2E Test Pattern

```python
class TestEndToEndWorkflow:
    """End-to-end tests proving the full review lifecycle works
    and observability tools correctly surface the data."""

    async def test_full_lifecycle_with_audit_trail(self, ctx: MockContext) -> None:
        """Create -> claim -> message -> approve -> close, then verify
        activity feed, audit log, and stats all reflect the lifecycle."""
        # Step 1: Create
        created = await create_review.fn(
            intent="implement feature X",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="3",
            plan="03-01",
            task="2",
            category="code_change",
            ctx=ctx,
        )
        review_id = created["review_id"]

        # Step 2: Activity feed shows pending review
        feed = await get_activity_feed.fn(status="pending", ctx=ctx)
        assert len(feed["reviews"]) == 1
        assert feed["reviews"][0]["id"] == review_id

        # Step 3: Claim
        claimed = await claim_review.fn(
            review_id=review_id, reviewer_id="reviewer-1", ctx=ctx
        )
        assert claimed["status"] == "claimed"

        # Step 4: Message exchange
        await add_message.fn(
            review_id=review_id, sender_role="reviewer",
            body="Looks good overall, one question...", ctx=ctx
        )

        # Step 5: Approve
        await submit_verdict.fn(
            review_id=review_id, verdict="approved",
            reason="LGTM", ctx=ctx
        )

        # Step 6: Close
        await close_review.fn(review_id=review_id, ctx=ctx)

        # VERIFY: Timeline shows complete history
        timeline = await get_review_timeline.fn(review_id=review_id, ctx=ctx)
        event_types = [e["event_type"] for e in timeline["events"]]
        assert "review_created" in event_types
        assert "review_claimed" in event_types
        assert "message_sent" in event_types
        assert "verdict_submitted" in event_types
        assert "review_closed" in event_types

        # VERIFY: Stats reflect the completed review
        stats = await get_review_stats.fn(ctx=ctx)
        assert stats["total_reviews"] == 1
        assert stats["by_category"]["code_change"] == 1
        assert stats["approval_rate_pct"] is not None
```

## Recommended Tool Names

Based on the "specialized tools" decision and consistency with existing naming:

| Tool Name | Purpose | Parameters |
|-----------|---------|------------|
| `get_activity_feed` | Full activity feed with message previews | `status?: str, category?: str` |
| `get_audit_log` | Append-only audit event history | `review_id?: str` (all events if omitted) |
| `get_review_stats` | Workflow health statistics | none |
| `get_review_timeline` | Chronological event timeline for one review | `review_id: str` |

Naming rationale:
- `get_` prefix is consistent with existing `get_review_status`, `get_proposal`, `get_discussion`
- Each tool does one thing (per "specialized tools" decision)
- `get_audit_log` without a review_id returns all events (per "history returns everything" decision)

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Derive history from reviews+messages tables | Dedicated audit_events table | Phase 5 (user decision) | Explicit event log with actor and metadata |
| `datetime('now')` for all timestamps | `strftime('%Y-%m-%dT%H:%M:%fZ', 'now')` for audit | Phase 5 | ISO 8601 compliance in new tools |
| Single list_reviews for all visibility | Specialized activity feed + stats + timeline | Phase 5 | Richer observability without overloading existing tools |

## Open Questions

1. **Audit events for historical reviews**
   - What we know: The audit_events table will be empty for reviews created before Phase 5 (Phases 1-4 reviews won't have audit trails).
   - What's unclear: Whether this matters for the user's experience.
   - Recommendation: This is expected and acceptable. Stats and timeline tools should handle missing audit events gracefully (return null for metrics that can't be computed). Document this as a known limitation.

2. **Activity feed pagination**
   - What we know: CONTEXT.md says "data volume is small per project" and "history returns everything -- no time-range filtering needed."
   - What's unclear: Whether a LIMIT should still be applied as a safety measure.
   - Recommendation: No pagination for now (consistent with user decision). If volume becomes a concern in v2, add optional limit parameter.

3. **Audit event for "in_review" transition**
   - What we know: The state machine supports `claimed -> in_review`, but no tool currently triggers this transition explicitly (it was designed for future use).
   - What's unclear: Whether to add an audit event type for this unused transition.
   - Recommendation: Skip it. Only wire audit events for transitions that actually happen in the current codebase. If `in_review` gets used later, add the event then.

4. **Stats tool: "time in each state" metric**
   - What we know: CONTEXT.md mentions "time in each state" as part of the stats tool.
   - What's unclear: Whether this means average time per-state or per-review time-in-state breakdown.
   - Recommendation: Implement as average time per-state across all reviews (e.g., "reviews spend an average of X seconds in pending, Y seconds in claimed"). This is computed from audit events by finding consecutive state transitions and averaging the gaps.

## Sources

### Primary (HIGH confidence)
- **Existing codebase** -- All source files in `tools/gsd-review-broker/src/gsd_review_broker/` read in full (db.py, models.py, tools.py, server.py, state_machine.py, notifications.py, priority.py, diff_utils.py)
- **Existing tests** -- conftest.py, test_tools.py, test_proposals.py, test_messages.py, test_category.py read in full. 147 tests passing.
- **SQLite date/time functions** -- [Official SQLite documentation](https://sqlite.org/lang_datefunc.html): `strftime()`, `julianday()`, `timediff()`, `unixepoch()` verified
- **SQLite aggregate functions** -- [Official SQLite documentation](https://sqlite.org/lang_aggfunc.html): `avg()`, `count()`, `sum()`, `group_concat()` verified
- **Phase 4 research** -- 04-RESEARCH.md read for patterns and prior decisions

### Secondary (MEDIUM confidence)
- **Audit log design patterns** -- [Database Design for Audit Logging (Redgate)](https://www.red-gate.com/blog/database-design-for-audit-logging), [SQLite JSON audit log (Simon Willison)](https://til.simonwillison.net/sqlite/json-audit-log): Informed schema design choices
- **ISO 8601 formatting in SQLite** -- [SQLite forum discussion](https://sqlite.org/forum/info/9e47a59f538b6b2c02b8b64e999d8e47365f64845d504b4c31f6300d22e9565b): Confirmed `strftime('%Y-%m-%dT%H:%M:%fZ', ...)` approach

### Tertiary (LOW confidence)
- None -- all findings verified with primary or secondary sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- No new libraries; all existing patterns carry forward unchanged
- Architecture (audit table): HIGH -- Standard append-only table design verified against SQLite docs and audit logging best practices
- Architecture (observability tools): HIGH -- SQL patterns verified against official SQLite function documentation
- Architecture (E2E tests): HIGH -- Follows established MockContext pattern from existing 147 tests
- Pitfalls: HIGH -- Based on direct analysis of existing codebase patterns and SQL edge cases
- ISO 8601 timestamps: HIGH -- SQLite strftime behavior verified against official docs

**Research date:** 2026-02-17
**Valid until:** 2026-03-17 (stable -- no external library changes involved; all SQLite features are mature)
