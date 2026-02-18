# Phase 7: Add Reviewer Lifecycle Management to Broker - Research

**Researched:** 2026-02-18
**Domain:** Subprocess lifecycle management, pool scaling, fenced reclaim, asyncio background tasks
**Confidence:** HIGH

## Summary

This phase transforms the broker from a passive review queue into an active process manager that spawns, scales, drains, and terminates Codex reviewer subprocesses. The core technical challenges are: (1) managing long-lived async subprocesses within the FastMCP lifespan context, (2) implementing ratio-based auto-scaling with a periodic background timer, (3) adding fencing tokens to prevent stale claim verdicts after reclaim, and (4) platform-aware spawning (WSL on Windows, native on Linux/macOS) using shell-free argv-list subprocess invocations.

The existing codebase already uses `asyncio.create_subprocess_exec` (in `diff_utils.py` and `db.py`), the `AppContext` dataclass pattern for shared state, and `asyncio.Lock` for write serialization. These patterns extend naturally to subprocess management. The new reviewer pool manager will live as a new module (`pool.py` or `reviewer_pool.py`) with state tracked in a new `reviewers` SQLite table and lifecycle events recorded in the existing `audit_events` table. Background scaling checks run as `asyncio.Task`s created during the lifespan context manager, cancelled on shutdown.

**Primary recommendation:** Build a `ReviewerPool` class that encapsulates all subprocess management, scaling logic, and reclaim checks. Integrate it into `AppContext` so all tools can access pool state. Use `asyncio.create_subprocess_exec` with `stdin=PIPE` for prompt delivery via `communicate()`, and `asyncio.TaskGroup` (or individual tasks with strong references) for background timers.

<user_constraints>
## User Constraints (from CONTEXT.md)

### Locked Decisions
- Spawn logic lives **inside the broker** server process (FastMCP) -- no separate supervisor
- Broker spawns Codex reviewers as subprocesses, managing their lifecycle directly
- **Platform-aware spawning**: detect OS -- use WSL on Windows, native spawn on Linux/macOS
- Expose MCP tools for manual override: `spawn_reviewer` and `kill_reviewer` alongside auto-scaling
- Codex spawn parameters (model, reasoning_effort, workspace_path, WSL distro) stored in **config.json**, not hardcoded
- **Config validation**: all spawn parameters must be validated before use -- allowlisted model names, path existence checks, no shell metacharacter injection in command construction
- **Shell-free spawn enforcement**: all subprocess invocations must use argv-list form (never shell=True, never `bash -lc`). On WSL: `['wsl', '-d', distro, '--', 'codex', 'exec', ...]` -- fully shell-free argv path. On native Linux/macOS: `['codex', 'exec', ...]` directly. Prohibit any command-string assembly. Tests must verify no shell metacharacter expansion.
- **Auth guardrail for spawn/kill tools**: localhost trusted environment -- same trust model as all other broker tools. spawn_reviewer enforces max pool cap, kill_reviewer only targets broker-managed reviewer IDs (cannot kill arbitrary processes). Rate-limit spawn calls to prevent runaway process creation.
- **Ratio-based scaling**: spawn a new reviewer when pending reviews exceed 3:1 ratio to active reviewers
- **Min 0, max configurable** pool bounds -- pool can scale to zero when idle, cold-starts on first review
- **Idle timeout** scale-down: terminate reviewers idle longer than a configurable duration
- **Reactive + periodic** check cadence: evaluate scaling on every `create_review` AND on a background timer for scale-down checks
- **Graceful drain** on TTL expiry: mark reviewer for termination, let it finish current review, then kill
- **Session-scoped naming with unique suffix**: display names follow `codex-r1`, `codex-r2`, ... pattern (counter resets per broker session for readability), BUT internal reviewer IDs include a unique session token (e.g., `codex-r1-a7f3`) to prevent collision with stale claims from previous broker sessions
- **Per-reviewer stats** tracked: reviews completed, average review time, approval rate per instance
- **Full lifecycle audit**: log spawn, drain-start, terminate events in `audit_events` table
- **Self-claim** stays (current model): reviewers poll and claim from pending queue
- **Claim timeout of 20 minutes**: if a reviewer claims but doesn't verdict within 20 min, reclaim and return to pending
- **Fenced reclaim**: use a fencing token (claim generation counter) so stale verdicts are rejected
- **No category routing**: all reviewers are generalists
- **Reviewer prompt template** stored in a **config file**, not baked into Python code

### Claude's Discretion
- Background timer interval for periodic scaling checks
- Exact subprocess management implementation (asyncio.subprocess vs subprocess module)
- Schema design for reviewer tracking table and fence token column
- How to detect platform (Windows vs Linux/macOS) for WSL vs native spawning
- Idle timeout default value
- Max TTL default value
- Rate-limit strategy for spawn calls (e.g., cooldown period between spawns)
- Session token generation strategy (UUID prefix, random hex, etc.)

### Deferred Ideas (OUT OF SCOPE)
- Master-reviewer orchestration layer (single verdict authority, file/symbol ownership, delegation to sub-reviewers)
- Category-based reviewer specialization
- Multi-reviewer consensus / quorum voting
- Heartbeat-based health monitoring (periodic ping from reviewer to broker)
</user_constraints>

## Standard Stack

### Core (already in project)
| Library | Version | Purpose | Why Standard |
|---------|---------|---------|--------------|
| asyncio (stdlib) | Python 3.12+ | Subprocess management, background tasks, event loop | Native async subprocess via `create_subprocess_exec`, `TaskGroup` for structured concurrency |
| aiosqlite | >=0.22,<1 | Async SQLite access | Already used for all DB operations; reviewer table follows same pattern |
| FastMCP | >=2.14,<3 | MCP server framework | Lifespan context manager for startup/shutdown hooks |

### Supporting (already in project)
| Library | Version | Purpose | When to Use |
|---------|---------|---------|-------------|
| pydantic | (via FastMCP dep) | Config validation models | Validate spawn config from config.json |
| unidiff | >=0.7.5,<1 | Diff parsing | Already used, no changes needed |

### New Dependencies: NONE
No new external dependencies are needed. All subprocess management, background tasks, and platform detection use Python stdlib modules (`asyncio`, `os`, `sys`, `platform`, `secrets`, `json`, `pathlib`).

### Alternatives Considered
| Instead of | Could Use | Tradeoff |
|------------|-----------|----------|
| `asyncio.create_subprocess_exec` | `subprocess.Popen` in thread pool | Sync subprocess would need `run_in_executor`, adding complexity; asyncio native is cleaner and already used in codebase |
| Background `asyncio.Task` | `aiojobs` scheduler | External dependency unnecessary; simple periodic timer task is sufficient |
| Raw SQLite migration | Alembic | Overkill for single-table additions; existing `SCHEMA_MIGRATIONS` pattern works |

## Architecture Patterns

### Recommended Project Structure (new files)
```
src/gsd_review_broker/
  pool.py              # ReviewerPool class: spawn, drain, kill, scaling, reclaim
  config_schema.py     # Pydantic models for spawn config validation
  platform_spawn.py    # Platform detection + argv builder (WSL vs native)
  server.py            # (modified) Add pool to lifespan, start background tasks
  db.py                # (modified) Add reviewers table migration, extend AppContext
  tools.py             # (modified) Add spawn_reviewer, kill_reviewer tools; modify submit_verdict for fence check
  state_machine.py     # (modified) Add CLAIMED -> PENDING transition for reclaim
  models.py            # (modified) Add ReviewerState enum, new AuditEventTypes
```

A `reviewer_prompt.md` template file in the project root or config directory.

### Pattern 1: ReviewerPool as AppContext Extension
**What:** A `ReviewerPool` class added to `AppContext` that holds all in-memory pool state (active processes, drain flags, scaling state) and references the shared DB connection for persistence.
**When to use:** All reviewer lifecycle operations go through this class.
**Example:**
```python
# Source: Extends existing AppContext pattern in db.py
@dataclass
class ReviewerPool:
    """Manages the lifecycle of Codex reviewer subprocesses."""
    session_token: str
    _counter: int = 0
    _processes: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    _draining: set[str] = field(default_factory=set)
    _last_spawn_time: float = 0.0
    _spawn_cooldown: float = 10.0  # seconds between spawns
    _config: SpawnConfig | None = None

@dataclass
class AppContext:
    db: aiosqlite.Connection
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    repo_root: str | None = None
    notifications: NotificationBus = field(default_factory=NotificationBus)
    pool: ReviewerPool | None = None  # NEW
```

### Pattern 2: Background Task in Lifespan
**What:** Start periodic scaling/reclaim check tasks during broker lifespan, cancel on shutdown.
**When to use:** For the periodic timer that checks idle timeout and claim timeout.
**Example:**
```python
# Source: Python 3.12 asyncio docs + existing broker_lifespan pattern
@asynccontextmanager
async def broker_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    # ... existing setup ...
    pool = ReviewerPool(session_token=secrets.token_hex(4))
    ctx = AppContext(db=db, repo_root=repo_root, pool=pool)

    # Start background tasks
    scaling_task = asyncio.create_task(_periodic_scaling_check(ctx))
    reclaim_task = asyncio.create_task(_periodic_reclaim_check(ctx))

    # Reclaim stale reviews from previous sessions on startup
    await _reclaim_stale_session_claims(ctx)

    try:
        yield ctx
    finally:
        # Cancel background tasks
        scaling_task.cancel()
        reclaim_task.cancel()
        with suppress(asyncio.CancelledError):
            await scaling_task
        with suppress(asyncio.CancelledError):
            await reclaim_task
        # Drain and kill all reviewers
        await pool.shutdown_all()
        # ... existing cleanup ...
```

### Pattern 3: Platform-Aware Argv Builder (Shell-Free)
**What:** Build subprocess argv lists based on detected platform, never using shell strings.
**When to use:** Every subprocess spawn.
**Example:**
```python
# Source: Codex CLI reference + existing PowerShell script patterns
import os
import sys

def detect_platform() -> str:
    """Detect runtime platform for spawn strategy."""
    if os.name == "nt":
        return "windows"  # Use WSL
    return "native"  # Linux/macOS, direct codex exec

def build_codex_argv(
    config: SpawnConfig,
    prompt_text: str,  # Passed via stdin, not in argv
) -> list[str]:
    """Build shell-free argv list for codex exec."""
    codex_args = [
        "codex", "exec",
        "--sandbox", "read-only",
        "--ephemeral",
        "--model", config.model,
        "-c", f"model_reasoning_effort={config.reasoning_effort}",
        "-C", config.workspace_path,
        "-",  # Read prompt from stdin
    ]

    if detect_platform() == "windows":
        return [
            "wsl", "-d", config.wsl_distro,
            "--", *codex_args,
        ]
    return codex_args
```

### Pattern 4: Fencing Token for Claim Reclaim
**What:** A monotonically increasing `claim_generation` counter on the `reviews` table that increments on every claim/reclaim. The `submit_verdict` tool checks that the reviewer's claim generation matches the current one before accepting the verdict.
**When to use:** Prevents stale verdicts after a timed-out claim is reclaimed.
**Example:**
```python
# In claim_review: store claim_generation
# In reclaim: increment claim_generation, set status back to pending
# In submit_verdict: check claim_generation matches

# Schema addition:
# ALTER TABLE reviews ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0

# Reclaim logic (inside write_lock):
await db.execute("""
    UPDATE reviews
    SET status = 'pending',
        claimed_by = NULL,
        claim_generation = claim_generation + 1,
        updated_at = datetime('now')
    WHERE id = ? AND status = 'claimed'
""", (review_id,))

# submit_verdict check:
# Reviewer passes their known claim_generation when submitting
# If it doesn't match current, reject as stale
```

### Pattern 5: Graceful Drain + TTL
**What:** When a reviewer reaches TTL or idle timeout, mark it as "draining" (in-memory flag). Don't assign new work. When its current review completes (or it has none), terminate the process.
**When to use:** TTL expiry, idle timeout, explicit kill_reviewer.
**Example:**
```python
async def drain_reviewer(self, reviewer_id: str) -> None:
    """Mark reviewer for graceful drain."""
    self._draining.add(reviewer_id)
    # Record audit event
    await record_event(self.db, None, "reviewer_drain_start",
                       actor="pool-manager",
                       metadata={"reviewer_id": reviewer_id})

    # Check if currently processing a review
    if not self._is_reviewing(reviewer_id):
        await self._terminate_reviewer(reviewer_id)

async def _terminate_reviewer(self, reviewer_id: str) -> None:
    """Terminate a reviewer subprocess."""
    proc = self._processes.get(reviewer_id)
    if proc and proc.returncode is None:
        proc.terminate()
        try:
            await asyncio.wait_for(proc.wait(), timeout=10.0)
        except TimeoutError:
            proc.kill()
            await proc.wait()
    self._processes.pop(reviewer_id, None)
    self._draining.discard(reviewer_id)
    # Record audit event + update DB
```

### Anti-Patterns to Avoid
- **shell=True or bash -lc**: Never pass commands as shell strings. The existing PowerShell script uses `bash -lc` with string interpolation -- this is exactly what we are replacing. Use `['wsl', '-d', distro, '--', 'codex', ...]` argv-list form.
- **Holding write_lock during subprocess spawn**: Subprocess creation can take seconds. Never hold the DB write lock while spawning. Spawn outside the lock, then record the result inside the lock.
- **Storing Process objects in SQLite**: asyncio.subprocess.Process objects are runtime-only. Track them in-memory (dict in ReviewerPool), persist metadata (reviewer_id, spawn_time, status) in SQLite.
- **Polling the database for scaling decisions**: Use the reactive pattern (check on create_review) plus a timer. Don't poll the DB every second.
- **Hard-killing without drain**: Always attempt graceful drain first. Hard kill only after timeout on the drain wait.

## Don't Hand-Roll

| Problem | Don't Build | Use Instead | Why |
|---------|-------------|-------------|-----|
| Subprocess lifecycle | Custom process wrapper | `asyncio.create_subprocess_exec` + `communicate()` | Python stdlib handles pipes, signals, cleanup; already used in `diff_utils.py` and `db.py` |
| Monotonic fencing tokens | Custom sequence generator | SQLite `claim_generation INTEGER` column with `+1` in UPDATE | Database-level atomicity under write_lock; survives process restart |
| Unique session tokens | Custom PRNG | `secrets.token_hex(4)` | Cryptographically random, stdlib, 8 hex chars = 4 billion values |
| Config validation | Manual if/elif chains | Pydantic BaseModel with Field validators | Already a project dependency (via FastMCP), handles type coercion and error messages |
| Platform detection | Manual registry/env checks | `os.name == "nt"` | Simple, reliable, already used in codebase (`db.py` line 166) |
| Periodic timer | `while True: await asyncio.sleep(N)` loop | Same, but with `asyncio.CancelledError` handling | This IS the standard pattern for asyncio periodic tasks; no library needed |
| Schema migrations | Migration framework | Existing `SCHEMA_MIGRATIONS` list pattern | Already established in `db.py`; just append new ALTER TABLE statements |

**Key insight:** The entire phase uses stdlib asyncio and SQLite. No new dependencies. The complexity is in the orchestration logic (scaling decisions, drain coordination, reclaim checks), not in the infrastructure.

## Common Pitfalls

### Pitfall 1: Orphaned Subprocesses on Broker Crash
**What goes wrong:** If the broker process crashes without running the lifespan cleanup, spawned Codex reviewer subprocesses become orphans consuming resources indefinitely.
**Why it happens:** Python `asyncio.create_subprocess_exec` creates real OS processes. If the parent exits without terminating them, they persist.
**How to avoid:** On startup, the broker should check for stale reviewer records in the DB (from previous sessions with different session tokens) and mark them as terminated. The actual OS processes from a crashed broker will eventually terminate when their stdin closes (Codex exec exits when stdin EOF is received). Record PIDs in the reviewers table so operators can manually clean up if needed.
**Warning signs:** Growing process count on the host; reviews stuck in "claimed" state from dead reviewers.

### Pitfall 2: Race Between Reclaim and Late Verdict
**What goes wrong:** Reviewer A times out, review is reclaimed to pending, Reviewer B claims it, but Reviewer A's verdict arrives and overwrites Reviewer B's work-in-progress.
**Why it happens:** Without fencing, `submit_verdict` just checks status = claimed.
**How to avoid:** The `claim_generation` fencing token. Every claim/reclaim increments the counter. `submit_verdict` must receive and validate the claim_generation. If mismatched, reject the verdict with a clear error message.
**Warning signs:** Reviews showing unexpected verdict content; audit log showing verdicts from reviewers that were already timed out.

### Pitfall 3: WSL Path Translation
**What goes wrong:** Windows paths like `C:\Projects\gsd-tandem` don't work inside WSL. The codex process fails to find the workspace.
**Why it happens:** WSL expects `/mnt/c/Projects/gsd-tandem` format.
**How to avoid:** Store the WSL workspace path explicitly in config.json (as the existing PowerShell script does). Do NOT try to auto-translate Windows paths. The config field is `workspace_wsl_path` on Windows, `workspace_path` on native.
**Warning signs:** Codex reviewers spawning but immediately failing with "workspace not found" errors.

### Pitfall 4: Subprocess Spawn Rate Explosion
**What goes wrong:** A burst of `create_review` calls triggers many simultaneous spawns, exceeding the max pool or overwhelming the system.
**Why it happens:** Reactive scaling (check on every create_review) without rate limiting.
**How to avoid:** Rate-limit spawns with a cooldown period (e.g., 10 seconds between spawns). Check `time.monotonic() - last_spawn_time > cooldown` before spawning. Also enforce the max pool cap as a hard limit.
**Warning signs:** Many codex processes starting simultaneously; high CPU/memory usage spikes.

### Pitfall 5: stdin Pipe Deadlock with Large Prompts
**What goes wrong:** Writing a large prompt to subprocess stdin while not reading stdout/stderr can deadlock if OS pipe buffers fill.
**Why it happens:** OS pipe buffers are typically 64KB. If the subprocess writes back before reading all stdin, buffers fill in both directions.
**How to avoid:** Use `proc.communicate(input=prompt_bytes)` which handles stdin write + stdout/stderr read concurrently. Never use `proc.stdin.write()` + `proc.stdout.read()` separately.
**Warning signs:** Spawn hangs indefinitely; reviewer never starts processing.

### Pitfall 6: Windows ProactorEventLoop Subprocess Behavior
**What goes wrong:** Subprocess terminate/kill behaves differently on Windows (both call TerminateProcess, no SIGTERM concept). Graceful drain may not work as expected.
**Why it happens:** On Windows, `process.terminate()` and `process.kill()` both call `TerminateProcess()`. There's no SIGTERM. However, since we're using WSL on Windows, the actual codex process runs inside WSL (Linux), so signals do work -- but they're sent through the WSL shim.
**How to avoid:** On Windows, terminate the WSL wrapper process. WSL should propagate the signal to child processes. Test this behavior specifically. If WSL doesn't propagate cleanly, use the `wsl --terminate` command as fallback.
**Warning signs:** Reviewer processes persisting after `terminate()` on Windows.

### Pitfall 7: Concurrent Scaling Decisions
**What goes wrong:** Two concurrent `create_review` calls both evaluate scaling and both decide to spawn, exceeding the pool cap by 1.
**Why it happens:** Scaling check runs outside write_lock (it's a read operation), spawn decision is non-atomic.
**How to avoid:** Use `ReviewerPool._spawn_lock` (a separate asyncio.Lock) to serialize scaling decisions. Check pool count inside the lock before spawning.
**Warning signs:** Pool size exceeding configured maximum.

## Code Examples

### Example 1: Subprocess Spawn with stdin Prompt Delivery
```python
# Source: Python 3.12 asyncio subprocess docs + Codex CLI reference
async def _spawn_codex_reviewer(
    argv: list[str],
    prompt: str,
) -> asyncio.subprocess.Process:
    """Spawn a Codex reviewer subprocess with prompt via stdin."""
    proc = await asyncio.create_subprocess_exec(
        *argv,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
    )
    # Use communicate() to write prompt and let codex run
    # NOTE: communicate() waits for process to finish.
    # For long-running reviewers, we write stdin then close it,
    # letting the process run independently.
    proc.stdin.write(prompt.encode("utf-8"))
    proc.stdin.close()
    await proc.stdin.drain()
    return proc
    # IMPORTANT: Keep a strong reference to `proc` in ReviewerPool._processes
    # to prevent garbage collection from killing the subprocess.
```

**CORRECTION:** For long-running processes like Codex reviewers that run indefinitely, we should NOT use `communicate()` because it waits for process exit. Instead, write to stdin, close it, and let the process run. Monitor it via `proc.returncode` checks in the periodic timer.

### Example 2: Periodic Background Timer
```python
# Source: Python asyncio docs + standard pattern
async def _periodic_scaling_check(ctx: AppContext, interval: float = 30.0) -> None:
    """Background task: periodic scale-down and reclaim checks."""
    while True:
        try:
            await asyncio.sleep(interval)
            await ctx.pool.check_idle_timeouts(ctx)
            await ctx.pool.check_claim_timeouts(ctx)
            await ctx.pool.check_ttl_expiry(ctx)
        except asyncio.CancelledError:
            raise  # Propagate cancellation for clean shutdown
        except Exception:
            logger.exception("Error in periodic scaling check")
            # Don't crash the background task on transient errors
```

### Example 3: Schema Migration for Reviewers Table and Fence Token
```python
# Source: Existing SCHEMA_MIGRATIONS pattern in db.py
SCHEMA_MIGRATIONS: list[str] = [
    # ... existing migrations ...

    # Phase 7 migrations -- reviewer pool
    """CREATE TABLE IF NOT EXISTS reviewers (
        id              TEXT PRIMARY KEY,
        display_name    TEXT NOT NULL,
        session_token   TEXT NOT NULL,
        status          TEXT NOT NULL DEFAULT 'active'
                        CHECK(status IN ('active','draining','terminated')),
        pid             INTEGER,
        spawned_at      TEXT NOT NULL DEFAULT (datetime('now')),
        terminated_at   TEXT,
        reviews_completed INTEGER NOT NULL DEFAULT 0,
        total_review_seconds REAL NOT NULL DEFAULT 0.0,
        approvals       INTEGER NOT NULL DEFAULT 0,
        rejections      INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_reviewers_session ON reviewers(session_token)",
    "CREATE INDEX IF NOT EXISTS idx_reviewers_status ON reviewers(status)",

    # Fence token on reviews table
    "ALTER TABLE reviews ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reviews ADD COLUMN claimed_at TEXT",
]
```

### Example 4: Fenced Verdict Submission
```python
# Source: Fencing token pattern from distributed systems literature
# Modified submit_verdict to accept and check claim_generation
async def submit_verdict(
    review_id: str,
    verdict: str,
    reason: str | None = None,
    counter_patch: str | None = None,
    claim_generation: int | None = None,  # NEW: fence token
    ctx: Context = None,
) -> dict:
    # ... existing validation ...
    async with app.write_lock:
        await app.db.execute("BEGIN IMMEDIATE")
        cursor = await app.db.execute(
            "SELECT status, claim_generation FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        # ... existing checks ...

        # Fence check: if caller provided claim_generation, verify it matches
        if claim_generation is not None:
            if row["claim_generation"] != claim_generation:
                await app.db.execute("ROLLBACK")
                return {
                    "error": "Stale claim: review was reclaimed since your claim. "
                    f"Your generation={claim_generation}, "
                    f"current={row['claim_generation']}"
                }
        # ... proceed with verdict ...
```

### Example 5: Config Validation with Pydantic
```python
# Source: Pydantic BaseModel pattern (already used in models.py)
from pydantic import BaseModel, Field, field_validator

ALLOWED_MODELS = {
    "gpt-5.3-codex", "gpt-5-codex", "o3", "o3-pro",
    "codex-mini", "gpt-5",
}

class SpawnConfig(BaseModel):
    """Validated configuration for spawning Codex reviewers."""
    model: str = Field(default="gpt-5.3-codex")
    reasoning_effort: str = Field(default="high")
    workspace_path: str  # Native path or WSL path depending on platform
    wsl_distro: str = Field(default="Ubuntu")
    max_pool_size: int = Field(default=3, ge=1, le=10)
    idle_timeout_seconds: float = Field(default=300.0, ge=60.0)
    max_ttl_seconds: float = Field(default=3600.0, ge=300.0)
    claim_timeout_seconds: float = Field(default=1200.0, ge=60.0)  # 20 min
    prompt_template_path: str = Field(default="reviewer_prompt.md")

    @field_validator("model")
    @classmethod
    def validate_model(cls, v: str) -> str:
        if v not in ALLOWED_MODELS:
            raise ValueError(f"Model {v!r} not in allowlist: {sorted(ALLOWED_MODELS)}")
        return v

    @field_validator("reasoning_effort")
    @classmethod
    def validate_reasoning_effort(cls, v: str) -> str:
        if v not in ("low", "medium", "high"):
            raise ValueError(f"reasoning_effort must be low/medium/high, got {v!r}")
        return v
```

## State of the Art

| Old Approach | Current Approach | When Changed | Impact |
|--------------|------------------|--------------|--------|
| External PowerShell script spawns single Codex reviewer | Broker-internal pool manager spawns/scales multiple reviewers | Phase 7 (this phase) | Removes manual intervention; enables auto-scaling |
| No claim timeout | 20-minute claim timeout with fenced reclaim | Phase 7 (this phase) | Prevents reviews stuck in claimed state from dead reviewers |
| `bash -lc` shell-string command assembly | Shell-free argv-list subprocess | Phase 7 (this phase) | Eliminates shell injection risk; cleaner subprocess management |
| Single reviewer, no stats | Per-reviewer stats in SQLite | Phase 7 (this phase) | Enables pool health monitoring |

**Deprecated/outdated:**
- `scripts/start-broker-reviewer.ps1`: Will be superseded by broker-internal spawning. Keep it as a fallback/reference but document it as legacy.

## Discretion Recommendations

Based on research, here are recommendations for the areas left to Claude's discretion:

| Area | Recommendation | Rationale |
|------|----------------|-----------|
| Background timer interval | **30 seconds** | Frequent enough to catch idle timeouts promptly, infrequent enough to avoid unnecessary DB queries. Scale-up is reactive (on create_review), so the timer mainly handles scale-down and reclaim. |
| Subprocess implementation | **`asyncio.create_subprocess_exec`** | Already used in `diff_utils.py` and `db.py`. Integrates naturally with the event loop. No need for `subprocess.Popen` + executor. |
| Reviewer tracking schema | See Example 3 above: `reviewers` table with id, display_name, session_token, status, pid, timestamps, and stats counters | Persists across queries; stats survive tool restarts within a session; terminated records kept for history. |
| Platform detection | **`os.name == "nt"`** for Windows detection | Already used in `db.py` line 166 and 193. Simple, reliable, consistent with codebase. |
| Idle timeout default | **5 minutes (300 seconds)** | Long enough that reviewers aren't churned during normal review bursts; short enough to reclaim resources during idle periods. |
| Max TTL default | **1 hour (3600 seconds)** | Codex sessions can accumulate context; recycling every hour keeps memory fresh. Configurable for longer-running scenarios. |
| Rate-limit strategy | **10-second cooldown between spawns** | Prevents burst spawning while allowing responsive scale-up. Track `last_spawn_time` in ReviewerPool; check `time.monotonic() - last_spawn_time > cooldown`. |
| Session token generation | **`secrets.token_hex(4)`** producing 8 hex chars (e.g., `a7f3b2e1`) | Cryptographically random, 4 billion possible values, readable in logs. Generated once at broker startup. |
| Fence token column | **`claim_generation INTEGER NOT NULL DEFAULT 0`** on reviews table | Simple integer counter; incremented on each claim/reclaim; checked in submit_verdict. No need for UUIDs or timestamps. |

## Schema Design Detail

### New `reviewers` Table
```sql
CREATE TABLE IF NOT EXISTS reviewers (
    id              TEXT PRIMARY KEY,      -- e.g., "codex-r1-a7f3b2e1"
    display_name    TEXT NOT NULL,         -- e.g., "codex-r1"
    session_token   TEXT NOT NULL,         -- broker session token
    status          TEXT NOT NULL DEFAULT 'active'
                    CHECK(status IN ('active','draining','terminated')),
    pid             INTEGER,              -- OS process ID for debugging
    spawned_at      TEXT NOT NULL DEFAULT (datetime('now')),
    last_active_at  TEXT NOT NULL DEFAULT (datetime('now')),
    terminated_at   TEXT,
    reviews_completed INTEGER NOT NULL DEFAULT 0,
    total_review_seconds REAL NOT NULL DEFAULT 0.0,
    approvals       INTEGER NOT NULL DEFAULT 0,
    rejections      INTEGER NOT NULL DEFAULT 0
);
```

### Reviews Table Additions
```sql
ALTER TABLE reviews ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0;
ALTER TABLE reviews ADD COLUMN claimed_at TEXT;  -- timestamp for claim timeout detection
```

### New Audit Event Types
- `reviewer_spawned` -- actor: pool-manager, metadata: {reviewer_id, display_name, pid}
- `reviewer_drain_start` -- actor: pool-manager, metadata: {reviewer_id, reason: "ttl"|"idle"|"manual"}
- `reviewer_terminated` -- actor: pool-manager, metadata: {reviewer_id, reviews_completed, exit_code}
- `review_reclaimed` -- actor: pool-manager, old_status: claimed, new_status: pending, metadata: {old_reviewer, reason: "claim_timeout", claim_generation}

## Codex Reviewer Prompt Template

The prompt should be stored in a markdown file (e.g., `tools/gsd-review-broker/reviewer_prompt.md`) and loaded at spawn time. Template variables are interpolated before passing to Codex stdin.

Key content (based on existing PowerShell script):
```
You are reviewer "{reviewer_id}". Loop indefinitely:
1) list_reviews(status="pending", wait=true).
2) When reviews appear, process in order; call claim_review(review_id=ID, reviewer_id="{reviewer_id}").
3) get_proposal(review_id=ID).
4) Perform thorough review focused on correctness, regressions, security, data integrity, missing tests.
5) submit_verdict(review_id=ID, verdict=..., reason=..., claim_generation=<from_claim_response>).
6) If approved or changes_requested, close_review(review_id=ID).
7) Loop.
Always include reasoning and prioritize catching real risks.
```

## State Machine Changes

The `VALID_TRANSITIONS` dict in `state_machine.py` needs one addition:
```python
ReviewStatus.CLAIMED: {
    ReviewStatus.IN_REVIEW,
    ReviewStatus.APPROVED,
    ReviewStatus.CHANGES_REQUESTED,
    ReviewStatus.PENDING,  # NEW: reclaim on timeout
},
```

This allows `claimed -> pending` for the reclaim operation. The transition should only be performed by the pool manager's reclaim check, not by arbitrary callers. The reclaim function validates this internally.

## Open Questions

1. **Codex MCP Server Configuration for Reviewers**
   - What we know: Codex exec needs an MCP config to know about the broker. The existing PowerShell script doesn't explicitly pass an MCP config -- it may rely on a global config or the workspace's `.mcp.json`.
   - What's unclear: How does Codex exec discover the broker's MCP endpoint? Does it read `.mcp.json` from the workspace, or does it need explicit `--mcp-server` flags?
   - Recommendation: Test with the existing `.mcp.json` in the workspace. If Codex doesn't auto-discover it, add an explicit MCP config flag to the argv builder. The `-C` workspace flag should cause Codex to read the workspace's `.mcp.json`.

2. **NVM Initialization in WSL**
   - What we know: The existing PowerShell script includes `if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi` for NVM initialization, but this requires `bash -lc` (shell string).
   - What's unclear: Does `codex` work without NVM? If installed globally or via a non-NVM method, it should be on PATH already in WSL.
   - Recommendation: Shell-free argv avoids NVM sourcing. Ensure `codex` is on the WSL user's default PATH (installed globally or via a method that adds to PATH in `.profile`). Document this as a setup requirement. If NVM is required, the user should add the NVM-managed node/bin to their `.profile` PATH.

3. **Claim Generation Backward Compatibility**
   - What we know: Adding `claim_generation` to `submit_verdict` changes the tool's signature.
   - What's unclear: Will existing callers (manual reviewers, other tools) break if they don't pass `claim_generation`?
   - Recommendation: Make `claim_generation` optional in `submit_verdict`. If not provided, skip the fence check (backward-compatible). Only broker-managed reviewers will pass it. This preserves compatibility with manual/external reviewers.

## Sources

### Primary (HIGH confidence)
- [Python 3.14 asyncio subprocess docs](https://docs.python.org/3/library/asyncio-subprocess.html) -- create_subprocess_exec API, Process methods, PIPE constants
- [Python 3.14 asyncio task docs](https://docs.python.org/3/library/asyncio-task.html) -- TaskGroup, create_task, CancelledError handling
- [Codex CLI command line reference](https://developers.openai.com/codex/cli/reference/) -- exec flags, --sandbox, --ephemeral, -C, -, --model
- [Codex non-interactive mode docs](https://developers.openai.com/codex/noninteractive/) -- exec usage patterns, stdin piping
- Existing codebase: `db.py`, `tools.py`, `diff_utils.py`, `server.py` -- established patterns for asyncio subprocess, write_lock, migrations, AppContext

### Secondary (MEDIUM confidence)
- [FastMCP background tasks docs](https://gofastmcp.com/servers/tasks) -- task=True decorator pattern (not directly used; we use lifespan tasks instead)
- [FastMCP lifespan discussion](https://github.com/jlowin/fastmcp/discussions/1763) -- lifespan context manager patterns
- [Fencing tokens pattern](https://levelup.gitconnected.com/beyond-the-lock-why-fencing-tokens-are-essential-5be0857d5a6a) -- monotonic counter, stale write prevention
- [Python platform detection](https://www.pythonmorsels.com/operating-system-checks/) -- os.name, sys.platform, platform.system()

### Tertiary (LOW confidence)
- [WSL subprocess signal propagation](https://github.com/python/cpython/issues/88050) -- terminate/kill behavior through WSL shim (needs testing)

## Metadata

**Confidence breakdown:**
- Standard stack: HIGH -- no new dependencies, all patterns already established in codebase
- Architecture: HIGH -- extends existing AppContext/lifespan/migration patterns directly
- Subprocess management: HIGH -- asyncio.create_subprocess_exec well-documented, already used in project
- Fencing tokens: HIGH -- well-understood distributed systems pattern, simple integer column
- Platform spawning (native): HIGH -- straightforward argv-list with codex exec
- Platform spawning (WSL): MEDIUM -- WSL signal propagation and NVM PATH need runtime testing
- Pitfalls: HIGH -- based on real codebase analysis and documented asyncio edge cases

**Research date:** 2026-02-18
**Valid until:** 2026-03-18 (30 days -- stable domain, stdlib-based)
