"""Database connection, schema management, and lifespan for the GSD Review Broker."""

from __future__ import annotations

import asyncio
import logging
import os
import secrets
import sys
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import aiosqlite
from fastmcp import FastMCP

from gsd_review_broker.audit import record_event
from gsd_review_broker.config_schema import load_spawn_config
from gsd_review_broker.notifications import QUEUE_TOPIC, NotificationBus
from gsd_review_broker.pool import ReviewerPool

DB_FILENAME = "codex_review_broker.sqlite3"
DB_CONFIG_DIRNAME = "gsd-review-broker"
DB_PATH_ENV_VAR = "BROKER_DB_PATH"
CONFIG_PATH_ENV_VAR = "BROKER_CONFIG_PATH"
REPO_ROOT_ENV_VAR = "BROKER_REPO_ROOT"
logger = logging.getLogger("gsd_review_broker")
_PROACTOR_CONNECTION_LOST_CALLBACK = "_ProactorBasePipeTransport._call_connection_lost"

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
    project         TEXT,
    claimed_by      TEXT,
    verdict_reason  TEXT,
    parent_id       TEXT REFERENCES reviews(id),
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_reviews_status ON reviews(status);
CREATE INDEX IF NOT EXISTS idx_reviews_parent ON reviews(parent_id);

CREATE TABLE IF NOT EXISTS messages (
    id          TEXT PRIMARY KEY,
    review_id   TEXT NOT NULL REFERENCES reviews(id),
    sender_role TEXT NOT NULL CHECK(sender_role IN ('proposer', 'reviewer')),
    round       INTEGER NOT NULL DEFAULT 1,
    body        TEXT NOT NULL,
    metadata    TEXT,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_messages_review ON messages(review_id, round);
"""

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
    # Phase 4 migrations
    "ALTER TABLE reviews ADD COLUMN category TEXT",
    # Phase 6 migrations
    "ALTER TABLE reviews ADD COLUMN skip_diff_validation INTEGER NOT NULL DEFAULT 0",
    # Phase 7 migrations
    "ALTER TABLE reviews ADD COLUMN project TEXT",
    # Phase 5 migrations -- audit_events table
    """CREATE TABLE IF NOT EXISTS audit_events (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        review_id   TEXT,
        event_type  TEXT NOT NULL,
        actor       TEXT,
        old_status  TEXT,
        new_status  TEXT,
        metadata    TEXT,
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
    )""",
    "CREATE INDEX IF NOT EXISTS idx_audit_review ON audit_events(review_id)",
    "CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)",
    # Phase 7 migrations -- reviewer pool
    """CREATE TABLE IF NOT EXISTS reviewers (
        id                  TEXT PRIMARY KEY,
        display_name        TEXT NOT NULL,
        session_token       TEXT NOT NULL,
        status              TEXT NOT NULL DEFAULT 'active'
                            CHECK(status IN ('active', 'draining', 'terminated')),
        pid                 INTEGER,
        spawned_at          TEXT NOT NULL DEFAULT (datetime('now')),
        last_active_at      TEXT NOT NULL DEFAULT (datetime('now')),
        terminated_at       TEXT,
        reviews_completed   INTEGER NOT NULL DEFAULT 0,
        total_review_seconds REAL NOT NULL DEFAULT 0.0,
        approvals           INTEGER NOT NULL DEFAULT 0,
        rejections          INTEGER NOT NULL DEFAULT 0
    )""",
    "CREATE INDEX IF NOT EXISTS idx_reviewers_session ON reviewers(session_token)",
    "CREATE INDEX IF NOT EXISTS idx_reviewers_status ON reviewers(status)",
    "ALTER TABLE reviews ADD COLUMN claim_generation INTEGER NOT NULL DEFAULT 0",
    "ALTER TABLE reviews ADD COLUMN claimed_at TEXT",
]


@dataclass
class AppContext:
    """Application context holding the database connection."""

    db: aiosqlite.Connection
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    repo_root: str | None = None
    notifications: NotificationBus = field(default_factory=NotificationBus)
    pool: ReviewerPool | None = None


async def ensure_schema(db: aiosqlite.Connection) -> None:
    """Create tables and indexes if they don't exist, then apply migrations."""
    await db.executescript(SCHEMA_SQL)
    for migration in SCHEMA_MIGRATIONS:
        try:
            await db.execute(migration)
        except aiosqlite.OperationalError as exc:
            # Idempotent migration: ignore only duplicate-column errors.
            if "duplicate column name" not in str(exc).lower():
                raise
    if await _audit_events_review_id_not_null(db):
        await _migrate_audit_events_review_id_nullable(db)


async def _audit_events_review_id_not_null(db: aiosqlite.Connection) -> bool:
    """Return True when legacy audit_events.review_id still has NOT NULL."""
    cursor = await db.execute(
        "SELECT name FROM sqlite_master WHERE type = 'table' AND name = 'audit_events'"
    )
    table_row = await cursor.fetchone()
    if table_row is None:
        return False

    cursor = await db.execute("PRAGMA table_info(audit_events)")
    columns = await cursor.fetchall()
    for column in columns:
        if column["name"] == "review_id":
            return bool(column["notnull"])
    return False


async def _migrate_audit_events_review_id_nullable(db: aiosqlite.Connection) -> None:
    """Rebuild legacy audit_events table so review_id accepts NULL values."""
    try:
        await db.execute("BEGIN IMMEDIATE")
        await db.execute(
            """CREATE TABLE audit_events_new (
                id          INTEGER PRIMARY KEY AUTOINCREMENT,
                review_id   TEXT,
                event_type  TEXT NOT NULL,
                actor       TEXT,
                old_status  TEXT,
                new_status  TEXT,
                metadata    TEXT,
                created_at  TEXT NOT NULL
                            DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
            )"""
        )
        await db.execute(
            """INSERT INTO audit_events_new (
                   id, review_id, event_type, actor, old_status, new_status,
                   metadata, created_at
               )
               SELECT id, review_id, event_type, actor, old_status, new_status,
                      metadata, created_at
               FROM audit_events"""
        )
        await db.execute("DROP TABLE audit_events")
        await db.execute("ALTER TABLE audit_events_new RENAME TO audit_events")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_review ON audit_events(review_id)")
        await db.execute("CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type)")
        await db.execute("COMMIT")
    except Exception:
        await _rollback_quietly(db)
        raise


async def discover_repo_root() -> str | None:
    """Discover the git repository root directory."""
    try:
        proc = await asyncio.create_subprocess_exec(
            "git", "rev-parse", "--show-toplevel",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        if proc.returncode == 0:
            return stdout.decode("utf-8", errors="replace").strip()
    except Exception:
        pass
    return None


def _is_windows_proactor_reset_noise(context: dict[str, Any]) -> bool:
    """Return True for known benign WinError 10054 asyncio shutdown noise."""
    exception = context.get("exception")
    if not isinstance(exception, ConnectionResetError):
        return False

    # Windows-specific socket reset code for forcibly closed remote endpoint.
    if getattr(exception, "winerror", None) != 10054:
        return False

    message = context.get("message")
    if isinstance(message, str) and _PROACTOR_CONNECTION_LOST_CALLBACK in message:
        return True

    handle = context.get("handle")
    if handle is None:
        return False

    return _PROACTOR_CONNECTION_LOST_CALLBACK in repr(handle)


def install_windows_proactor_noise_filter(
    loop: asyncio.AbstractEventLoop,
) -> Callable[[], None]:
    """Suppress noisy Proactor callback reset traces on Windows.

    Keeps existing exception-handler behavior for all other asyncio errors.
    Returns a callable that restores the previous handler.
    """
    previous_handler = loop.get_exception_handler()

    if os.name != "nt":
        return lambda: None

    def handler(current_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
        if _is_windows_proactor_reset_noise(context):
            return

        if previous_handler is not None:
            previous_handler(current_loop, context)
        else:
            current_loop.default_exception_handler(context)

    loop.set_exception_handler(handler)

    def restore() -> None:
        if loop.get_exception_handler() is handler:
            loop.set_exception_handler(previous_handler)

    return restore


def _default_user_config_dir() -> Path:
    """Resolve a cross-platform user config directory for broker state."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / DB_CONFIG_DIRNAME

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata).expanduser() / DB_CONFIG_DIRNAME
        return Path.home() / "AppData" / "Roaming" / DB_CONFIG_DIRNAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / DB_CONFIG_DIRNAME

    return Path.home() / ".config" / DB_CONFIG_DIRNAME


def resolve_db_path(repo_root: str | None) -> Path:
    """Resolve the database path.

    Priority:
    1) Explicit BROKER_DB_PATH environment variable
    2) Standard user config directory (~/.config, APPDATA, or Application Support)
    """
    del repo_root  # repo root is used for diff validation, not database location.

    configured_path = os.environ.get(DB_PATH_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()

    return _default_user_config_dir() / DB_FILENAME


async def _rollback_quietly(db: aiosqlite.Connection) -> None:
    with suppress(Exception):
        await db.execute("ROLLBACK")


def _repo_config_path(repo_root: str | None) -> Path:
    configured_path = os.environ.get(CONFIG_PATH_ENV_VAR)
    if configured_path:
        return Path(configured_path).expanduser()

    base = Path(repo_root) if repo_root is not None else Path.cwd()
    return base / ".planning" / "config.json"


async def _check_idle_timeouts(ctx: AppContext) -> None:
    pool = ctx.pool
    if pool is None:
        return
    cutoff = f"-{int(pool.config.idle_timeout_seconds)} seconds"
    cursor = await ctx.db.execute(
        """SELECT id FROM reviewers
           WHERE status = 'active'
             AND last_active_at < datetime('now', ?)
             AND NOT EXISTS (
                 SELECT 1
                 FROM reviews
                 WHERE reviews.claimed_by = reviewers.id
                   AND reviews.status != 'closed'
             )""",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        await pool.drain_reviewer(row["id"], ctx.db, ctx.write_lock, reason="idle")


async def _check_ttl_expiry(ctx: AppContext) -> None:
    pool = ctx.pool
    if pool is None:
        return
    cutoff = f"-{int(pool.config.max_ttl_seconds)} seconds"
    cursor = await ctx.db.execute(
        """SELECT id FROM reviewers
           WHERE status = 'active'
             AND spawned_at < datetime('now', ?)
             AND NOT EXISTS (
                 SELECT 1
                 FROM reviews
                 WHERE reviews.claimed_by = reviewers.id
                   AND reviews.status != 'closed'
             )""",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    for row in rows:
        await pool.drain_reviewer(row["id"], ctx.db, ctx.write_lock, reason="ttl")


async def _check_claim_timeouts(ctx: AppContext) -> None:
    pool = ctx.pool
    if pool is None:
        return
    cutoff = f"-{int(pool.config.claim_timeout_seconds)} seconds"
    cursor = await ctx.db.execute(
        """SELECT id FROM reviews
           WHERE status = 'claimed'
             AND COALESCE(claimed_at, updated_at, created_at) < datetime('now', ?)""",
        (cutoff,),
    )
    rows = await cursor.fetchall()
    if not rows:
        return
    from gsd_review_broker.tools import reclaim_review  # local import avoids cycle

    for row in rows:
        await reclaim_review(row["id"], ctx, reason="claim_timeout")


async def _check_dead_processes(ctx: AppContext) -> None:
    pool = ctx.pool
    if pool is None:
        return
    from gsd_review_broker.tools import reclaim_review  # local import avoids cycle

    for reviewer_id, proc in list(pool._processes.items()):
        if proc.returncode is None:
            continue

        # If a reviewer process exits while it still owns open reviews, preserve
        # lifecycle semantics and recover claimed work immediately.
        cursor = await ctx.db.execute(
            """SELECT id, status
               FROM reviews
               WHERE claimed_by = ?
                 AND status != 'closed'""",
            (reviewer_id,),
        )
        attached_rows = await cursor.fetchall()

        detached_review_ids: list[str] = []
        detached_pending = False
        for row in attached_rows:
            if row["status"] == "claimed":
                await reclaim_review(row["id"], ctx, reason="reviewer_process_exit")
                continue
            detached_review_ids.append(row["id"])
            if row["status"] == "pending":
                detached_pending = True

        if detached_review_ids:
            async with ctx.write_lock:
                await ctx.db.execute("BEGIN IMMEDIATE")
                try:
                    for review_id in detached_review_ids:
                        await ctx.db.execute(
                            """UPDATE reviews
                               SET claimed_by = NULL,
                                   claimed_at = NULL,
                                   updated_at = datetime('now')
                               WHERE id = ? AND claimed_by = ?""",
                            (review_id, reviewer_id),
                        )
                        await record_event(
                            ctx.db,
                            review_id,
                            "review_detached",
                            actor="pool-manager",
                            metadata={
                                "reason": "reviewer_process_exit",
                                "reviewer_id": reviewer_id,
                            },
                        )
                    await ctx.db.execute("COMMIT")
                except Exception:
                    await _rollback_quietly(ctx.db)
                    raise
            for review_id in detached_review_ids:
                ctx.notifications.notify(review_id)
            if detached_pending:
                ctx.notifications.notify(QUEUE_TOPIC)

        cursor = await ctx.db.execute(
            """SELECT COUNT(*) AS n
               FROM reviews
               WHERE claimed_by = ?
                 AND status != 'closed'""",
            (reviewer_id,),
        )
        remaining_row = await cursor.fetchone()
        remaining_open = int(remaining_row["n"]) if remaining_row is not None else 0
        if remaining_open > 0:
            await pool.mark_dead_process_draining(
                reviewer_id,
                ctx.db,
                ctx.write_lock,
                exit_code=proc.returncode,
                open_reviews=remaining_open,
            )
            continue

        await pool._terminate_reviewer(reviewer_id, ctx.db, ctx.write_lock)


async def _check_reactive_scaling(ctx: AppContext) -> None:
    """Run one reactive scaling pass from background checks."""
    if ctx.pool is None:
        return
    from gsd_review_broker.tools import _reactive_scale_check  # local import avoids cycle

    await _reactive_scale_check(ctx, source="periodic")


async def _periodic_check(ctx: AppContext, name: str = "scaling") -> None:
    del name  # reserved for future multi-task variants
    while True:
        pool = ctx.pool
        if pool is None:
            await asyncio.sleep(1.0)
            continue
        await asyncio.sleep(pool.config.background_check_interval_seconds)
        for label, fn in (
            ("reactive_scale", _check_reactive_scaling),
            ("idle_timeout", _check_idle_timeouts),
            ("ttl_expiry", _check_ttl_expiry),
            ("claim_timeout", _check_claim_timeouts),
            ("dead_process", _check_dead_processes),
        ):
            try:
                await fn(ctx)
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("background check failed: %s", label)


async def _startup_terminate_stale_reviewers(ctx: AppContext) -> int:
    pool = ctx.pool
    if pool is None:
        return 0
    stale_ids: list[str] = []
    async with ctx.write_lock:
        try:
            await ctx.db.execute("BEGIN IMMEDIATE")
            cursor = await ctx.db.execute(
                """SELECT id FROM reviewers
                   WHERE status IN ('active', 'draining')
                     AND session_token != ?""",
                (pool.session_token,),
            )
            rows = await cursor.fetchall()
            stale_ids = [row["id"] for row in rows]
            if stale_ids:
                placeholders = ", ".join("?" for _ in stale_ids)
                await ctx.db.execute(
                    f"""UPDATE reviewers
                        SET status = 'terminated', terminated_at = datetime('now')
                        WHERE id IN ({placeholders})""",
                    stale_ids,
                )
            await ctx.db.execute("COMMIT")
        except Exception:
            await _rollback_quietly(ctx.db)
            raise
    return len(stale_ids)


async def _startup_ownership_sweep(ctx: AppContext) -> int:
    pool = ctx.pool
    if pool is None:
        return 0
    cursor = await ctx.db.execute(
        """SELECT id, claimed_by FROM reviews
           WHERE status = 'claimed'
             AND (
                 claimed_by IS NULL
                 OR claimed_by NOT IN (
                     SELECT id FROM reviewers
                     WHERE session_token = ? AND status IN ('active', 'draining')
                 )
             )""",
        (pool.session_token,),
    )
    rows = await cursor.fetchall()
    if not rows:
        return 0
    from gsd_review_broker.tools import reclaim_review  # local import avoids cycle

    reclaimed = 0
    for row in rows:
        result = await reclaim_review(row["id"], ctx, reason="stale_session")
        if "error" not in result:
            reclaimed += 1
    return reclaimed


async def _startup_reactive_scale_check(ctx: AppContext) -> None:
    """Run one immediate reactive scaling pass after startup recovery."""
    if ctx.pool is None:
        return
    from gsd_review_broker.tools import _reactive_scale_check  # local import avoids cycle

    await _reactive_scale_check(ctx, source="startup")


@asynccontextmanager
async def broker_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize SQLite with WAL mode at server startup, clean up on shutdown."""
    del server
    loop = asyncio.get_running_loop()
    restore_exception_handler = install_windows_proactor_noise_filter(loop)
    repo_root_override = os.environ.get(REPO_ROOT_ENV_VAR)
    if repo_root_override:
        repo_root = str(Path(repo_root_override).expanduser())
    else:
        repo_root = await discover_repo_root()
    db_path = resolve_db_path(repo_root)
    config_path = _repo_config_path(repo_root)
    if repo_root_override:
        logger.info("Using repo root override from %s: %s", REPO_ROOT_ENV_VAR, repo_root)
    config_path_override = os.environ.get(CONFIG_PATH_ENV_VAR)
    if config_path_override:
        logger.info("Using config path override from %s: %s", CONFIG_PATH_ENV_VAR, config_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    db = await aiosqlite.connect(
        str(db_path),
        isolation_level=None,  # CRITICAL: enables manual BEGIN IMMEDIATE
    )
    db.row_factory = aiosqlite.Row
    await db.execute("PRAGMA journal_mode=WAL")
    await db.execute("PRAGMA busy_timeout=5000")
    await db.execute("PRAGMA synchronous=NORMAL")
    await db.execute("PRAGMA foreign_keys=ON")
    await ensure_schema(db)

    pool: ReviewerPool | None = None
    try:
        spawn_config = load_spawn_config(config_path, repo_root=repo_root)
    except FileNotFoundError:
        logger.info("No config file, reviewer pool disabled (%s)", config_path)
    except Exception as exc:
        logger.warning("Failed to load reviewer_pool config; pool disabled: %s", exc)
    else:
        if spawn_config is None:
            logger.info("No reviewer_pool config, reviewer pool disabled")
        else:
            pool = ReviewerPool(
                session_token=secrets.token_hex(4),
                config=spawn_config,
            )

    ctx = AppContext(db=db, repo_root=repo_root, pool=pool)
    background_task: asyncio.Task | None = None
    if pool is not None:
        stale_terminated = await _startup_terminate_stale_reviewers(ctx)
        reclaimed = await _startup_ownership_sweep(ctx)
        await _startup_reactive_scale_check(ctx)
        logger.info(
            "Reviewer pool enabled: session=%s stale_terminated=%s reclaimed=%s",
            pool.session_token,
            stale_terminated,
            reclaimed,
        )
        background_task = asyncio.create_task(_periodic_check(ctx, "scaling"))

    logger.info("Broker ready - db=%s, repo=%s", db_path, repo_root or "cwd")
    try:
        yield ctx
    finally:
        try:
            if background_task is not None:
                background_task.cancel()
                with suppress(asyncio.CancelledError):
                    await background_task
            if ctx.pool is not None:
                await ctx.pool.shutdown_all(db, ctx.write_lock)
            await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            await db.close()
        finally:
            restore_exception_handler()
