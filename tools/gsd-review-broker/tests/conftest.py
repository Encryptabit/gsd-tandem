"""Shared test fixtures for the GSD Review Broker."""

from __future__ import annotations

from collections.abc import AsyncIterator

import aiosqlite
import pytest

from gsd_review_broker.db import ensure_schema


@pytest.fixture
async def db() -> AsyncIterator[aiosqlite.Connection]:
    """In-memory SQLite database for tests."""
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await ensure_schema(conn)
    yield conn
    await conn.close()
