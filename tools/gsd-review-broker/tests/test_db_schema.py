"""Tests for database schema migration safety."""

from __future__ import annotations

import aiosqlite
import pytest

from gsd_review_broker import db as db_module
from gsd_review_broker.db import ensure_schema


class TestSchemaMigration:
    """Verify Phase 1 -> Phase 2 schema migration preserves data."""

    async def test_phase1_to_phase2_migration(self) -> None:
        """Phase 1 database gains new columns without data loss."""
        conn = await aiosqlite.connect(":memory:", isolation_level=None)
        conn.row_factory = aiosqlite.Row

        # Create Phase 1 schema (no description/diff/affected_files columns)
        await conn.executescript("""\
            CREATE TABLE IF NOT EXISTS reviews (
                id              TEXT PRIMARY KEY,
                status          TEXT NOT NULL DEFAULT 'pending',
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
        """)

        # Insert a Phase 1 row
        await conn.execute(
            "INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            ("test-id-1", "pending", "fix bug", "gsd-executor", "proposer", "1"),
        )

        # Run ensure_schema (should apply migrations)
        await ensure_schema(conn)

        # Check new columns exist
        cursor = await conn.execute("PRAGMA table_info(reviews)")
        columns = {row["name"] for row in await cursor.fetchall()}
        assert "description" in columns
        assert "diff" in columns
        assert "affected_files" in columns

        # Verify pre-existing row is intact
        cursor = await conn.execute("SELECT * FROM reviews WHERE id = ?", ("test-id-1",))
        row = await cursor.fetchone()
        assert row is not None
        assert row["id"] == "test-id-1"
        assert row["status"] == "pending"
        assert row["intent"] == "fix bug"
        assert row["agent_type"] == "gsd-executor"
        assert row["agent_role"] == "proposer"
        assert row["phase"] == "1"
        # New columns should be NULL for existing rows
        assert row["description"] is None
        assert row["diff"] is None
        assert row["affected_files"] is None

        await conn.close()

    async def test_non_duplicate_migration_errors_are_not_suppressed(
        self, monkeypatch
    ) -> None:
        """Migration should only ignore duplicate-column errors."""
        conn = await aiosqlite.connect(":memory:", isolation_level=None)
        conn.row_factory = aiosqlite.Row

        monkeypatch.setattr(
            db_module,
            "SCHEMA_MIGRATIONS",
            ["ALTER TABLE reviews THIS IS INVALID SQL"],
        )

        with pytest.raises(aiosqlite.OperationalError):
            await ensure_schema(conn)

        await conn.close()
