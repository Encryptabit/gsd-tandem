"""MCP tool definitions for the GSD Review Broker."""

from __future__ import annotations

import uuid

from fastmcp import Context

from gsd_review_broker.db import AppContext
from gsd_review_broker.models import ReviewStatus
from gsd_review_broker.server import mcp
from gsd_review_broker.state_machine import validate_transition


@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    ctx: Context = None,
) -> dict:
    """Create a new review for a proposed change. Returns review_id and initial status."""
    app: AppContext = ctx.lifespan_context
    review_id = str(uuid.uuid4())
    try:
        await app.db.execute("BEGIN IMMEDIATE")
        await app.db.execute(
            """INSERT INTO reviews (id, status, intent, agent_type, agent_role,
                                    phase, plan, task, created_at, updated_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
            (review_id, ReviewStatus.PENDING, intent, agent_type, agent_role, phase, plan, task),
        )
        await app.db.execute("COMMIT")
    except Exception:
        await app.db.execute("ROLLBACK")
        raise
    return {"review_id": review_id, "status": ReviewStatus.PENDING}


@mcp.tool
async def list_reviews(
    status: str | None = None,
    ctx: Context = None,
) -> dict:
    """List reviews, optionally filtered by status.

    Use status='pending' to find reviews awaiting a reviewer.
    """
    app: AppContext = ctx.lifespan_context
    if status is not None:
        cursor = await app.db.execute(
            "SELECT id, status, intent, agent_type, phase, created_at "
            "FROM reviews WHERE status = ?",
            (status,),
        )
    else:
        cursor = await app.db.execute(
            "SELECT id, status, intent, agent_type, phase, created_at FROM reviews"
        )
    rows = await cursor.fetchall()
    reviews = [
        {
            "id": row["id"],
            "status": row["status"],
            "intent": row["intent"],
            "agent_type": row["agent_type"],
            "phase": row["phase"],
            "created_at": row["created_at"],
        }
        for row in rows
    ]
    return {"reviews": reviews}


@mcp.tool
async def claim_review(
    review_id: str,
    reviewer_id: str,
    ctx: Context = None,
) -> dict:
    """Claim a pending review for evaluation. Only pending reviews can be claimed."""
    app: AppContext = ctx.lifespan_context
    cursor = await app.db.execute("SELECT status FROM reviews WHERE id = ?", (review_id,))
    row = await cursor.fetchone()
    if row is None:
        return {"error": f"Review not found: {review_id}"}
    current_status = ReviewStatus(row["status"])
    try:
        validate_transition(current_status, ReviewStatus.CLAIMED)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        await app.db.execute("BEGIN IMMEDIATE")
        await app.db.execute(
            """UPDATE reviews SET status = ?, claimed_by = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (ReviewStatus.CLAIMED, reviewer_id, review_id),
        )
        await app.db.execute("COMMIT")
    except Exception:
        await app.db.execute("ROLLBACK")
        raise
    return {"review_id": review_id, "status": ReviewStatus.CLAIMED, "claimed_by": reviewer_id}


@mcp.tool
async def submit_verdict(
    review_id: str,
    verdict: str,
    reason: str | None = None,
    ctx: Context = None,
) -> dict:
    """Submit a verdict on a claimed review. Verdict must be 'approved' or 'changes_requested'."""
    valid_verdicts = {
        "approved": ReviewStatus.APPROVED,
        "changes_requested": ReviewStatus.CHANGES_REQUESTED,
    }
    if verdict not in valid_verdicts:
        return {
            "error": f"Invalid verdict: {verdict!r}. Must be 'approved' or 'changes_requested'."
        }
    target_status = valid_verdicts[verdict]
    app: AppContext = ctx.lifespan_context
    cursor = await app.db.execute("SELECT status FROM reviews WHERE id = ?", (review_id,))
    row = await cursor.fetchone()
    if row is None:
        return {"error": f"Review not found: {review_id}"}
    current_status = ReviewStatus(row["status"])
    try:
        validate_transition(current_status, target_status)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        await app.db.execute("BEGIN IMMEDIATE")
        await app.db.execute(
            """UPDATE reviews SET status = ?, verdict_reason = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (target_status, reason, review_id),
        )
        await app.db.execute("COMMIT")
    except Exception:
        await app.db.execute("ROLLBACK")
        raise
    return {"review_id": review_id, "status": str(target_status), "verdict_reason": reason}


@mcp.tool
async def close_review(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Close a review that has reached a terminal verdict (approved or changes_requested)."""
    app: AppContext = ctx.lifespan_context
    cursor = await app.db.execute("SELECT status FROM reviews WHERE id = ?", (review_id,))
    row = await cursor.fetchone()
    if row is None:
        return {"error": f"Review not found: {review_id}"}
    current_status = ReviewStatus(row["status"])
    try:
        validate_transition(current_status, ReviewStatus.CLOSED)
    except ValueError as exc:
        return {"error": str(exc)}
    try:
        await app.db.execute("BEGIN IMMEDIATE")
        await app.db.execute(
            """UPDATE reviews SET status = ?, updated_at = datetime('now')
               WHERE id = ?""",
            (ReviewStatus.CLOSED, review_id),
        )
        await app.db.execute("COMMIT")
    except Exception:
        await app.db.execute("ROLLBACK")
        raise
    return {"review_id": review_id, "status": ReviewStatus.CLOSED}
