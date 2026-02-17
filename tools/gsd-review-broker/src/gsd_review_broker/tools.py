"""MCP tool definitions for the GSD Review Broker."""

from __future__ import annotations

import json
import uuid
from contextlib import suppress

from fastmcp import Context

from gsd_review_broker.db import AppContext
from gsd_review_broker.diff_utils import extract_affected_files, validate_diff
from gsd_review_broker.models import ReviewStatus
from gsd_review_broker.server import mcp
from gsd_review_broker.state_machine import validate_transition


def _db_error(tool_name: str, exc: Exception) -> dict:
    return {"error": f"{tool_name} failed due to database error: {exc}"}


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    stripped = reason.strip()
    return stripped if stripped else None


async def _rollback_quietly(app: AppContext) -> None:
    with suppress(Exception):
        await app.db.execute("ROLLBACK")


@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    description: str | None = None,
    diff: str | None = None,
    review_id: str | None = None,
    ctx: Context = None,
) -> dict:
    """Create a new review or revise an existing one.

    **New review** (omit review_id): Creates a fresh review with the given intent,
    description, and optional unified diff. Diffs are validated on submission.
    Returns review_id and initial status.

    **Revision** (pass existing review_id): Resubmits a review that is in
    changes_requested state. Replaces intent, description, diff, and affected_files.
    Clears claimed_by and verdict_reason. Returns to pending status.
    Revised diffs are also validated before persistence.
    """
    app: AppContext = ctx.lifespan_context

    # Compute affected_files from diff if provided
    affected_files: str | None = None
    if diff is not None:
        is_valid, error_detail = await validate_diff(diff, cwd=app.repo_root)
        if not is_valid:
            return {
                "error": "Diff validation failed on submission. Diff does not apply cleanly.",
                "validation_error": error_detail,
            }
        affected_files = extract_affected_files(diff)

    # --- Revision flow ---
    if review_id is not None:
        async with app.write_lock:
            try:
                await app.db.execute("BEGIN IMMEDIATE")
                cursor = await app.db.execute(
                    "SELECT status FROM reviews WHERE id = ?", (review_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    await app.db.execute("ROLLBACK")
                    return {"error": f"Review not found: {review_id}"}
                current_status = ReviewStatus(row["status"])
                try:
                    validate_transition(current_status, ReviewStatus.PENDING)
                except ValueError as exc:
                    await app.db.execute("ROLLBACK")
                    return {"error": str(exc)}
                await app.db.execute(
                    """UPDATE reviews
                       SET status = ?, intent = ?, description = ?, diff = ?,
                           affected_files = ?, claimed_by = NULL, verdict_reason = NULL,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (
                        ReviewStatus.PENDING,
                        intent,
                        description,
                        diff,
                        affected_files,
                        review_id,
                    ),
                )
                await app.db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(app)
                return _db_error("create_review", exc)
        return {"review_id": review_id, "status": ReviewStatus.PENDING, "revised": True}

    # --- New review flow ---
    new_review_id = str(uuid.uuid4())
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            await app.db.execute(
                """INSERT INTO reviews (id, status, intent, description, diff,
                                        affected_files, agent_type, agent_role,
                                        phase, plan, task, created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
                (
                    new_review_id,
                    ReviewStatus.PENDING,
                    intent,
                    description,
                    diff,
                    affected_files,
                    agent_type,
                    agent_role,
                    phase,
                    plan,
                    task,
                ),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("create_review", exc)
    return {"review_id": new_review_id, "status": ReviewStatus.PENDING}


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
    """Claim a pending review for evaluation. Only pending reviews can be claimed.

    If the review contains a unified diff, it is validated against the working tree
    using git apply --check inside the write lock. If the diff does not apply cleanly,
    the review is auto-rejected with changes_requested status and validation error details.

    On successful claim, returns review metadata (intent, description, affected_files,
    has_diff flag) but NOT the full diff text. Use get_proposal to retrieve the diff.
    """
    app: AppContext = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT status, diff, intent, description, affected_files "
                "FROM reviews WHERE id = ?",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}
            current_status = ReviewStatus(row["status"])
            try:
                validate_transition(current_status, ReviewStatus.CLAIMED)
            except ValueError as exc:
                await app.db.execute("ROLLBACK")
                return {"error": str(exc)}

            # Validate diff inside write_lock (prevents wasted subprocess on concurrent claims)
            diff_text = row["diff"]
            if diff_text:
                is_valid, error_detail = await validate_diff(diff_text, cwd=app.repo_root)
                if not is_valid:
                    await app.db.execute(
                        """UPDATE reviews
                           SET status = ?, verdict_reason = ?, claimed_by = ?,
                               updated_at = datetime('now')
                           WHERE id = ?""",
                        (
                            ReviewStatus.CHANGES_REQUESTED,
                            f"Auto-rejected: diff does not apply cleanly.\n{error_detail}",
                            "broker-validator",
                            review_id,
                        ),
                    )
                    await app.db.execute("COMMIT")
                    return {
                        "review_id": review_id,
                        "status": "changes_requested",
                        "auto_rejected": True,
                        "validation_error": error_detail,
                    }

            await app.db.execute(
                """UPDATE reviews SET status = ?, claimed_by = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (ReviewStatus.CLAIMED, reviewer_id, review_id),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("claim_review", exc)

    # Build response with inline proposal metadata
    result: dict = {
        "review_id": review_id,
        "status": ReviewStatus.CLAIMED,
        "claimed_by": reviewer_id,
        "intent": row["intent"],
    }
    if row["description"] is not None:
        result["description"] = row["description"]
    if row["affected_files"] is not None:
        try:
            result["affected_files"] = json.loads(row["affected_files"])
        except (json.JSONDecodeError, TypeError):
            result["affected_files"] = row["affected_files"]
    if diff_text:
        result["has_diff"] = True
    return result


@mcp.tool
async def submit_verdict(
    review_id: str,
    verdict: str,
    reason: str | None = None,
    ctx: Context = None,
) -> dict:
    """Submit a verdict on a claimed review.

    Verdict must be 'approved', 'changes_requested', or 'comment'.
    - approved: Approves the review (notes optional).
    - changes_requested: Requests changes (notes required).
    - comment: Records feedback without changing review state (notes required).
    """
    normalized_reason = _normalize_reason(reason)

    # --- Notes enforcement ---
    if verdict == "changes_requested" and normalized_reason is None:
        return {"error": "Notes (reason) required for 'changes_requested' verdict."}
    if verdict == "comment" and normalized_reason is None:
        return {"error": "Notes (reason) required for 'comment' verdict."}

    # --- Comment verdict (no state transition) ---
    if verdict == "comment":
        app: AppContext = ctx.lifespan_context
        async with app.write_lock:
            try:
                await app.db.execute("BEGIN IMMEDIATE")
                cursor = await app.db.execute(
                    "SELECT status FROM reviews WHERE id = ?", (review_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    await app.db.execute("ROLLBACK")
                    return {"error": f"Review not found: {review_id}"}
                current_status = ReviewStatus(row["status"])
                if current_status not in (ReviewStatus.CLAIMED, ReviewStatus.IN_REVIEW):
                    await app.db.execute("ROLLBACK")
                    return {
                        "error": (
                            f"Cannot comment on review in '{current_status}' state. "
                            "Comments are only valid on claimed or in_review reviews."
                        )
                    }
                await app.db.execute(
                    """UPDATE reviews SET verdict_reason = ?, updated_at = datetime('now')
                       WHERE id = ?""",
                    (normalized_reason, review_id),
                )
                await app.db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(app)
                return _db_error("submit_verdict", exc)
        return {
            "review_id": review_id,
            "status": str(current_status),
            "verdict": "comment",
            "verdict_reason": normalized_reason,
        }

    # --- Standard verdicts (approved / changes_requested) ---
    valid_verdicts = {
        "approved": ReviewStatus.APPROVED,
        "changes_requested": ReviewStatus.CHANGES_REQUESTED,
    }
    if verdict not in valid_verdicts:
        return {
            "error": (
                f"Invalid verdict: {verdict!r}. "
                "Must be 'approved', 'changes_requested', or 'comment'."
            )
        }
    target_status = valid_verdicts[verdict]
    app = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute("SELECT status FROM reviews WHERE id = ?", (review_id,))
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}
            current_status = ReviewStatus(row["status"])
            try:
                validate_transition(current_status, target_status)
            except ValueError as exc:
                await app.db.execute("ROLLBACK")
                return {"error": str(exc)}
            await app.db.execute(
                """UPDATE reviews SET status = ?, verdict_reason = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (target_status, normalized_reason, review_id),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("submit_verdict", exc)
    return {
        "review_id": review_id,
        "status": str(target_status),
        "verdict_reason": normalized_reason,
    }


@mcp.tool
async def close_review(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Close a review that has reached a terminal verdict (approved or changes_requested)."""
    app: AppContext = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute("SELECT status FROM reviews WHERE id = ?", (review_id,))
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}
            current_status = ReviewStatus(row["status"])
            try:
                validate_transition(current_status, ReviewStatus.CLOSED)
            except ValueError as exc:
                await app.db.execute("ROLLBACK")
                return {"error": str(exc)}
            await app.db.execute(
                """UPDATE reviews SET status = ?, updated_at = datetime('now')
                   WHERE id = ?""",
                (ReviewStatus.CLOSED, review_id),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("close_review", exc)
    return {"review_id": review_id, "status": ReviewStatus.CLOSED}


@mcp.tool
async def get_review_status(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Check the current status of a review. Call repeatedly to poll for changes.

    Returns immediately -- does NOT block waiting for reviewer action.
    Recommended polling interval: 3 seconds.
    """
    app: AppContext = ctx.lifespan_context
    cursor = await app.db.execute(
        """SELECT id, status, intent, agent_type, agent_role, phase, plan, task,
                  claimed_by, verdict_reason, updated_at
           FROM reviews WHERE id = ?""",
        (review_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"error": f"Review {review_id} not found"}
    return {
        "id": row["id"],
        "status": row["status"],
        "intent": row["intent"],
        "agent_type": row["agent_type"],
        "agent_role": row["agent_role"],
        "phase": row["phase"],
        "plan": row["plan"],
        "task": row["task"],
        "claimed_by": row["claimed_by"],
        "verdict_reason": row["verdict_reason"],
        "updated_at": row["updated_at"],
    }


@mcp.tool
async def get_proposal(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Retrieve full proposal content including diff.

    Use after claim_review to read the complete unified diff for review.
    This is a read-only operation -- no state changes occur.
    """
    app: AppContext = ctx.lifespan_context
    cursor = await app.db.execute(
        """SELECT id, status, intent, description, diff, affected_files
           FROM reviews WHERE id = ?""",
        (review_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        return {"error": f"Review {review_id} not found"}

    affected_files = None
    if row["affected_files"] is not None:
        try:
            affected_files = json.loads(row["affected_files"])
        except (json.JSONDecodeError, TypeError):
            affected_files = row["affected_files"]

    return {
        "id": row["id"],
        "status": row["status"],
        "intent": row["intent"],
        "description": row["description"],
        "diff": row["diff"],
        "affected_files": affected_files,
    }
