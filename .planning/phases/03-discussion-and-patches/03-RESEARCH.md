# Phase 3: Discussion and Patches - Research

**Researched:** 2026-02-16
**Domain:** Multi-round review conversations, counter-patches, priority queuing, push notifications
**Confidence:** HIGH (existing codebase patterns well-understood; notification landscape thoroughly verified)

## Summary

Phase 3 extends the single-round propose/verdict flow into a rich back-and-forth protocol with four sub-features: message threading per review, counter-patches from the reviewer, priority-based queue ordering, and push notifications to connected clients. The existing codebase (FastMCP 2.14.5, aiosqlite, SQLite) provides all necessary primitives.

The most significant design decision is the push notification mechanism. Research reveals that neither Claude Code nor Codex CLI currently display MCP server-initiated notifications (log messages, progress notifications, resource_updated) in their UI. Both clients receive notifications at the protocol level but do not surface them. This means **true push notifications are not viable for v1** -- the existing polling pattern (`get_review_status`) should remain the primary mechanism, with a lightweight internal event system (asyncio.Event/Condition) ready for future push support.

**Primary recommendation:** Implement messages as a new `messages` table with flat ordering per review (not a tree), counter-patches as a `pending_counter_patch` column on reviews, priority as a `priority` column with sort-only semantics, and notifications as an internal asyncio event bus that tools can optionally await (with polling as the primary external mechanism).

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- **Message threading:** Turn-based messaging with strict alternation between proposer and reviewer. Messages support text body + optional structured metadata (e.g., file paths, line references).
- **Counter-patches:** Counter-patch replaces the original proposal's diff (not coexisting). Counter-patches go through the same `git apply --check` validation as original diffs. Proposer must explicitly accept a counter-patch before it becomes the active diff -- it is pending until accepted.
- **Priority levels:** Auto-inferred from agent identity context (agent role + phase), not manually set. Priority is fixed at submission time -- reviewer cannot change it after the fact. Priority affects sort order in `list_reviews` only (critical first, then normal, then low). Inference rules: planner proposals = critical, executor tasks = normal, verification = low.
- **Push notifications:** Trigger events: new proposal creation and proposer revision submissions (not all state changes). Both proposer and reviewer receive push notifications for events relevant to them.

### Claude's Discretion
- Message structure (flat vs threaded)
- History retrieval strategy (full vs latest round)
- Which verdicts can carry counter-patches
- Push notification transport mechanism
- Reconnect/catch-up behavior

### Deferred Ideas (OUT OF SCOPE)
None -- discussion stayed within phase scope.
</user_constraints>

## Standard Stack

The established libraries/tools for this domain:

### Core (already installed)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastMCP | 2.14.5 | MCP server framework | Already in use; provides Context, tools, lifespan |
| aiosqlite | 0.22.x | Async SQLite access | Already in use; all DB patterns established |
| unidiff | 0.7.5+ | Diff parsing | Already in use for extract_affected_files |
| mcp SDK | 1.26.0 | Underlying MCP protocol | Transitive via FastMCP; provides notification types |

### Supporting (no new dependencies needed)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| asyncio (stdlib) | 3.12+ | Event/Condition for internal notifications | Push notification internal signaling |
| json (stdlib) | 3.12+ | Message metadata serialization | Structured metadata on messages |
| uuid (stdlib) | 3.12+ | Message IDs | Already used for review IDs |

### No New Dependencies Required
Phase 3 requires zero new dependencies. Everything builds on the existing stack with new tables, columns, and tools.

**Installation:** No changes to pyproject.toml dependencies.

## Architecture Patterns

### Recommended Project Structure (additions only)
```
src/gsd_review_broker/
    db.py              # ADD: messages table, priority column, counter_patch columns
    models.py          # ADD: Priority enum, Message model, CounterPatchStatus enum
    tools.py           # ADD: add_message, accept_counter_patch, get_discussion tools
                       # MODIFY: submit_verdict (counter-patch), create_review (priority),
                       #          list_reviews (priority sort)
    state_machine.py   # No changes needed (existing transitions support the flow)
    notifications.py   # NEW: Internal event bus for notification signaling
    priority.py        # NEW: Priority inference logic from agent identity
tests/
    test_messages.py       # NEW: Message threading tests
    test_counter_patch.py  # NEW: Counter-patch lifecycle tests
    test_priority.py       # NEW: Priority inference and sort tests
    test_notifications.py  # NEW: Internal event bus tests
```

### Pattern 1: Flat Message List with Round Tracking (RECOMMENDED for Claude's Discretion: message structure)

**What:** Messages stored in a flat `messages` table with `round_number` column. Each propose/revise cycle increments the round. All messages in a review are ordered by `created_at`.

**Why flat over threaded tree:**
- The existing review model is inherently sequential (pending -> claimed -> verdict -> revise -> re-claim)
- Turn-based alternation (locked decision) maps perfectly to a flat chronological list
- A tree structure adds parent_message_id complexity with no benefit since messages never branch
- Retrieval is simpler: `SELECT ... WHERE review_id = ? ORDER BY created_at`
- Round-based filtering (`WHERE round_number = ?`) enables "latest round" queries trivially

**Schema:**
```sql
CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    review_id   TEXT NOT NULL REFERENCES reviews(id),
    sender_role TEXT NOT NULL CHECK(sender_role IN ('proposer', 'reviewer')),
    round       INTEGER NOT NULL DEFAULT 1,
    body        TEXT NOT NULL,
    metadata    TEXT,  -- JSON: file paths, line refs, etc.
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_messages_review ON messages(review_id, round);
```

**Turn enforcement:** Before inserting a message, query the last message for this review. If it exists and has the same `sender_role`, reject with an error. This enforces strict alternation.

**Round tracking:** Round increments when a review transitions from `changes_requested` back to `pending` (revision). The round number is stored on the review itself and copied to each message.

### Pattern 2: Counter-Patch as Pending State on Review

**What:** Counter-patches are stored directly on the `reviews` table as pending replacement diffs. A counter-patch does not immediately replace the active diff -- it requires explicit acceptance by the proposer.

**Schema additions to reviews table:**
```sql
ALTER TABLE reviews ADD COLUMN counter_patch TEXT;
ALTER TABLE reviews ADD COLUMN counter_patch_affected_files TEXT;
ALTER TABLE reviews ADD COLUMN counter_patch_status TEXT CHECK(counter_patch_status IN ('pending', 'accepted', 'rejected'));
```

**Lifecycle:**
1. Reviewer submits verdict with counter-patch (diff validated via `git apply --check`)
2. Counter-patch stored in `counter_patch` column, status = `pending`
3. Proposer calls `accept_counter_patch` or `reject_counter_patch`
4. On accept: `counter_patch` replaces `diff`, `affected_files` updated, counter-patch columns cleared
5. On reject: counter-patch columns cleared, review continues

**Which verdicts can carry counter-patches (RECOMMENDATION for Claude's Discretion):**
Use `request_changes` and `comment` only. Not `approve`. Rationale:
- `request_changes` + counter-patch = "here's what I want instead" (primary use case)
- `comment` + counter-patch = "consider this alternative" (softer suggestion)
- `approve` + counter-patch is contradictory -- if you're approving, the current diff is fine; attaching an alternative creates confusion about which diff is actually approved

### Pattern 3: Priority Inference Module

**What:** A pure function that derives priority from agent identity fields.

```python
from gsd_review_broker.models import Priority

def infer_priority(agent_type: str, agent_role: str, phase: str,
                   plan: str | None, task: str | None) -> Priority:
    """Infer review priority from agent identity context.

    Rules (from locked decisions):
    - planner proposals = critical
    - executor tasks = normal
    - verification = low
    """
    if agent_type == "gsd-planner" or agent_role == "planner":
        return Priority.CRITICAL
    if "verify" in phase.lower() or "verification" in (task or "").lower():
        return Priority.LOW
    return Priority.NORMAL
```

**Sort in list_reviews:**
```sql
ORDER BY
    CASE priority
        WHEN 'critical' THEN 0
        WHEN 'normal' THEN 1
        WHEN 'low' THEN 2
    END,
    created_at ASC
```

### Pattern 4: Internal Event Bus for Notifications (RECOMMENDED for Claude's Discretion: transport mechanism)

**What:** An asyncio-based event notification system internal to the broker process. Not exposed over MCP protocol (since clients don't display server notifications). Instead, tools that want to "wait for something to happen" can subscribe to events internally.

**Why not MCP protocol notifications:**
- Claude Code receives MCP notifications but does NOT display them in UI (confirmed: GitHub issue #3174, closed as "not planned")
- Codex CLI's notification display support is undocumented/unverified
- `ctx.session.send_log_message()` and `ctx.report_progress()` are silently ignored by current clients
- Building on non-functional client capabilities wastes implementation effort

**What to build instead:**
```python
import asyncio
from dataclasses import dataclass, field

@dataclass
class NotificationBus:
    """In-process event bus for review state changes."""
    _events: dict[str, asyncio.Event] = field(default_factory=dict)

    def notify(self, review_id: str) -> None:
        """Signal that something changed for a review."""
        event = self._events.get(review_id)
        if event is not None:
            event.set()

    async def wait_for_change(self, review_id: str, timeout: float = 25.0) -> bool:
        """Wait for a change on a review, with timeout. Returns True if signaled."""
        event = self._events.setdefault(review_id, asyncio.Event())
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    def cleanup(self, review_id: str) -> None:
        """Remove event for a closed review."""
        self._events.pop(review_id, None)
```

**Integration with AppContext:**
```python
@dataclass
class AppContext:
    db: aiosqlite.Connection
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    repo_root: str | None = None
    notifications: NotificationBus = field(default_factory=NotificationBus)
```

**Trigger points (per locked decisions):**
- `create_review` (new proposal) -> `notifications.notify(review_id)` -- alerts reviewer
- `create_review` (revision) -> `notifications.notify(review_id)` -- alerts reviewer of resubmission

**Consumer:** `get_review_status` can optionally use `wait_for_change` instead of returning immediately, reducing polling frequency while respecting MCP timeout (30s). This is an enhancement to the existing polling tool.

**Reconnect behavior (RECOMMENDATION for Claude's Discretion):** Poll-only fallback. Since true push is not displayed by clients, reconnect backlog adds complexity for zero user-visible benefit. The internal event bus reduces polling latency (event fires immediately vs. 3-second polling interval) without requiring any client-side support.

### Pattern 5: History Retrieval Strategy (RECOMMENDATION for Claude's Discretion)

**Recommendation: Full history by default, with optional round filter.**

```python
@mcp.tool
async def get_discussion(
    review_id: str,
    round: int | None = None,  # None = all rounds
    ctx: Context = None,
) -> dict:
    """Retrieve discussion messages for a review."""
```

Rationale:
- Full history is needed for context when reviewing a revision (what was said before?)
- Round filter lets agents get just the latest exchange when they already have prior context
- Messages are small text -- performance is not a concern for typical review discussions
- Agents can request `round=N` for efficiency when they know the current round number

### Anti-Patterns to Avoid
- **Storing messages in a JSON blob on the review:** Violates atomicity, makes querying by round/sender impossible, breaks concurrent access patterns
- **Using MCP resources for message streams:** Resources are for static/semi-static content, not real-time message streams. Tools returning dicts are the established pattern in this codebase
- **Building WebSocket/SSE push directly:** Adds infrastructure complexity for zero client-visible benefit (clients don't display notifications)
- **Making priority mutable:** Locked decision says priority is fixed at submission time. Don't add update_priority tools

## Don't Hand-Roll

Problems that look simple but have existing solutions:

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Diff validation | Custom diff parser | `validate_diff()` (existing) | Already handles git apply --check with subprocess, error reporting |
| Affected files extraction | Manual diff parsing | `extract_affected_files()` (existing) | Uses unidiff.PatchSet, handles edge cases |
| State transitions | If/else chains | `validate_transition()` (existing) | State machine is already defined and tested |
| UUID generation | Custom ID scheme | `uuid.uuid4()` (existing pattern) | Consistent with review IDs |
| JSON metadata | Custom serialization | `json.dumps/loads` (existing pattern) | Consistent with affected_files handling |
| Write serialization | Custom locks | `app.write_lock` (existing) | asyncio.Lock already manages concurrent writes |
| Transaction pattern | Ad-hoc SQL | BEGIN IMMEDIATE/COMMIT/ROLLBACK (existing) | Established pattern with `_rollback_quietly` |

**Key insight:** Phase 3 adds new tables and tools but should follow ALL existing patterns exactly. The codebase has strong conventions (dict error returns, MockContext testing, write_lock + BEGIN IMMEDIATE) that must be replicated, not reinvented.

## Common Pitfalls

### Pitfall 1: Turn Enforcement Race Condition
**What goes wrong:** Two proposer messages could be inserted simultaneously if turn validation happens outside the write lock.
**Why it happens:** Read-then-write pattern without locking allows interleaving.
**How to avoid:** Check last message sender inside BEGIN IMMEDIATE block, within `app.write_lock`.
**Warning signs:** Test with concurrent message submissions.

### Pitfall 2: Counter-Patch Orphaning on Review Revision
**What goes wrong:** If a review has a pending counter-patch and the proposer revises (resubmits), the counter-patch references a now-replaced diff.
**Why it happens:** Revision replaces intent/description/diff but might forget to clear counter-patch columns.
**How to avoid:** When processing a revision in `create_review`, clear `counter_patch`, `counter_patch_affected_files`, and `counter_patch_status` columns.
**Warning signs:** Test revision flow with a pending counter-patch present.

### Pitfall 3: Priority Sort Breaking Existing list_reviews Behavior
**What goes wrong:** Adding ORDER BY with CASE expression changes default ordering for existing unprioritized reviews.
**Why it happens:** Reviews created before priority column exists will have NULL priority.
**How to avoid:** Default priority to 'normal' in the schema migration. Use `COALESCE(priority, 'normal')` in the sort expression. Run migration that backfills existing rows.
**Warning signs:** Test list_reviews with a mix of old (no priority) and new reviews.

### Pitfall 4: Counter-Patch Validation Timing
**What goes wrong:** Counter-patch diff validated at verdict submission time but becomes stale by the time proposer accepts it (working tree changed).
**Why it happens:** Time gap between reviewer submitting counter-patch and proposer accepting it.
**How to avoid:** Re-validate counter-patch diff when proposer calls `accept_counter_patch`, just like `claim_review` re-validates the original diff. If stale, return error but don't auto-modify the review state.
**Warning signs:** Test accept_counter_patch with a diff that was valid at submission but invalid at acceptance.

### Pitfall 5: Message Round Tracking Desync
**What goes wrong:** Round number on messages doesn't match the review's current round if the round increment happens in a different transaction.
**Why it happens:** Round is incremented during revision but messages are inserted separately.
**How to avoid:** Store `current_round` on the review itself. When creating a message, read the review's current_round in the same transaction. When processing a revision, increment `current_round` atomically.
**Warning signs:** Test multi-round lifecycle: create -> claim -> reject -> revise -> re-claim -> message -> reject -> revise.

### Pitfall 6: Schema Migration on Existing Data
**What goes wrong:** `ALTER TABLE reviews ADD COLUMN priority ...` fails because the column already exists (idempotent rerun) or reviews without priority cause sort errors.
**Why it happens:** The codebase uses SCHEMA_MIGRATIONS list that catches duplicate column errors.
**How to avoid:** Follow the existing SCHEMA_MIGRATIONS pattern exactly. Add new migrations to the list. Ensure DEFAULT values are set so existing rows get sensible defaults.
**Warning signs:** Test with a pre-existing database that has reviews from Phase 1-2.

## Code Examples

Verified patterns from the existing codebase:

### Adding a Message (follows existing tool pattern)
```python
@mcp.tool
async def add_message(
    review_id: str,
    sender_role: str,
    body: str,
    metadata: str | None = None,
    ctx: Context = None,
) -> dict:
    """Add a message to a review's discussion thread.

    Turn-based: proposer and reviewer must alternate. Rejects consecutive
    messages from the same role.
    """
    if sender_role not in ("proposer", "reviewer"):
        return {"error": "sender_role must be 'proposer' or 'reviewer'"}

    app: AppContext = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")

            # Verify review exists and is in a valid state for messaging
            cursor = await app.db.execute(
                "SELECT status, current_round FROM reviews WHERE id = ?",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}

            status = row["status"]
            if status not in ("claimed", "in_review", "changes_requested"):
                await app.db.execute("ROLLBACK")
                return {"error": f"Cannot add message to review in '{status}' state"}

            # Turn enforcement: check last message sender
            cursor = await app.db.execute(
                "SELECT sender_role FROM messages WHERE review_id = ? "
                "ORDER BY created_at DESC LIMIT 1",
                (review_id,),
            )
            last_msg = await cursor.fetchone()
            if last_msg is not None and last_msg["sender_role"] == sender_role:
                await app.db.execute("ROLLBACK")
                return {
                    "error": f"Turn violation: {sender_role} cannot send consecutive messages. "
                    f"Waiting for {'reviewer' if sender_role == 'proposer' else 'proposer'}."
                }

            msg_id = str(uuid.uuid4())
            await app.db.execute(
                """INSERT INTO messages (id, review_id, sender_role, round, body, metadata)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (msg_id, review_id, sender_role, row["current_round"], body, metadata),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("add_message", exc)

    # Fire notification for the other party
    app.notifications.notify(review_id)

    return {"message_id": msg_id, "review_id": review_id, "round": row["current_round"]}
```

### Counter-Patch in submit_verdict (extends existing pattern)
```python
# Inside submit_verdict, after verdict validation:
if counter_patch is not None:
    if verdict not in ("request_changes", "comment"):
        return {"error": "Counter-patches only allowed with request_changes or comment verdicts"}

    is_valid, error_detail = await validate_diff(counter_patch, cwd=app.repo_root)
    if not is_valid:
        return {
            "error": "Counter-patch diff validation failed",
            "validation_error": error_detail,
        }
    counter_affected = extract_affected_files(counter_patch)

    # Store in DB within the existing transaction
    await app.db.execute(
        """UPDATE reviews SET counter_patch = ?, counter_patch_affected_files = ?,
                              counter_patch_status = 'pending'
           WHERE id = ?""",
        (counter_patch, counter_affected, review_id),
    )
```

### Priority Inference (new module)
```python
# priority.py
from gsd_review_broker.models import Priority

def infer_priority(agent_type: str, agent_role: str, phase: str,
                   plan: str | None = None, task: str | None = None) -> Priority:
    """Infer review priority from agent identity.

    Rules:
    - planner proposals = critical
    - executor tasks = normal (default)
    - verification = low
    """
    if "planner" in agent_type.lower():
        return Priority.CRITICAL
    if "verify" in phase.lower():
        return Priority.LOW
    return Priority.NORMAL
```

### Priority-Sorted list_reviews Query
```sql
SELECT id, status, intent, agent_type, phase, priority, created_at
FROM reviews
WHERE status = ?
ORDER BY
    CASE COALESCE(priority, 'normal')
        WHEN 'critical' THEN 0
        WHEN 'normal' THEN 1
        WHEN 'low' THEN 2
    END,
    created_at ASC
```

### Schema Migrations (follows existing pattern)
```python
SCHEMA_MIGRATIONS: list[str] = [
    # Phase 2 migrations
    "ALTER TABLE reviews ADD COLUMN description TEXT",
    "ALTER TABLE reviews ADD COLUMN diff TEXT",
    "ALTER TABLE reviews ADD COLUMN affected_files TEXT",
    # Phase 3 migrations
    "ALTER TABLE reviews ADD COLUMN priority TEXT NOT NULL DEFAULT 'normal'",
    "ALTER TABLE reviews ADD COLUMN current_round INTEGER NOT NULL DEFAULT 1",
    "ALTER TABLE reviews ADD COLUMN counter_patch TEXT",
    "ALTER TABLE reviews ADD COLUMN counter_patch_affected_files TEXT",
    "ALTER TABLE reviews ADD COLUMN counter_patch_status TEXT",
]
```

Note: SQLite `ALTER TABLE ADD COLUMN` with `NOT NULL DEFAULT` works for new columns on existing rows -- the default value is used for existing rows that don't have the column.

### Test Pattern (follows MockContext pattern exactly)
```python
# test_messages.py
class TestMessageThreading:
    async def test_alternating_messages(self, ctx: MockContext) -> None:
        """Proposer and reviewer can alternate messages."""
        created = await _create_review(ctx)
        review_id = created["review_id"]
        await claim_review.fn(review_id=review_id, reviewer_id="r1", ctx=ctx)

        # Reviewer sends first message
        msg1 = await add_message.fn(
            review_id=review_id, sender_role="reviewer",
            body="Please clarify the intent", ctx=ctx,
        )
        assert "message_id" in msg1

        # Proposer responds
        msg2 = await add_message.fn(
            review_id=review_id, sender_role="proposer",
            body="The intent is to refactor logging", ctx=ctx,
        )
        assert "message_id" in msg2

    async def test_consecutive_same_sender_rejected(self, ctx: MockContext) -> None:
        """Two consecutive messages from the same role are rejected."""
        created = await _create_review(ctx)
        review_id = created["review_id"]
        await claim_review.fn(review_id=review_id, reviewer_id="r1", ctx=ctx)

        await add_message.fn(
            review_id=review_id, sender_role="reviewer",
            body="First message", ctx=ctx,
        )
        result = await add_message.fn(
            review_id=review_id, sender_role="reviewer",
            body="Second message (should fail)", ctx=ctx,
        )
        assert "error" in result
        assert "Turn violation" in result["error"]
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| MCP SSE transport | Streamable HTTP | FastMCP 2.3+ | Server already uses streamable-http; correct |
| Push via ctx.report_progress | Not displayed by clients | Confirmed Feb 2026 | Internal event bus is the pragmatic alternative |
| Client notifications via send_log_message | Claude Code ignores (issue #3174 closed as not planned) | Feb 2026 | Don't rely on protocol-level push |

**Key realization:** The MCP protocol supports server-to-client notifications, but the two clients this broker serves (Claude Code, Codex CLI) do not display them. The pragmatic approach is an internal event bus that reduces polling latency within the same process, while keeping the existing `get_review_status` polling pattern as the external API.

## Open Questions

Things that couldn't be fully resolved:

1. **Codex CLI notification display**
   - What we know: Codex CLI supports MCP streamable-http transport and has notification infrastructure
   - What's unclear: Whether it actually displays `notifications/message` or progress notifications in its UI
   - Recommendation: Treat as unsupported (same as Claude Code). If it turns out to work, the internal event bus can be extended to also send MCP notifications -- the architecture supports adding that layer later

2. **First message sender**
   - What we know: Turn-based alternation is locked. But who sends the first message?
   - What's unclear: Does the proposer or reviewer send the first explicit message after claim? The verdict itself could be considered the reviewer's first "message" in a round.
   - Recommendation: Allow either role to send the first message in a discussion. The verdict is a separate mechanism -- explicit messages are supplementary. First-message flexibility avoids blocking on "whose turn is it?" ambiguity.

3. **Counter-patch + approve edge case**
   - What we know: Recommendation is to disallow counter-patches on approve verdicts
   - What's unclear: Could there be a legitimate use case for "approve with a minor tweak"?
   - Recommendation: Disallow for now. "Approve with tweak" is really "request_changes with a counter-patch" semantically. Keep the model clean.

4. **Round increment trigger**
   - What we know: Rounds should track propose/revise cycles
   - What's unclear: Does the round increment on `create_review` revision, or on `claim_review` re-claim?
   - Recommendation: Increment on revision (when `create_review` is called with `review_id` for resubmission). This is when the proposer starts a new attempt.

## New Tools Summary

| Tool | Purpose | INTER/PROTO |
|------|---------|-------------|
| `add_message` | Add a message to a review discussion | INTER-03 |
| `get_discussion` | Retrieve messages for a review (all rounds or specific round) | INTER-03 |
| `accept_counter_patch` | Proposer accepts reviewer's counter-patch as active diff | PROTO-06 |
| `reject_counter_patch` | Proposer rejects reviewer's counter-patch | PROTO-06 |

**Modified tools:**
| Tool | Change | INTER/PROTO |
|------|--------|-------------|
| `submit_verdict` | Add optional `counter_patch` parameter | PROTO-06 |
| `create_review` | Auto-infer priority, store on review, clear counter-patch on revision, increment round | PROTO-08, INTER-03 |
| `list_reviews` | Sort by priority (critical > normal > low) then created_at | PROTO-08 |
| `get_review_status` | Optionally wait on NotificationBus before returning | INTER-06 |

## Sources

### Primary (HIGH confidence)
- Existing codebase at `tools/gsd-review-broker/src/` -- all current patterns, schema, tools (read in full)
- FastMCP 2.14.5 documentation at https://gofastmcp.com/servers/context -- Context methods, notification capabilities
- Claude Code GitHub issue #3174 -- notifications not displayed, closed as "not planned"
- Claude Code GitHub issue #4157 -- progress notifications not shown in UI

### Secondary (MEDIUM confidence)
- FastMCP GitHub discussions #429 -- streaming not yet supported for tool responses
- MCP server notifications reference implementation at https://github.com/prakharbanka/mcp-server-notifications -- pattern for ctx.session.send_progress_notification
- Python asyncio sync primitives documentation -- Event/Condition patterns

### Tertiary (LOW confidence)
- Codex CLI MCP notification support -- not explicitly documented for display behavior; assumed unsupported

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, existing codebase fully understood
- Architecture (messages): HIGH -- flat list with round tracking is straightforward SQL
- Architecture (counter-patches): HIGH -- column additions follow established migration pattern
- Architecture (priority): HIGH -- pure function + ORDER BY, simple
- Architecture (notifications): MEDIUM -- internal event bus is sound, but "optimal" push remains blocked by client limitations
- Pitfalls: HIGH -- all derived from analyzing existing code patterns and identifying extension points

**Research date:** 2026-02-16
**Valid until:** 2026-03-16 (stable; no external dependency changes expected)
