"""Tests for the audit event recording helper."""

from __future__ import annotations

import json
import re
import uuid

import aiosqlite

from gsd_review_broker.audit import record_event


async def _insert_review(db: aiosqlite.Connection, review_id: str | None = None) -> str:
    """Insert a minimal review to satisfy the foreign key constraint."""
    rid = review_id or str(uuid.uuid4())
    await db.execute(
        """INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase,
                                created_at, updated_at)
           VALUES (?, 'pending', 'test intent', 'executor', 'proposer', '1',
                   datetime('now'), datetime('now'))""",
        (rid,),
    )
    return rid


async def test_record_event_basic(db: aiosqlite.Connection) -> None:
    """record_event inserts a row with all fields populated."""
    rid = await _insert_review(db)
    meta = {"intent": "test", "category": "code_change"}

    await db.execute("BEGIN IMMEDIATE")
    await record_event(
        db,
        review_id=rid,
        event_type="review_created",
        actor="executor",
        old_status=None,
        new_status="pending",
        metadata=meta,
    )
    await db.execute("COMMIT")

    cursor = await db.execute(
        "SELECT * FROM audit_events WHERE review_id = ?", (rid,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["review_id"] == rid
    assert row["event_type"] == "review_created"
    assert row["actor"] == "executor"
    assert row["old_status"] is None
    assert row["new_status"] == "pending"
    # Verify metadata roundtrip
    parsed = json.loads(row["metadata"])
    assert parsed == meta


async def test_record_event_minimal(db: aiosqlite.Connection) -> None:
    """record_event works with only required fields (optional fields None)."""
    rid = await _insert_review(db)

    await db.execute("BEGIN IMMEDIATE")
    await record_event(db, review_id=rid, event_type="review_closed")
    await db.execute("COMMIT")

    cursor = await db.execute(
        "SELECT * FROM audit_events WHERE review_id = ?", (rid,)
    )
    row = await cursor.fetchone()
    assert row is not None
    assert row["actor"] is None
    assert row["old_status"] is None
    assert row["new_status"] is None
    assert row["metadata"] is None


async def test_record_event_timestamps_iso8601(db: aiosqlite.Connection) -> None:
    """created_at column value matches ISO 8601 pattern."""
    rid = await _insert_review(db)

    await db.execute("BEGIN IMMEDIATE")
    await record_event(db, review_id=rid, event_type="review_created")
    await db.execute("COMMIT")

    cursor = await db.execute(
        "SELECT created_at FROM audit_events WHERE review_id = ?", (rid,)
    )
    row = await cursor.fetchone()
    ts = row["created_at"]
    # ISO 8601: contains 'T' separator and ends with 'Z'
    assert "T" in ts
    assert ts.endswith("Z")
    # Full pattern: YYYY-MM-DDTHH:MM:SS.sssZ
    assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", ts)


async def test_record_event_multiple_events_same_review(db: aiosqlite.Connection) -> None:
    """Multiple events for the same review have sequential autoincrement IDs."""
    rid = await _insert_review(db)

    await db.execute("BEGIN IMMEDIATE")
    await record_event(db, review_id=rid, event_type="review_created", new_status="pending")
    await record_event(db, review_id=rid, event_type="review_claimed", old_status="pending", new_status="claimed")
    await record_event(db, review_id=rid, event_type="verdict_submitted", old_status="claimed", new_status="approved")
    await db.execute("COMMIT")

    cursor = await db.execute(
        "SELECT id FROM audit_events WHERE review_id = ? ORDER BY id ASC", (rid,)
    )
    rows = await cursor.fetchall()
    assert len(rows) == 3
    ids = [row["id"] for row in rows]
    assert ids[1] == ids[0] + 1
    assert ids[2] == ids[1] + 1
