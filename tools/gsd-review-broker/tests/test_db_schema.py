"""Tests for database schema migration safety."""

from __future__ import annotations

import asyncio
from pathlib import Path

import aiosqlite
import pytest

from gsd_review_broker import db as db_module
from gsd_review_broker.db import ensure_schema


async def test_reviewers_table_exists(db: aiosqlite.Connection) -> None:
    await db.execute(
        """INSERT INTO reviewers (
               id, display_name, session_token, status
           ) VALUES (?, ?, ?, ?)""",
        ("codex-r1-a7f3b2e1", "codex-r1", "a7f3b2e1", "active"),
    )
    cursor = await db.execute(
        "SELECT id, status FROM reviewers WHERE id = ?", ("codex-r1-a7f3b2e1",)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["id"] == "codex-r1-a7f3b2e1"
    assert row["status"] == "active"


async def test_claim_generation_column_exists(db: aiosqlite.Connection) -> None:
    await db.execute(
        """INSERT INTO reviews (
               id, status, intent, agent_type, agent_role, phase
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        ("review-claim-gen", "pending", "intent", "gsd-executor", "proposer", "1"),
    )
    cursor = await db.execute(
        "SELECT claim_generation FROM reviews WHERE id = ?",
        ("review-claim-gen",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["claim_generation"] == 0


async def test_claimed_at_column_exists(db: aiosqlite.Connection) -> None:
    await db.execute(
        """INSERT INTO reviews (
               id, status, intent, agent_type, agent_role, phase
           ) VALUES (?, ?, ?, ?, ?, ?)""",
        ("review-claimed-at", "pending", "intent", "gsd-executor", "proposer", "1"),
    )
    cursor = await db.execute(
        "SELECT claimed_at FROM reviews WHERE id = ?",
        ("review-claimed-at",),
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["claimed_at"] is None


async def test_audit_events_review_id_migrates_to_nullable() -> None:
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.executescript(
        """CREATE TABLE audit_events (
               id          INTEGER PRIMARY KEY AUTOINCREMENT,
               review_id   TEXT NOT NULL,
               event_type  TEXT NOT NULL,
               actor       TEXT,
               old_status  TEXT,
               new_status  TEXT,
               metadata    TEXT,
               created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))
           );"""
    )
    await conn.execute(
        """INSERT INTO audit_events (review_id, event_type, actor)
           VALUES ('legacy-review', 'review_created', 'proposer')"""
    )

    await ensure_schema(conn)

    cursor = await conn.execute("PRAGMA table_info(audit_events)")
    table_info = await cursor.fetchall()
    review_id_column = next(row for row in table_info if row["name"] == "review_id")
    assert review_id_column["notnull"] == 0

    await conn.execute(
        """INSERT INTO audit_events (review_id, event_type, actor)
           VALUES (NULL, 'reviewer_spawned', 'pool-manager')"""
    )
    cursor = await conn.execute(
        """SELECT review_id FROM audit_events
           WHERE event_type = 'reviewer_spawned'
           ORDER BY id DESC LIMIT 1"""
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["review_id"] is None
    await conn.close()


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


def _winerror_10054_connection_reset() -> ConnectionResetError:
    exc = ConnectionResetError(10054, "An existing connection was forcibly closed")
    exc.winerror = 10054  # type: ignore[attr-defined]
    return exc


def test_is_windows_proactor_reset_noise_detects_known_callback() -> None:
    context = {
        "message": (
            "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"
        ),
        "exception": _winerror_10054_connection_reset(),
    }
    assert db_module._is_windows_proactor_reset_noise(context) is True


def test_is_windows_proactor_reset_noise_ignores_nonmatching_context() -> None:
    nonmatching_message = {
        "message": "Exception in callback another_handler()",
        "exception": _winerror_10054_connection_reset(),
    }
    assert db_module._is_windows_proactor_reset_noise(nonmatching_message) is False

    wrong_error = {
        "message": (
            "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"
        ),
        "exception": RuntimeError("unexpected"),
    }
    assert db_module._is_windows_proactor_reset_noise(wrong_error) is False


async def test_install_windows_proactor_noise_filter_suppresses_only_known_noise(
    monkeypatch,
) -> None:
    monkeypatch.setattr(db_module.os, "name", "nt")
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()
    forwarded_contexts: list[dict[str, object]] = []

    def previous_handler(_loop: asyncio.AbstractEventLoop, context: dict[str, object]) -> None:
        forwarded_contexts.append(context)

    loop.set_exception_handler(previous_handler)
    restore = db_module.install_windows_proactor_noise_filter(loop)
    installed_handler = loop.get_exception_handler()
    assert installed_handler is not None
    assert installed_handler is not previous_handler

    try:
        suppressed_context = {
            "message": (
                "Exception in callback _ProactorBasePipeTransport._call_connection_lost()"
            ),
            "exception": _winerror_10054_connection_reset(),
        }
        installed_handler(loop, suppressed_context)
        assert forwarded_contexts == []

        forwarded_context = {
            "message": "Exception in callback something_else()",
            "exception": RuntimeError("boom"),
        }
        installed_handler(loop, forwarded_context)
        assert forwarded_contexts == [forwarded_context]
    finally:
        restore()
        loop.set_exception_handler(original)


async def test_install_windows_proactor_noise_filter_is_noop_on_non_windows(
    monkeypatch,
) -> None:
    monkeypatch.setattr(db_module.os, "name", "posix")
    loop = asyncio.get_running_loop()
    original = loop.get_exception_handler()

    def previous_handler(_loop: asyncio.AbstractEventLoop, _context: dict[str, object]) -> None:
        return

    loop.set_exception_handler(previous_handler)
    restore = db_module.install_windows_proactor_noise_filter(loop)
    try:
        assert loop.get_exception_handler() is previous_handler
        restore()
        assert loop.get_exception_handler() is previous_handler
    finally:
        loop.set_exception_handler(original)


def test_resolve_db_path_honors_env_override(monkeypatch) -> None:
    custom_path = "~/custom-broker/reviews.sqlite3"
    monkeypatch.setenv(db_module.DB_PATH_ENV_VAR, custom_path)
    path = db_module.resolve_db_path(repo_root="/ignored/repo")
    assert path == Path(custom_path).expanduser()


def test_resolve_db_path_uses_xdg_config_home(monkeypatch) -> None:
    monkeypatch.delenv(db_module.DB_PATH_ENV_VAR, raising=False)
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/xdg-config")
    monkeypatch.setattr(db_module.os, "name", "posix")
    monkeypatch.setattr(db_module.sys, "platform", "linux")

    path = db_module.resolve_db_path(repo_root=None)
    assert path == Path("/tmp/xdg-config/gsd-review-broker/codex_review_broker.sqlite3")


def test_resolve_db_path_uses_home_config_fallback(monkeypatch) -> None:
    monkeypatch.delenv(db_module.DB_PATH_ENV_VAR, raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    monkeypatch.setattr(db_module.os, "name", "posix")
    monkeypatch.setattr(db_module.sys, "platform", "linux")
    monkeypatch.setattr(db_module.Path, "home", staticmethod(lambda: Path("/home/tester")))

    path = db_module.resolve_db_path(repo_root=None)
    assert path == Path("/home/tester/.config/gsd-review-broker/codex_review_broker.sqlite3")


def test_repo_config_path_uses_repo_root_by_default(monkeypatch) -> None:
    monkeypatch.delenv(db_module.CONFIG_PATH_ENV_VAR, raising=False)
    path = db_module._repo_config_path("/tmp/repo")
    assert path == Path("/tmp/repo/.planning/config.json")


def test_repo_config_path_honors_env_override(monkeypatch) -> None:
    custom_config = "~/broker/custom-config.json"
    monkeypatch.setenv(db_module.CONFIG_PATH_ENV_VAR, custom_config)
    path = db_module._repo_config_path("/ignored/repo")
    assert path == Path(custom_config).expanduser()
