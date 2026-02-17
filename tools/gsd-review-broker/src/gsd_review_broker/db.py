"""Database connection, schema management, and lifespan for the GSD Review Broker."""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite
from fastmcp import FastMCP

DB_PATH = Path(".planning") / "codex_review_broker.sqlite3"

SCHEMA_SQL = """\
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
"""


@dataclass
class AppContext:
    """Application context holding the database connection."""

    db: aiosqlite.Connection
    write_lock: asyncio.Lock = field(default_factory=asyncio.Lock)


async def ensure_schema(db: aiosqlite.Connection) -> None:
    """Create tables and indexes if they don't exist."""
    await db.executescript(SCHEMA_SQL)


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
    try:
        yield AppContext(db=db)
    finally:
        await db.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        await db.close()
