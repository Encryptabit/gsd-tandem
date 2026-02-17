# Phase 2: Proposal and Diff Protocol - Research

**Researched:** 2026-02-16
**Domain:** Unified diff handling, subprocess-based git validation, SQLite schema evolution, MCP tool surface design
**Confidence:** HIGH

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions

#### Proposal structure
- Structured breakdown for intent: natural language description + list of what changed and why (PR-description style, not one-liner)
- Full agent identity context included: agent_type, role, phase, plan, task -- reviewer sees exactly where the change fits in the workflow
- Multi-file diffs stored as a single unified diff blob, but proposal metadata lists affected files separately for easier navigation
- All file operations supported: create, modify, delete -- full unified diff semantics including /dev/null for new/deleted files

#### Verdict model
- Three verdict types: approve, request_changes, comment -- matches GitHub PR review model
- Notes required for request_changes and comment; only approve can be bare
- request_changes transitions to a new 'changes_requested' state (distinct from pending) -- stays claimed by the same reviewer, signals proposer needs to act
- Revisions replace in place -- latest proposal overwrites previous content, no version history

#### Diff validation
- git apply --check runs on claim, not on submission -- diffs can enter the queue before validation
- No format validation on submission -- trust the proposer (always an agent generating valid diffs)
- If git apply --check fails on claim: auto-reject with detailed errors (capture git apply --check stderr so proposer knows exactly which files/hunks conflicted)

### Claude's Discretion

- Tool surface design: whether to extend existing tools or create new ones for proposal submission, verdict, and diff retrieval
- How the reviewer accesses proposal content (inline in claim response vs separate tool)
- Revision mechanism details (how resubmission works on same review_id)
- Verdict tool design (standalone vs extending Phase 1 approve/reject tools)

### Deferred Ideas (OUT OF SCOPE)

None -- discussion stayed within phase scope
</user_constraints>

## Summary

Phase 2 enriches the Phase 1 review lifecycle with structured proposal content (intent descriptions + unified diffs), typed reviewer verdicts with reasoning, and pre-application diff validation via `git apply --check`. The existing codebase has a clean three-layer architecture (tools -> state_machine -> db) with 6 MCP tools, a reviews table in SQLite, and established patterns for async database operations and transaction management.

The primary technical challenges are: (1) extending the SQLite schema to store diff blobs and structured proposal metadata alongside existing review records, (2) running `git apply --check` as an async subprocess on both Windows and POSIX with stdin piping and stderr capture, (3) extracting affected file paths from unified diff text for proposal metadata, and (4) designing the MCP tool surface to minimize agent round-trips while keeping tools focused.

Research confirms that `asyncio.create_subprocess_exec` works correctly on Windows (Python 3.12+ uses ProactorEventLoop by default), `git apply --check` returns exit code 0 on success / 1 on failure with descriptive stderr, and the `unidiff` library can parse diff blobs to extract file paths. The existing `submit_verdict` tool already handles `approved` and `changes_requested` verdicts -- it needs extension to support `comment` as a third verdict type.

**Primary recommendation:** Extend the existing schema with new columns on the reviews table (diff, description, affected_files) rather than creating separate tables. Extend existing tools (create_review, submit_verdict, claim_review) with new parameters rather than creating parallel tool sets. Add one new tool (get_proposal) for reviewer access to full proposal content. Run `git apply --check` via `asyncio.create_subprocess_exec` with stdin pipe during `claim_review`.

## Standard Stack

### Core (already in place from Phase 1)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| FastMCP | `>=2.14,<3` | MCP server framework | Already in use. `@mcp.tool` decorator, Context injection. |
| aiosqlite | `>=0.22,<1` | Async SQLite | Already in use. WAL mode, `BEGIN IMMEDIATE` transactions. |
| Python | 3.12+ | Runtime | `asyncio.create_subprocess_exec` works on Windows via ProactorEventLoop (default since 3.8). |
| pydantic | (transitive) | Data validation | Already in use for models. Extend Review model with new fields. |

### New for Phase 2
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| unidiff | `>=0.7.5` | Parse unified diffs | Extract affected file paths from diff blob for proposal metadata. Lightweight (pure Python, no C deps). |
| asyncio.subprocess | (stdlib) | Async subprocess | Run `git apply --check` without blocking the event loop. No additional install. |

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `unidiff` for file extraction | Manual regex parsing | Regex is fragile for edge cases (renames, binary files, mode changes). `unidiff` handles all unified diff variants correctly. |
| `unidiff` for file extraction | `whatthepatch` library | Less maintained, fewer GitHub stars, API less intuitive. `unidiff` is the standard choice. |
| `asyncio.create_subprocess_exec` | `subprocess.run` in executor | Works but less idiomatic. `create_subprocess_exec` is the standard async pattern and avoids thread pool overhead. |
| Extending reviews table | Separate proposals table | Separate table adds JOIN complexity for every query. A review IS a proposal -- keeping data together is simpler. Schema migration is trivial with ALTER TABLE ADD COLUMN. |

**Installation:**
```bash
# In tools/gsd-review-broker/
# Add unidiff to pyproject.toml dependencies:
#   "unidiff>=0.7.5,<1",
uv sync
```

## Architecture Patterns

### Schema Evolution Strategy

The Phase 1 reviews table needs three new columns for proposal content. SQLite supports `ALTER TABLE ADD COLUMN` with `IF NOT EXISTS`-style safety via `CREATE TABLE IF NOT EXISTS` in the initial schema, or by checking `PRAGMA table_info` before altering.

**Recommended approach:** Update `SCHEMA_SQL` in `db.py` to include the new columns from the start. Since the database is recreated on first run (CREATE TABLE IF NOT EXISTS), existing databases from Phase 1 testing can simply be deleted. For production continuity, add defensive `ALTER TABLE ADD COLUMN` statements wrapped in try/except.

```python
# Schema evolution in db.py
SCHEMA_V2_MIGRATIONS = [
    "ALTER TABLE reviews ADD COLUMN description TEXT",
    "ALTER TABLE reviews ADD COLUMN diff TEXT",
    "ALTER TABLE reviews ADD COLUMN affected_files TEXT",  # JSON array of file paths
]

async def ensure_schema(db: aiosqlite.Connection) -> None:
    """Create tables and indexes, then apply migrations."""
    await db.executescript(SCHEMA_SQL)
    for migration in SCHEMA_V2_MIGRATIONS:
        try:
            await db.execute(migration)
        except Exception:
            pass  # Column already exists
```

**Confidence:** HIGH -- `ALTER TABLE ADD COLUMN` is fully supported in SQLite and the try/except pattern is standard for idempotent migrations.

### Pattern 1: Async Subprocess for Git Validation

**What:** Run `git apply --check` via `asyncio.create_subprocess_exec` with stdin piping to validate diffs without blocking the event loop.
**When to use:** During `claim_review` when the review has a diff attached.

```python
# Source: Python 3.14 docs (asyncio-subprocess.html), empirically verified on Windows
import asyncio

async def validate_diff(diff_text: str, cwd: str | None = None) -> tuple[bool, str]:
    """Run git apply --check on a diff. Returns (valid, error_message)."""
    proc = await asyncio.create_subprocess_exec(
        "git", "apply", "--check",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    stdout, stderr = await proc.communicate(input=diff_text.encode("utf-8"))
    if proc.returncode == 0:
        return True, ""
    return False, stderr.decode("utf-8", errors="replace")
```

**Empirically verified behavior (tested 2026-02-16 on this project):**
- Success: returncode=0, stderr='' (empty)
- Context mismatch: returncode=1, stderr='error: patch failed: path/to/file:1\nerror: path/to/file: patch does not apply\n'
- File not found: returncode=1, stderr='error: path/to/file: No such file or directory\n'
- Corrupt patch: returncode=128, stderr='error: corrupt patch at line N\n'
- New file creation: returncode=0 (succeeds even if file doesn't exist yet -- correct behavior)

**Confidence:** HIGH -- empirically tested via both `subprocess.run` and `asyncio.create_subprocess_exec` on Windows in this project's repo.

### Pattern 2: Extracting Affected Files from Diff Blob

**What:** Use `unidiff.PatchSet` to parse the diff blob and extract the list of affected file paths with their operation type (added/modified/deleted).
**When to use:** During proposal submission to populate the `affected_files` metadata.

```python
# Source: python-unidiff GitHub README (github.com/matiasb/python-unidiff)
from unidiff import PatchSet

def extract_affected_files(diff_text: str) -> list[dict[str, str]]:
    """Extract affected files from a unified diff string."""
    patch = PatchSet(diff_text)
    files = []
    for patched_file in patch:
        if patched_file.is_added_file:
            op = "create"
        elif patched_file.is_removed_file:
            op = "delete"
        else:
            op = "modify"
        files.append({
            "path": patched_file.path,
            "operation": op,
            "added": patched_file.added,
            "removed": patched_file.removed,
        })
    return files
```

**Confidence:** HIGH -- `unidiff` v0.7.5 API verified via GitHub README.

### Pattern 3: Extending Existing Tools vs. Creating New Ones

**Recommendation (Claude's Discretion area):** Extend existing tools with optional parameters rather than creating parallel tool sets.

**Rationale from MCP best practices:**
- Fewer tools = less confusion for agents discovering available tools
- Optional parameters maintain backward compatibility
- Agent context window is conserved

**Specific recommendations:**

| Existing Tool | Extension | Rationale |
|---------------|-----------|-----------|
| `create_review` | Add optional `description` (str) and `diff` (str) params | Proposal submission IS review creation with content. Calling `create_review` with diff makes it a proposal. |
| `submit_verdict` | Add `comment` as third verdict type | Already handles `approved` and `changes_requested`. Adding `comment` is a one-line change to `valid_verdicts`. |
| `claim_review` | Run `git apply --check` before claiming when diff exists. Return proposal content inline. | On-claim validation is a locked decision. Inline content saves a round-trip. |
| NEW: `get_proposal` | New read-only tool | Reviewer may need to re-read proposal content after initial claim. Separate tool keeps concerns clean. |
| `create_review` (revision) | Accept `review_id` param for resubmission | When `review_id` is provided AND status is `changes_requested`, replace proposal content and transition back to `pending`. No separate update tool needed. |

**Tool inventory after Phase 2:**
1. `create_review` -- extended with description, diff, review_id (for revision)
2. `list_reviews` -- unchanged
3. `claim_review` -- extended with git apply --check and inline proposal content
4. `submit_verdict` -- extended with `comment` verdict type, notes enforcement
5. `get_review_status` -- unchanged
6. `close_review` -- unchanged
7. `get_proposal` -- NEW, read-only proposal content retrieval

**Confidence:** MEDIUM -- this is a design recommendation (Claude's Discretion area) based on MCP best practices research. The specific tool boundaries are subjective.

### Pattern 4: Revision Flow via create_review Resubmission

**What:** When a review is in `changes_requested` state, the proposer calls `create_review` with the existing `review_id` to replace the proposal content and transition the review back to `pending`.
**When to use:** Proposer revises after receiving `changes_requested` verdict.

**State machine change needed:** The existing transition map already has `CHANGES_REQUESTED -> PENDING` (for resubmission). This transition needs to:
1. Overwrite `description`, `diff`, `affected_files` columns with new values
2. Clear `verdict_reason` (previous verdict no longer applies)
3. Set status back to `pending`
4. Clear `claimed_by` (reviewer needs to re-claim for re-validation)

**Confidence:** HIGH -- the state machine transition already exists in Phase 1 code.

### Recommended Project Structure (Phase 2 additions)

```
tools/gsd-review-broker/
  src/
    gsd_review_broker/
      __init__.py
      server.py           # Unchanged
      tools.py            # Extended: create_review, submit_verdict, claim_review + new get_proposal
      models.py           # Extended: VerdictType enum, updated Review model
      state_machine.py    # Unchanged (transitions already support Phase 2)
      db.py               # Extended: schema V2 migrations, new columns
      diff_utils.py       # NEW: validate_diff(), extract_affected_files()
  tests/
    conftest.py           # Extended: fixtures for proposals with diffs
    test_tools.py         # Extended: proposal creation, verdict, revision tests
    test_state_machine.py # Unchanged (transitions already tested)
    test_polling.py       # Unchanged
    test_diff_utils.py    # NEW: diff validation and file extraction tests
    test_proposals.py     # NEW: full proposal lifecycle tests
```

### Anti-Patterns to Avoid
- **Separate proposals table with FK to reviews:** Over-engineering. A review IS a proposal. Keep data together in one table.
- **Synchronous subprocess for git apply:** Blocks the event loop, freezes all connected MCP clients. Use `asyncio.create_subprocess_exec`.
- **Validating diff format on submission:** Locked decision says no format validation on submission. Trust the proposer.
- **Storing version history of revisions:** Locked decision says latest overwrites previous. No history table needed.
- **Creating parallel tool sets (create_proposal, submit_proposal_verdict):** Confuses agents with duplicate functionality. Extend existing tools.
- **Using `shell=True` in subprocess calls:** Security risk and Windows compatibility issues. Always use argument lists.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Unified diff parsing | Custom regex to extract file paths from diff headers | `unidiff.PatchSet` | Edge cases: renames, mode changes, binary files, /dev/null paths. Library handles all correctly. |
| Diff format validation | Custom parser checking diff syntax | `git apply --check` | Git's own validation is authoritative. It checks context lines, hunk boundaries, file existence -- things a custom parser would miss. |
| Async subprocess management | Thread pool + subprocess.run | `asyncio.create_subprocess_exec` | Standard library solution. Correct on both Windows (ProactorEventLoop) and POSIX. |
| Schema migration | Custom migration tracking table | `ALTER TABLE ADD COLUMN` with try/except | Only 3 new columns needed. No migration framework warranted for this scale. |
| Affected files JSON serialization | Custom format | `json.dumps`/`json.loads` on list of dicts | Standard, readable, queryable with SQLite JSON functions if ever needed. |

**Key insight:** Phase 2's complexity is in the tool design and state transitions, not in new libraries. The diff is stored as raw text (SQLite TEXT column), validated by git's own tooling, and parsed by `unidiff` only for metadata extraction. No custom diff engine needed.

## Common Pitfalls

### Pitfall 1: Blocking Event Loop with subprocess.run
**What goes wrong:** Using synchronous `subprocess.run()` for `git apply --check` blocks the asyncio event loop. All MCP clients freeze until the subprocess completes.
**Why it happens:** `subprocess.run` is synchronous. In an async context, it blocks the single thread running the event loop.
**How to avoid:** Use `asyncio.create_subprocess_exec` with `PIPE` for stdin/stdout/stderr. Use `proc.communicate()` to send diff text and read results.
**Warning signs:** All tools become unresponsive during diff validation. Reviewer can't poll while proposer's diff is being validated.

### Pitfall 2: git apply --check Working Directory
**What goes wrong:** `git apply --check` fails because it runs from the wrong working directory. The broker process may not be started from the project root.
**Why it happens:** `git apply` resolves file paths relative to the current working directory within the git repository. If the broker starts from a different directory, paths in the diff won't match.
**How to avoid:** Pass `cwd` parameter to `asyncio.create_subprocess_exec` pointing to the git repository root. Discover repo root via `git rev-parse --show-toplevel` at server startup and cache it in `AppContext`.
**Warning signs:** All diffs fail validation with "No such file or directory" even when the diff is correct.

### Pitfall 3: Notes Enforcement Asymmetry
**What goes wrong:** Agent submits `request_changes` or `comment` verdict without notes. The verdict is stored without explanation, defeating the purpose of structured feedback.
**Why it happens:** Phase 1 `submit_verdict` has `reason` as optional. Phase 2 requires notes for `request_changes` and `comment` but not for `approve`.
**How to avoid:** Validate in `submit_verdict`: if verdict is `request_changes` or `comment` and reason is None/empty, return error dict. Approve remains optional.
**Warning signs:** Reviews in `changes_requested` state with no `verdict_reason` explaining what to fix.

### Pitfall 4: Revision Without Clearing Reviewer State
**What goes wrong:** Proposer revises a review (changes_requested -> pending) but the previous `claimed_by` and `verdict_reason` persist. The review appears to still be claimed.
**Why it happens:** Revision only updates the proposal content and status, forgetting to clear reviewer-specific fields.
**How to avoid:** On revision, explicitly clear: `claimed_by = NULL`, `verdict_reason = NULL`. The status transition to `pending` makes it available for re-claiming.
**Warning signs:** Revised reviews showing previous reviewer's name and old verdict reason alongside new proposal content.

### Pitfall 5: Large Diffs Exceeding MCP Response Limits
**What goes wrong:** A proposal with a large multi-file diff exceeds MCP output token limits when returned inline with claim_review.
**Why it happens:** Claude Code has `MAX_MCP_OUTPUT_TOKENS` (default varies). Large diffs in tool responses may be truncated.
**How to avoid:** Return a summary (affected files, line counts) in `claim_review` response. Full diff accessible via `get_proposal` tool. If diff is very large, consider truncation with a note that full content is available.
**Warning signs:** Reviewer sees truncated diff content and makes incorrect review decisions.

### Pitfall 6: Race Condition in Claim + Validation
**What goes wrong:** Two reviewers try to claim the same review simultaneously. Both run `git apply --check`. One succeeds at claiming, the other fails. But both ran potentially expensive subprocess calls.
**Why it happens:** The write_lock only serializes database operations, not the subprocess call.
**How to avoid:** Run `git apply --check` INSIDE the write_lock, after the SELECT+validate step confirms the review is still pending. This means the subprocess runs while holding the lock, but it's fast (<1s for typical diffs) and prevents wasted work.
**Warning signs:** Log shows multiple `git apply --check` invocations for the same review.

## Code Examples

### Extended Schema (db.py)
```python
# Source: SQLite ALTER TABLE docs (sqlite.org/lang_altertable.html)
SCHEMA_SQL = """\
CREATE TABLE IF NOT EXISTS reviews (
    id              TEXT PRIMARY KEY,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending','claimed','in_review',
                                     'approved','changes_requested','closed')),
    intent          TEXT NOT NULL,
    description     TEXT,
    diff            TEXT,
    affected_files  TEXT,
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
"""

# Defensive migrations for existing databases
SCHEMA_MIGRATIONS = [
    "ALTER TABLE reviews ADD COLUMN description TEXT",
    "ALTER TABLE reviews ADD COLUMN diff TEXT",
    "ALTER TABLE reviews ADD COLUMN affected_files TEXT",
]

async def ensure_schema(db: aiosqlite.Connection) -> None:
    """Create tables/indexes, then apply migrations for existing databases."""
    await db.executescript(SCHEMA_SQL)
    for migration in SCHEMA_MIGRATIONS:
        try:
            await db.execute(migration)
        except Exception:
            pass  # Column already exists
```

### Diff Validation (diff_utils.py)
```python
# Source: Python asyncio-subprocess docs, empirically verified 2026-02-16
import asyncio
import json
from unidiff import PatchSet

async def validate_diff(diff_text: str, cwd: str | None = None) -> tuple[bool, str]:
    """Run git apply --check on a diff blob.

    Returns (is_valid, error_detail). Error detail contains git's stderr
    which describes exactly which files/hunks failed.

    Empirically verified exit codes:
    - 0: patch applies cleanly
    - 1: patch fails (context mismatch, file not found)
    - 128: corrupt/malformed patch
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "apply", "--check",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    _stdout, stderr = await proc.communicate(input=diff_text.encode("utf-8"))
    if proc.returncode == 0:
        return True, ""
    return False, stderr.decode("utf-8", errors="replace").strip()


def extract_affected_files(diff_text: str) -> str:
    """Parse unified diff and return JSON string of affected files.

    Returns JSON array: [{"path": "...", "operation": "create|modify|delete",
                          "added": N, "removed": N}, ...]
    """
    try:
        patch = PatchSet(diff_text)
    except Exception:
        return "[]"  # If diff can't be parsed, return empty list
    files = []
    for pf in patch:
        if pf.is_added_file:
            op = "create"
        elif pf.is_removed_file:
            op = "delete"
        else:
            op = "modify"
        files.append({
            "path": pf.path,
            "operation": op,
            "added": pf.added,
            "removed": pf.removed,
        })
    return json.dumps(files)
```

### Extended create_review Tool
```python
# Source: Existing tools.py pattern, extended for Phase 2
@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    description: str | None = None,
    diff: str | None = None,
    review_id: str | None = None,  # For revisions
    ctx: Context = None,
) -> dict:
    """Create a new review or revise an existing one.

    For new reviews: omit review_id. Returns new review_id.
    For revisions: pass existing review_id (must be in changes_requested state).
    Include description (PR-style summary) and diff (unified diff) for proposals.
    """
    app: AppContext = ctx.lifespan_context
    affected_files = extract_affected_files(diff) if diff else None

    if review_id is not None:
        # Revision flow: update existing review
        return await _revise_review(app, review_id, intent, description, diff, affected_files)

    # New review flow
    new_id = str(uuid.uuid4())
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            await app.db.execute(
                """INSERT INTO reviews (id, status, intent, description, diff, affected_files,
                                        agent_type, agent_role, phase, plan, task,
                                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
                (new_id, ReviewStatus.PENDING, intent, description, diff, affected_files,
                 agent_type, agent_role, phase, plan, task),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("create_review", exc)
    return {"review_id": new_id, "status": ReviewStatus.PENDING}
```

### Extended claim_review with Diff Validation
```python
# Source: Existing tools.py pattern + asyncio subprocess pattern
@mcp.tool
async def claim_review(
    review_id: str,
    reviewer_id: str,
    ctx: Context = None,
) -> dict:
    """Claim a pending review. If the review has a diff, validates it with
    git apply --check first. Auto-rejects if diff doesn't apply cleanly."""
    app: AppContext = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT status, diff, intent, description, affected_files FROM reviews WHERE id = ?",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}

            current_status = ReviewStatus(row["status"])
            try:
                validate_transition(current_status, ReviewStatus.CLAIMED)
            except ValueError as exc:
                await app.db.execute("ROLLBACK")
                return {"error": str(exc)}

            # Validate diff if present (locked decision: validate on claim)
            diff_text = row["diff"]
            if diff_text:
                is_valid, error_detail = await validate_diff(diff_text, cwd=app.repo_root)
                if not is_valid:
                    # Auto-reject: transition to changes_requested with error detail
                    await app.db.execute(
                        """UPDATE reviews SET status = ?, verdict_reason = ?,
                           claimed_by = ?, updated_at = datetime('now') WHERE id = ?""",
                        (ReviewStatus.CHANGES_REQUESTED,
                         f"Auto-rejected: diff does not apply cleanly.\n{error_detail}",
                         "broker-validator", review_id),
                    )
                    await app.db.execute("COMMIT")
                    return {
                        "review_id": review_id,
                        "status": ReviewStatus.CHANGES_REQUESTED,
                        "auto_rejected": True,
                        "validation_error": error_detail,
                    }

            # Claim succeeds
            await app.db.execute(
                """UPDATE reviews SET status = ?, claimed_by = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (ReviewStatus.CLAIMED, reviewer_id, review_id),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("claim_review", exc)

    # Return proposal content inline (saves reviewer a round-trip)
    result = {
        "review_id": review_id,
        "status": ReviewStatus.CLAIMED,
        "claimed_by": reviewer_id,
        "intent": row["intent"],
    }
    if row["description"]:
        result["description"] = row["description"]
    if row["affected_files"]:
        result["affected_files"] = row["affected_files"]
    if diff_text:
        result["has_diff"] = True
    return result
```

### Extended submit_verdict with Comment Type
```python
# Source: Existing tools.py pattern, extended for Phase 2
@mcp.tool
async def submit_verdict(
    review_id: str,
    verdict: str,
    reason: str | None = None,
    ctx: Context = None,
) -> dict:
    """Submit a verdict on a claimed review.

    Verdict must be 'approved', 'changes_requested', or 'comment'.
    Notes (reason) required for 'changes_requested' and 'comment'.
    """
    valid_verdicts = {
        "approved": ReviewStatus.APPROVED,
        "changes_requested": ReviewStatus.CHANGES_REQUESTED,
    }
    # 'comment' keeps the review in current state (does not transition)
    if verdict == "comment":
        if not reason:
            return {"error": "Notes (reason) required for 'comment' verdict."}
        # Comment doesn't change state -- just records the feedback
        # This will be implemented by updating verdict_reason without state change
        # ... (comment handling logic)
    elif verdict not in valid_verdicts:
        return {
            "error": f"Invalid verdict: {verdict!r}. Must be 'approved', 'changes_requested', or 'comment'."
        }
    elif verdict in ("changes_requested",) and not reason:
        return {"error": "Notes (reason) required for 'changes_requested' verdict."}

    # ... (existing transition logic)
```

### Git Repo Root Discovery
```python
# Source: git rev-parse docs, for AppContext initialization
async def discover_repo_root() -> str | None:
    """Discover the git repository root directory."""
    proc = await asyncio.create_subprocess_exec(
        "git", "rev-parse", "--show-toplevel",
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    stdout, _ = await proc.communicate()
    if proc.returncode == 0:
        return stdout.decode("utf-8").strip()
    return None
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| Separate proposals/verdicts tables | Columns on reviews table | Phase 2 design decision | Simpler queries, no JOINs, single row per review |
| Format-validate diffs on submission | Trust proposer, validate on claim only | Phase 2 context decision | Diffs can queue before validation; catches actual state at review time |
| approve/reject binary verdicts | approve/request_changes/comment trio | Phase 2 context decision | `comment` allows non-blocking feedback; matches GitHub PR model |

**Deprecated/outdated:**
- Phase 1's `submit_verdict` only accepts `approved` and `changes_requested`. Phase 2 adds `comment` as a third type, but `comment` does NOT cause a state transition -- it records feedback while keeping the review in its current state.

## Open Questions

1. **Comment verdict state behavior**
   - What we know: `comment` is a verdict type that should record feedback. The locked decision says notes are required for `comment`.
   - What's unclear: Should `comment` cause any state transition? In GitHub PRs, a "comment" review doesn't block merging. The most natural mapping is: `comment` stores the reason but does NOT transition state (review stays in `claimed` or `in_review`).
   - Recommendation: Implement `comment` as a no-transition verdict that updates `verdict_reason` without changing `status`. The reviewer can still approve or request_changes afterward.

2. **Diff content in claim_review response size**
   - What we know: `MAX_MCP_OUTPUT_TOKENS` limits tool response size. Large multi-file diffs could exceed this.
   - What's unclear: The exact token limit and whether it causes truncation or error.
   - Recommendation: Return proposal metadata (intent, description, affected_files) inline with `claim_review`. Provide full diff only via `get_proposal` tool. Include `has_diff: true` flag so reviewer knows to call `get_proposal` for the actual diff.

3. **Repo root caching and broker startup location**
   - What we know: The broker may be started from any directory. `git apply --check` needs to run from the git repo root.
   - What's unclear: Whether the broker is always started from inside a git repository.
   - Recommendation: Discover repo root at lifespan startup via `git rev-parse --show-toplevel`. Cache in `AppContext.repo_root`. Fail gracefully if not in a git repo (diff validation returns error, but broker still works for non-diff reviews).

## Sources

### Primary (HIGH confidence)
- [Python asyncio subprocess docs](https://docs.python.org/3/library/asyncio-subprocess.html) -- `create_subprocess_exec` API, PIPE constants, `communicate()` pattern
- [Python asyncio platform support](https://docs.python.org/3/library/asyncio-platforms.html) -- Windows ProactorEventLoop supports subprocesses (default since Python 3.8)
- [Git apply documentation](https://git-scm.com/docs/git-apply) -- `--check` flag behavior, stdin piping with `-`, `--verbose` output
- [SQLite ALTER TABLE](https://www.sqlite.org/lang_altertable.html) -- ADD COLUMN syntax and limitations
- [python-unidiff GitHub](https://github.com/matiasb/python-unidiff) -- PatchSet API, file status detection, v0.7.5
- **Empirical testing** (2026-02-16) -- `git apply --check` exit codes and stderr format verified on Windows in this repository

### Secondary (MEDIUM confidence)
- [MCP Best Practices (philschmid.de)](https://www.philschmid.de/mcp-best-practices) -- Tool naming, parameter design, composition patterns
- [MCP Best Practice Guide](https://mcp-best-practice.github.io/mcp-best-practice/best-practice/) -- Tool design principles
- Phase 1 RESEARCH.md and existing codebase -- Established patterns for tools, db, state machine

### Tertiary (LOW confidence)
- None -- all findings verified against primary or secondary sources

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- Phase 1 stack unchanged; `unidiff` verified via GitHub; asyncio subprocess verified via official docs + empirical testing
- Architecture: HIGH -- Schema evolution approach is standard SQLite. Tool extension pattern follows MCP best practices and existing codebase conventions.
- Diff validation: HIGH -- `git apply --check` behavior empirically verified on Windows with multiple scenarios (success, context mismatch, missing file, corrupt patch, new file creation)
- Tool surface design: MEDIUM -- Recommendations follow MCP best practices but specific tool boundaries involve subjective design judgment (Claude's Discretion area)
- Pitfalls: HIGH -- All pitfalls derive from verified behavior (subprocess blocking, working directory, race conditions) or are preventive patterns from Phase 1 experience

**Research date:** 2026-02-16
**Valid until:** 2026-03-16 (30 days -- stack is stable, patterns are established)
