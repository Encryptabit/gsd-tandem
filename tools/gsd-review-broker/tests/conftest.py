"""Shared test fixtures for the GSD Review Broker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiosqlite
import pytest

from gsd_review_broker.db import AppContext, ensure_schema


@dataclass
class MockContext:
    """Minimal mock for fastmcp.Context that provides lifespan_context."""

    lifespan_context: AppContext


@pytest.fixture
async def db() -> AsyncIterator[aiosqlite.Connection]:
    """In-memory SQLite database for tests."""
    conn = await aiosqlite.connect(":memory:", isolation_level=None)
    conn.row_factory = aiosqlite.Row
    await conn.execute("PRAGMA foreign_keys=ON")
    await ensure_schema(conn)
    yield conn
    await conn.close()


@pytest.fixture
def ctx(db: aiosqlite.Connection) -> MockContext:
    """Create a MockContext wrapping the in-memory db fixture."""
    return MockContext(lifespan_context=AppContext(db=db))
