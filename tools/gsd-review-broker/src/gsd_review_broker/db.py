"""Database connection, schema management, and lifespan for the GSD Review Broker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite
from fastmcp import FastMCP

from gsd_review_broker.notifications import NotificationBus

DB_PATH = Path(".planning") / "codex_review_broker.sqlite3"

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


@dataclass
class AppContext:
    """Application context holding the database connection."""

    db: aiosqlite.Connection
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    repo_root: str | None = None
    notifications: NotificationBus = field(default_factory=NotificationBus)


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


@asynccontextmanager
async def broker_lifespan(server: FastMCP) -> AsyncIterator[AppContext]:
    """Initialize SQLite with WAL mode at server startup, clean up on shutdown."""
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
    await ensure_schema(db)
    repo_root = await discover_repo_root()
    try:
        yield AppContext(db=db, repo_root=repo_root)
    finally:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await db.close()
