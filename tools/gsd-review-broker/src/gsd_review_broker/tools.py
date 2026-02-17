"""MCP tool definitions for the GSD Review Broker."""

from __future__ import annotations

import json
import uuid
from contextlib import suppress

from fastmcp import Context

from gsd_review_broker.db import AppContext
from gsd_review_broker.diff_utils import extract_affected_files, validate_diff
from gsd_review_broker.models import ReviewStatus
from gsd_review_broker.priority import infer_priority
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
                           current_round = current_round + 1,
                           counter_patch = NULL,
                           counter_patch_affected_files = NULL,
                           counter_patch_status = NULL,
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
        app.notifications.notify(review_id)
        return {"review_id": review_id, "status": ReviewStatus.PENDING, "revised": True}

    # --- New review flow ---
    new_review_id = str(uuid.uuid4())
    priority = infer_priority(agent_type, agent_role, phase, plan, task)
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            await app.db.execute(
                """INSERT INTO reviews (id, status, intent, description, diff,
                                        affected_files, agent_type, agent_role,
                                        phase, plan, task, priority,
                                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'), datetime('now'))""",
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
                    str(priority),
                ),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("create_review", exc)
    app.notifications.notify(new_review_id)
    return {"review_id": new_review_id, "status": ReviewStatus.PENDING}


@mcp.tool
async def list_reviews(
    status: str | None = None,
    ctx: Context = None,
) -> dict:
    """List reviews, optionally filtered by status.

    Results are sorted by priority (critical first, then normal, then low)
    and by creation time within each priority tier.

    Use status='pending' to find reviews awaiting a reviewer.
    """
    app: AppContext = ctx.lifespan_context
    order_clause = (
        "ORDER BY CASE COALESCE(priority, 'normal') "
        "WHEN 'critical' THEN 0 WHEN 'normal' THEN 1 WHEN 'low' THEN 2 END, "
        "created_at ASC"
    )
    if status is not None:
        cursor = await app.db.execute(
            "SELECT id, status, intent, agent_type, phase, priority, created_at "
            f"FROM reviews WHERE status = ? {order_clause}",
            (status,),
        )
    else:
        cursor = await app.db.execute(
            "SELECT id, status, intent, agent_type, phase, priority, created_at "
            f"FROM reviews {order_clause}"
        )
    rows = await cursor.fetchall()
    reviews = [
        {
            "id": row["id"],
            "status": row["status"],
            "intent": row["intent"],
            "agent_type": row["agent_type"],
            "phase": row["phase"],
            "priority": row["priority"],
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
    counter_patch: str | None = None,
    ctx: Context = None,
) -> dict:
    """Submit a verdict on a claimed review.

    Verdict must be 'approved', 'changes_requested', or 'comment'.
    - approved: Approves the review (notes optional). Counter-patches not allowed.
    - changes_requested: Requests changes (notes required). May include counter_patch.
    - comment: Records feedback without changing review state (notes required).
      May include counter_patch.

    If counter_patch is provided, it is validated via git apply --check before storage.
    The counter-patch is stored with status 'pending' for the proposer to accept or reject.
    """
    normalized_reason = _normalize_reason(reason)

    # --- Notes enforcement ---
    if verdict == "changes_requested" and normalized_reason is None:
        return {"error": "Notes (reason) required for 'changes_requested' verdict."}
    if verdict == "comment" and normalized_reason is None:
        return {"error": "Notes (reason) required for 'comment' verdict."}

    # --- Counter-patch validation (before any branch) ---
    app: AppContext = ctx.lifespan_context
    counter_affected: str | None = None
    if counter_patch is not None:
        if verdict not in ("changes_requested", "comment"):
            return {
                "error": "Counter-patches only allowed with changes_requested or comment verdicts"
            }
        is_valid, error_detail = await validate_diff(counter_patch, cwd=app.repo_root)
        if not is_valid:
            return {
                "error": "Counter-patch diff validation failed",
                "validation_error": error_detail,
            }
        counter_affected = extract_affected_files(counter_patch)

    # --- Comment verdict (no state transition) ---
    if verdict == "comment":
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
                if counter_patch is not None:
                    await app.db.execute(
                        """UPDATE reviews SET verdict_reason = ?,
                               counter_patch = ?, counter_patch_affected_files = ?,
                               counter_patch_status = 'pending',
                               updated_at = datetime('now')
                           WHERE id = ?""",
                        (normalized_reason, counter_patch, counter_affected, review_id),
                    )
                else:
                    await app.db.execute(
                        """UPDATE reviews SET verdict_reason = ?, updated_at = datetime('now')
                           WHERE id = ?""",
                        (normalized_reason, review_id),
                    )
                await app.db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(app)
                return _db_error("submit_verdict", exc)
        if counter_patch is not None:
            app.notifications.notify(review_id)
        result = {
            "review_id": review_id,
            "status": str(current_status),
            "verdict": "comment",
            "verdict_reason": normalized_reason,
        }
        if counter_patch is not None:
            result["has_counter_patch"] = True
        return result

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
            if counter_patch is not None:
                await app.db.execute(
                    """UPDATE reviews SET status = ?, verdict_reason = ?,
                           counter_patch = ?, counter_patch_affected_files = ?,
                           counter_patch_status = 'pending',
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (target_status, normalized_reason, counter_patch, counter_affected, review_id),
                )
            else:
                await app.db.execute(
                    """UPDATE reviews SET status = ?, verdict_reason = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (target_status, normalized_reason, review_id),
                )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("submit_verdict", exc)
    if counter_patch is not None:
        app.notifications.notify(review_id)
    result = {
        "review_id": review_id,
        "status": str(target_status),
        "verdict_reason": normalized_reason,
    }
    if counter_patch is not None:
        result["has_counter_patch"] = True
    return result


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
async def accept_counter_patch(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Accept a pending counter-patch, replacing the review's active diff.

    Re-validates the counter-patch via git apply --check before replacing.
    If the counter-patch no longer applies cleanly, returns an error without
    modifying review state (the proposer can then reject it instead).
    """
    app: AppContext = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                """SELECT status, counter_patch, counter_patch_affected_files,
                          counter_patch_status
                   FROM reviews WHERE id = ?""",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}
            if row["counter_patch_status"] != "pending":
                await app.db.execute("ROLLBACK")
                return {"error": "No pending counter-patch to accept"}

            # Re-validate: diff may be stale
            is_valid, error_detail = await validate_diff(
                row["counter_patch"], cwd=app.repo_root
            )
            if not is_valid:
                await app.db.execute("ROLLBACK")
                return {
                    "error": "Counter-patch no longer applies cleanly",
                    "validation_error": error_detail,
                }

            await app.db.execute(
                """UPDATE reviews
                   SET diff = counter_patch,
                       affected_files = counter_patch_affected_files,
                       counter_patch = NULL,
                       counter_patch_affected_files = NULL,
                       counter_patch_status = 'accepted',
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (review_id,),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("accept_counter_patch", exc)

    app.notifications.notify(review_id)
    return {
        "review_id": review_id,
        "counter_patch_status": "accepted",
        "message": "Counter-patch accepted as active diff",
    }


@mcp.tool
async def reject_counter_patch(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Reject a pending counter-patch, clearing counter-patch columns."""
    app: AppContext = ctx.lifespan_context
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT counter_patch_status FROM reviews WHERE id = ?",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}
            if row["counter_patch_status"] != "pending":
                await app.db.execute("ROLLBACK")
                return {"error": "No pending counter-patch to reject"}
            await app.db.execute(
                """UPDATE reviews
                   SET counter_patch = NULL,
                       counter_patch_affected_files = NULL,
                       counter_patch_status = 'rejected',
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (review_id,),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("reject_counter_patch", exc)

    app.notifications.notify(review_id)
    return {"review_id": review_id, "counter_patch_status": "rejected"}


@mcp.tool
async def get_review_status(
    review_id: str,
    wait: bool = False,
    ctx: Context = None,
) -> dict:
    """Check the current status of a review. Call repeatedly to poll for changes.

    By default returns immediately. If wait=True, blocks up to 25 seconds waiting
    for a state change notification before returning current status. This reduces
    polling latency without requiring frequent requests.

    Recommended usage:
    - wait=False (default): Traditional polling, recommended interval 3 seconds.
    - wait=True: Long-poll mode, call again immediately after each response.
    """
    app: AppContext = ctx.lifespan_context

    if wait:
        await app.notifications.wait_for_change(review_id, timeout=25.0)

    cursor = await app.db.execute(
        """SELECT id, status, intent, agent_type, agent_role, phase, plan, task,
                  claimed_by, verdict_reason, priority, current_round, updated_at
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
        "priority": row["priority"],
        "current_round": row["current_round"],
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


@mcp.tool
async def add_message(
    review_id: str,
    sender_role: str,
    body: str,
    metadata: str | None = None,
    ctx: Context = None,
) -> dict:
    """Add a message to a review's discussion thread.

    Messages form a flat chronological conversation per review with strict turn
    alternation. Either role can send the first message, but subsequent messages
    must alternate between proposer and reviewer.

    Only reviews in 'claimed' or 'changes_requested' state accept messages.
    """
    if sender_role not in ("proposer", "reviewer"):
        return {"error": f"Invalid sender_role: {sender_role!r}. Must be 'proposer' or 'reviewer'."}

    app: AppContext = ctx.lifespan_context
    msg_id = str(uuid.uuid4())

    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")

            # Verify review exists and is in a valid state for messaging
            cursor = await app.db.execute(
                "SELECT status, current_round FROM reviews WHERE id = ?",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}

            current_status = ReviewStatus(row["status"])
            if current_status not in (ReviewStatus.CLAIMED, ReviewStatus.CHANGES_REQUESTED):
                await app.db.execute("ROLLBACK")
                return {
                    "error": (
                        f"Cannot add message to review in '{current_status}' state. "
                        "Messages are only valid on claimed or changes_requested reviews."
                    )
                }

            current_round = row["current_round"]

            # Turn enforcement: check last message sender
            cursor = await app.db.execute(
                "SELECT sender_role FROM messages WHERE review_id = ? "
                "ORDER BY rowid DESC LIMIT 1",
                (review_id,),
            )
            last_msg = await cursor.fetchone()
            if last_msg is not None and last_msg["sender_role"] == sender_role:
                await app.db.execute("ROLLBACK")
                return {
                    "error": (
                        f"Turn violation: '{sender_role}' sent the last message. "
                        "Messages must alternate between proposer and reviewer."
                    )
                }

            # Insert message
            await app.db.execute(
                """INSERT INTO messages (id, review_id, sender_role, round, body, metadata, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (msg_id, review_id, sender_role, current_round, body, metadata),
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("add_message", exc)

    # Fire notification outside write_lock
    app.notifications.notify(review_id)

    return {"message_id": msg_id, "review_id": review_id, "round": current_round}


@mcp.tool
async def get_discussion(
    review_id: str,
    round: int | None = None,
    ctx: Context = None,
) -> dict:
    """Retrieve the discussion thread for a review.

    Returns all messages in chronological order. Optionally filter by round number.
    This is a read-only operation -- no state changes occur.
    """
    app: AppContext = ctx.lifespan_context

    # Verify review exists
    cursor = await app.db.execute(
        "SELECT id FROM reviews WHERE id = ?", (review_id,)
    )
    if await cursor.fetchone() is None:
        return {"error": f"Review not found: {review_id}"}

    if round is not None:
        cursor = await app.db.execute(
            """SELECT id, sender_role, round, body, metadata, created_at
               FROM messages WHERE review_id = ? AND round = ?
               ORDER BY created_at ASC""",
            (review_id, round),
        )
    else:
        cursor = await app.db.execute(
            """SELECT id, sender_role, round, body, metadata, created_at
               FROM messages WHERE review_id = ?
               ORDER BY created_at ASC""",
            (review_id,),
        )

    rows = await cursor.fetchall()
    messages = []
    for msg_row in rows:
        parsed_metadata = None
        if msg_row["metadata"] is not None:
            try:
                parsed_metadata = json.loads(msg_row["metadata"])
            except (json.JSONDecodeError, TypeError):
                parsed_metadata = msg_row["metadata"]
        messages.append({
            "id": msg_row["id"],
            "sender_role": msg_row["sender_role"],
            "round": msg_row["round"],
            "body": msg_row["body"],
            "metadata": parsed_metadata,
            "created_at": msg_row["created_at"],
        })

    return {"review_id": review_id, "messages": messages, "count": len(messages)}
