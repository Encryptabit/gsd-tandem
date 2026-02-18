"""Shared test fixtures for the GSD Review Broker."""

from __future__ import annotations

from collections.abc import AsyncIterator
from dataclasses import dataclass

import aiosqlite
import pytest

from gsd_review_broker.db import AppContext, ensure_schema
from gsd_review_broker.notifications import NotificationBus


@dataclass
class _MockFastMCP:
    """Stands in for the FastMCP instance so ctx.fastmcp._lifespan_result works."""

    _lifespan_result: AppContext


@dataclass
class MockContext:
    """Minimal mock for fastmcp.Context that provides fastmcp._lifespan_result."""

    fastmcp: _MockFastMCP

    @property
    def lifespan_context(self) -> AppContext:
        """Backwards-compat alias used by tests that access ctx.lifespan_context directly."""
        return self.fastmcp._lifespan_result


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
    app = AppContext(db=db, notifications=NotificationBus())
    return MockContext(fastmcp=_MockFastMCP(_lifespan_result=app))
