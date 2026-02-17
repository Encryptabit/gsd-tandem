"""Audit event recording helper for the GSD Review Broker."""

from __future__ import annotations

import json

import aiosqlite


async def record_event(
    db: aiosqlite.Connection,
    review_id: str,
    event_type: str,
    actor: str | None = None,
    old_status: str | None = None,
    new_status: str | None = None,
    metadata: dict | None = None,
) -> None:
    """Record an audit event within the current transaction.

    Must be called INSIDE an existing BEGIN IMMEDIATE...COMMIT block.
    The caller is responsible for transaction management.
    """
    metadata_json = json.dumps(metadata) if metadata else None
    await db.execute(
        """INSERT INTO audit_events
           (review_id, event_type, actor, old_status, new_status, metadata, created_at)
           VALUES (?, ?, ?, ?, ?, ?, strftime('%Y-%m-%dT%H:%M:%fZ', 'now'))""",
        (review_id, event_type, actor, old_status, new_status, metadata_json),
    )
