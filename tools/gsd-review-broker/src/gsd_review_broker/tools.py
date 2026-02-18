"""MCP tool definitions for the GSD Review Broker."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import time
import uuid
from contextlib import suppress

from fastmcp import Context

from gsd_review_broker.audit import record_event
from gsd_review_broker.db import AppContext
from gsd_review_broker.diff_utils import extract_affected_files, validate_diff
from gsd_review_broker.models import ReviewStatus
from gsd_review_broker.notifications import QUEUE_TOPIC
from gsd_review_broker.priority import infer_priority
from gsd_review_broker.server import mcp
from gsd_review_broker.state_machine import validate_transition

logger = logging.getLogger("gsd_review_broker")


def _app_ctx(ctx: Context) -> AppContext:
    """Resolve the broker AppContext from a FastMCP Context, across versions."""
    if ctx is None:
        raise RuntimeError("Missing MCP context")
    if hasattr(ctx, "lifespan_context"):
        return ctx.lifespan_context
    rc = getattr(ctx, "request_context", None)
    if rc is not None and hasattr(rc, "lifespan_context"):
        return rc.lifespan_context
    fm = getattr(ctx, "fastmcp", None)
    if fm is not None and hasattr(fm, "_lifespan_result"):
        return fm._lifespan_result
    raise RuntimeError("Unable to resolve broker lifespan context")


def _db_error(tool_name: str, exc: Exception) -> dict:
    logger.exception("%s -> database error: %s", tool_name, exc)
    return {"error": f"{tool_name} failed due to database error: {exc}"}


def _normalize_reason(reason: str | None) -> str | None:
    if reason is None:
        return None
    stripped = reason.strip()
    return stripped if stripped else None


async def _rollback_quietly(app: AppContext) -> None:
    with suppress(Exception):
        await app.db.execute("ROLLBACK")


def _short(review_id: str | None) -> str:
    """Render compact review IDs in logs."""
    if not review_id:
        return "unknown"
    return review_id[:8]


def _clip(value: str, max_len: int = 72) -> str:
    """Clamp long text for compact log lines."""
    if len(value) <= max_len:
        return value
    return value[: max_len - 3] + "..."


def _missing(value: str | None) -> bool:
    """Treat None/empty/whitespace-only as missing."""
    return value is None or value.strip() == ""


@mcp.tool
async def create_review(
    intent: str,
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
    project: str | None = None,
    category: str | None = None,
    description: str | None = None,
    diff: str | None = None,
    review_id: str | None = None,
    skip_diff_validation: bool = False,
    ctx: Context = None,
) -> dict:
    """Create a new review or revise an existing one.

    **New review** (omit review_id): Creates a fresh review with the given intent,
    description, and optional unified diff. Diffs are validated on submission
    unless skip_diff_validation is True.
    Returns review_id and initial status.

    **Revision** (pass existing review_id): Resubmits a review that is in
    changes_requested state. Replaces intent, description, diff, and affected_files.
    Clears claimed_by and verdict_reason. Returns to pending status.
    Revised diffs are also validated before persistence unless skip_diff_validation
    is True.

    Set skip_diff_validation=True for post-commit diffs that are already applied
    to the working tree. The diff will be stored as-is for reviewer inspection,
    and the persisted flag will cause claim_review to skip re-validation.

    `project` is optional and can be used to scope queue operations in shared
    global broker deployments.
    """
    app: AppContext = _app_ctx(ctx)

    # Compute affected_files from diff if provided
    affected_files: str | None = None
    if diff is not None:
        if not skip_diff_validation:
            is_valid, error_detail = await validate_diff(diff, cwd=app.repo_root)
            if not is_valid:
                logger.info(
                    "create_review -> %s validation_failed (%s)",
                    _short(review_id),
                    _clip(error_detail or "no details"),
                )
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
                    "SELECT status, project FROM reviews WHERE id = ?", (review_id,)
                )
                row = await cursor.fetchone()
                if row is None:
                    await app.db.execute("ROLLBACK")
                    return {"error": f"Review not found: {review_id}"}
                current_status = ReviewStatus(row["status"])
                resolved_project = project if project is not None else row["project"]
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
                           project = ?,
                           skip_diff_validation = ?,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (
                        ReviewStatus.PENDING,
                        intent,
                        description,
                        diff,
                        affected_files,
                        resolved_project,
                        1 if skip_diff_validation else 0,
                        review_id,
                    ),
                )
                await record_event(
                    app.db, review_id, "review_revised",
                    actor=agent_type,
                    old_status="changes_requested",
                    new_status="pending",
                    metadata={"revised": True, "project": resolved_project},
                )
                await app.db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(app)
                return _db_error("create_review", exc)
        app.notifications.notify(review_id)
        app.notifications.notify(QUEUE_TOPIC)
        logger.info(
            'create_review -> %s revised (phase=%s, project=%s, category=%s) "%s"',
            _short(review_id),
            phase,
            resolved_project,
            category or "uncategorized",
            _clip(intent),
        )
        return {
            "review_id": review_id,
            "status": ReviewStatus.PENDING,
            "revised": True,
            "project": resolved_project,
        }

    # --- New review flow ---
    new_review_id = str(uuid.uuid4())
    priority = infer_priority(agent_type, agent_role, phase, plan, task)
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            await app.db.execute(
                """INSERT INTO reviews (id, status, intent, description, diff,
                                        affected_files, agent_type, agent_role,
                                        phase, plan, task, project, priority, category,
                                        skip_diff_validation,
                                        created_at, updated_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                           datetime('now'), datetime('now'))""",
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
                    project,
                    str(priority),
                    category,
                    1 if skip_diff_validation else 0,
                ),
            )
            await record_event(
                app.db, new_review_id, "review_created",
                actor=agent_type,
                new_status="pending",
                metadata={"intent": intent, "project": project, "category": category},
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("create_review", exc)
    app.notifications.notify(new_review_id)
    app.notifications.notify(QUEUE_TOPIC)
    if getattr(app, "pool", None) is not None:
        asyncio.create_task(_reactive_scale_check(app, source="create_review"))
    logger.info(
        'create_review -> %s new (phase=%s, project=%s, category=%s) "%s"',
        _short(new_review_id),
        phase,
        project,
        category or "uncategorized",
        _clip(intent),
    )
    return {"review_id": new_review_id, "status": ReviewStatus.PENDING, "project": project}


@mcp.tool
async def list_reviews(
    status: str | None = None,
    category: str | None = None,
    project: str | None = None,
    projects: list[str] | None = None,
    wait: bool = False,
    ctx: Context = None,
) -> dict:
    """List reviews, optionally filtered by status and/or category.

    Results are sorted by priority (critical first, then normal, then low)
    and by creation time within each priority tier.

    Use status='pending' to find reviews awaiting a reviewer.
    Use category to filter by review type (e.g. 'plan_review', 'code_change').
    Use project to scope to a specific project in a shared broker database.
    Use projects to scope to multiple projects.
    If project/projects are omitted, reviews from all projects are returned.

    If wait=True, blocks up to 25 seconds until a pending review exists.
    wait=True requires status='pending' to avoid ambiguous semantics.
    """
    if wait and status != "pending":
        logger.info(
            "list_reviews -> invalid wait config (status=%s, category=%s)",
            status,
            category,
        )
        return {"error": "wait=True requires status='pending'"}
    if project is not None and projects is not None:
        return {"error": "Provide either 'project' or 'projects', not both."}
    if projects is not None and len(projects) == 0:
        return {"error": "'projects' must contain at least one project identifier."}

    app: AppContext = _app_ctx(ctx)
    project_filter_values = [project] if project is not None else projects

    async def _query() -> list[dict]:
        order_clause = (
            "ORDER BY CASE COALESCE(priority, 'normal') "
            "WHEN 'critical' THEN 0 WHEN 'normal' THEN 1 WHEN 'low' THEN 2 END, "
            "created_at ASC"
        )
        conditions: list[str] = []
        params: list[str] = []
        if status is not None:
            conditions.append("status = ?")
            params.append(status)
        if category is not None:
            conditions.append("category = ?")
            params.append(category)
        if project_filter_values is not None:
            if len(project_filter_values) == 1:
                conditions.append("project = ?")
                params.append(project_filter_values[0])
            else:
                placeholders = ", ".join("?" for _ in project_filter_values)
                conditions.append(f"project IN ({placeholders})")
                params.extend(project_filter_values)

        where_clause = ""
        if conditions:
            where_clause = "WHERE " + " AND ".join(conditions)

        cursor = await app.db.execute(
            "SELECT id, status, intent, agent_type, phase, priority, project, category, created_at "
            f"FROM reviews {where_clause} {order_clause}",
            params,
        )
        rows = await cursor.fetchall()
        return [
            {
                "id": row["id"],
                "status": row["status"],
                "intent": row["intent"],
                "agent_type": row["agent_type"],
                "phase": row["phase"],
                "priority": row["priority"],
                "project": row["project"],
                "category": row["category"],
                "created_at": row["created_at"],
            }
            for row in rows
        ]

    if not wait:
        reviews = await _query()
        logger.info(
            "list_reviews -> %s reviews (status=%s, category=%s, projects=%s, wait=false)",
            len(reviews),
            status,
            category,
            project_filter_values,
        )
        return {"reviews": reviews}

    # Long-poll loop with a hard deadline so request latency stays bounded.
    deadline = time.monotonic() + 25.0
    while True:
        # Capture version before reading to avoid missing a notify between steps.
        version = app.notifications.current_version(QUEUE_TOPIC)
        reviews = await _query()
        if reviews:
            logger.info(
                "list_reviews -> %s reviews (status=%s, category=%s, projects=%s, wait=true)",
                len(reviews),
                status,
                category,
                project_filter_values,
            )
            return {"reviews": reviews}

        remaining = deadline - time.monotonic()
        if remaining <= 0:
            logger.info(
                "list_reviews -> 0 reviews timeout "
                "(status=%s, category=%s, projects=%s, wait=true)",
                status,
                category,
                project_filter_values,
            )
            return {"reviews": []}

        await app.notifications.wait_for_change(
            QUEUE_TOPIC, timeout=remaining, since_version=version
        )


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
    app: AppContext = _app_ctx(ctx)
    auto_rejected_result: dict | None = None
    diff_text: str | None = None
    response_generation: int | None = None
    row = None
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT status, diff, intent, description, affected_files, project, category, "
                "skip_diff_validation, claim_generation "
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

            # Backward-compat: reviewer rows are optional (manual reviewers).
            if not _missing(reviewer_id):
                reviewer_cursor = await app.db.execute(
                    "SELECT status FROM reviewers WHERE id = ?",
                    (reviewer_id,),
                )
                reviewer_row = await reviewer_cursor.fetchone()
                if reviewer_row is not None and reviewer_row["status"] != "active":
                    await app.db.execute("ROLLBACK")
                    return {
                        "error": f"Reviewer {reviewer_id} is {reviewer_row['status']}, "
                        "cannot claim new reviews",
                    }

            diff_text = row["diff"]
            skip_validation = (
                bool(row["skip_diff_validation"])
                if row["skip_diff_validation"] is not None
                else False
            )
            if diff_text and not skip_validation:
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
                    await record_event(
                        app.db,
                        review_id,
                        "review_auto_rejected",
                        actor="broker-validator",
                        old_status="pending",
                        new_status="changes_requested",
                        metadata={"reason": error_detail},
                    )
                    await app.db.execute("COMMIT")
                    auto_rejected_result = {
                        "review_id": review_id,
                        "status": "changes_requested",
                        "auto_rejected": True,
                        "validation_error": error_detail,
                        "project": row["project"],
                        "category": row["category"],
                    }

            if auto_rejected_result is None:
                prior_generation = int(row["claim_generation"] or 0)
                response_generation = prior_generation + 1
                await app.db.execute(
                    """UPDATE reviews
                       SET status = ?, claimed_by = ?, claimed_at = datetime('now'),
                           claim_generation = claim_generation + 1,
                           updated_at = datetime('now')
                       WHERE id = ?""",
                    (ReviewStatus.CLAIMED, reviewer_id, review_id),
                )
                await record_event(
                    app.db,
                    review_id,
                    "review_claimed",
                    actor=reviewer_id,
                    old_status="pending",
                    new_status="claimed",
                    metadata={"claim_generation": response_generation},
                )
                await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("claim_review", exc)

    app.notifications.notify(review_id)
    if auto_rejected_result is not None:
        logger.info("claim_review -> %s auto_rejected by validator", _short(review_id))
        return auto_rejected_result

    result: dict = {
        "review_id": review_id,
        "status": ReviewStatus.CLAIMED,
        "claimed_by": reviewer_id,
        "claim_generation": response_generation,
        "intent": row["intent"],
        "project": row["project"],
        "category": row["category"],
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
    logger.info("claim_review -> %s claimed by %s", _short(review_id), reviewer_id)
    return result


@mcp.tool
async def submit_verdict(
    review_id: str,
    verdict: str,
    reason: str | None = None,
    counter_patch: str | None = None,
    claim_generation: int | None = None,
    reviewer_id: str | None = None,
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
        logger.info("submit_verdict -> %s rejected (missing reason)", _short(review_id))
        return {"error": "Notes (reason) required for 'changes_requested' verdict."}
    if verdict == "comment" and normalized_reason is None:
        logger.info("submit_verdict -> %s rejected (missing comment)", _short(review_id))
        return {"error": "Notes (reason) required for 'comment' verdict."}

    # --- Counter-patch validation (before any branch) ---
    app: AppContext = _app_ctx(ctx)
    counter_affected: str | None = None
    if counter_patch is not None:
        if verdict not in ("changes_requested", "comment"):
            logger.info(
                "submit_verdict -> %s rejected (counter_patch not allowed for %s)",
                _short(review_id),
                verdict,
            )
            return {
                "error": "Counter-patches only allowed with changes_requested or comment verdicts"
            }
        is_valid, error_detail = await validate_diff(counter_patch, cwd=app.repo_root)
        if not is_valid:
            logger.info(
                "submit_verdict -> %s counter_patch invalid (%s)",
                _short(review_id),
                _clip(error_detail or "no details"),
            )
            return {
                "error": "Counter-patch diff validation failed",
                "validation_error": error_detail,
            }
        counter_affected = extract_affected_files(counter_patch)

    def _guard_claimed_verdict(
        current_status: ReviewStatus,
        row_claim_generation: int,
        row_claimed_by: str | None,
        managed_claim: bool,
    ) -> dict | None:
        is_claimed_state = current_status == ReviewStatus.CLAIMED
        if managed_claim and claim_generation is None and _missing(reviewer_id):
            return {
                "error": (
                    "Claimed reviews require reviewer_id or claim_generation "
                    "for verdict submission"
                )
            }
        if claim_generation is not None and claim_generation != row_claim_generation:
            return {
                "error": (
                    "Stale claim: review was reclaimed since your claim. "
                    f"Your generation={claim_generation}, current={row_claim_generation}"
                )
            }
        if (
            managed_claim
            and row_claimed_by is not None
            and not _missing(reviewer_id)
            and reviewer_id != row_claimed_by
        ):
            return {
                "error": (
                    f"Unauthorized: review is claimed by {row_claimed_by}, "
                    f"not {reviewer_id}"
                )
            }
        return None

    # --- Comment verdict (no state transition) ---
    if verdict == "comment":
        async with app.write_lock:
            try:
                await app.db.execute("BEGIN IMMEDIATE")
                cursor = await app.db.execute(
                    "SELECT status, claim_generation, claimed_by FROM reviews WHERE id = ?",
                    (review_id,),
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
                managed_claim = False
                if current_status == ReviewStatus.CLAIMED and row["claimed_by"] is not None:
                    reviewer_cursor = await app.db.execute(
                        "SELECT 1 FROM reviewers WHERE id = ?",
                        (row["claimed_by"],),
                    )
                    managed_claim = await reviewer_cursor.fetchone() is not None

                guard_error = _guard_claimed_verdict(
                    current_status,
                    int(row["claim_generation"] or 0),
                    row["claimed_by"],
                    managed_claim,
                )
                if guard_error is not None:
                    await app.db.execute("ROLLBACK")
                    return guard_error

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
                await record_event(
                    app.db,
                    review_id,
                    "verdict_comment",
                    actor="reviewer",
                    old_status=str(current_status),
                    new_status=str(current_status),
                    metadata={
                        "reason": normalized_reason,
                        "has_counter_patch": counter_patch is not None,
                        "reviewer_id": reviewer_id,
                        "claim_generation": claim_generation,
                    },
                )
                await app.db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(app)
                return _db_error("submit_verdict", exc)
        app.notifications.notify(review_id)
        result = {
            "review_id": review_id,
            "status": str(current_status),
            "verdict": "comment",
            "verdict_reason": normalized_reason,
        }
        if counter_patch is not None:
            result["has_counter_patch"] = True
        logger.info(
            "submit_verdict -> %s COMMENT (counter_patch=%s)",
            _short(review_id),
            bool(counter_patch is not None),
        )
        return result

    # --- Standard verdicts (approved / changes_requested) ---
    valid_verdicts = {
        "approved": ReviewStatus.APPROVED,
        "changes_requested": ReviewStatus.CHANGES_REQUESTED,
    }
    if verdict not in valid_verdicts:
        logger.info("submit_verdict -> %s invalid verdict=%s", _short(review_id), verdict)
        return {
            "error": (
                f"Invalid verdict: {verdict!r}. "
                "Must be 'approved', 'changes_requested', or 'comment'."
            )
        }
    target_status = valid_verdicts[verdict]
    row_claimed_by: str | None = None
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT status, claim_generation, claimed_by FROM reviews WHERE id = ?",
                (review_id,),
            )
            row = await cursor.fetchone()
            if row is None:
                await app.db.execute("ROLLBACK")
                return {"error": f"Review not found: {review_id}"}
            current_status = ReviewStatus(row["status"])
            row_claimed_by = row["claimed_by"]
            managed_claim = False
            if current_status == ReviewStatus.CLAIMED and row_claimed_by is not None:
                reviewer_cursor = await app.db.execute(
                    "SELECT 1 FROM reviewers WHERE id = ?",
                    (row_claimed_by,),
                )
                managed_claim = await reviewer_cursor.fetchone() is not None
            guard_error = _guard_claimed_verdict(
                current_status,
                int(row["claim_generation"] or 0),
                row_claimed_by,
                managed_claim,
            )
            if guard_error is not None:
                await app.db.execute("ROLLBACK")
                return guard_error
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
            await record_event(
                app.db, review_id, "verdict_submitted",
                actor="reviewer",
                old_status=str(current_status),
                new_status=str(target_status),
                metadata={
                    "verdict": verdict,
                    "has_counter_patch": counter_patch is not None,
                    "reviewer_id": reviewer_id,
                    "claim_generation": claim_generation,
                },
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("submit_verdict", exc)
    await _maybe_finalize_draining_reviewer(app, row_claimed_by, trigger="terminal_verdict")
    app.notifications.notify(review_id)
    result = {
        "review_id": review_id,
        "status": str(target_status),
        "verdict_reason": normalized_reason,
    }
    if counter_patch is not None:
        result["has_counter_patch"] = True
    logger.info(
        "submit_verdict -> %s %s (counter_patch=%s)",
        _short(review_id),
        verdict.upper(),
        bool(counter_patch is not None),
    )
    return result


async def _maybe_finalize_draining_reviewer(
    app: AppContext,
    reviewer_id: str | None,
    trigger: str,
) -> None:
    """Terminate draining reviewer when all claimed reviews are resolved."""
    if _missing(reviewer_id):
        return
    pool = getattr(app, "pool", None)
    terminate_via_pool = False
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT status FROM reviewers WHERE id = ?",
                (reviewer_id,),
            )
            row = await cursor.fetchone()
            if row is None or row["status"] != "draining":
                await app.db.execute("ROLLBACK")
                return

            cursor = await app.db.execute(
                "SELECT COUNT(*) AS n FROM reviews WHERE status = 'claimed' AND claimed_by = ?",
                (reviewer_id,),
            )
            claims_row = await cursor.fetchone()
            remaining = int(claims_row["n"]) if claims_row is not None else 0
            if remaining > 0:
                await app.db.execute("ROLLBACK")
                return

            if pool is not None and reviewer_id in pool._processes:
                terminate_via_pool = True
            else:
                await app.db.execute(
                    """UPDATE reviewers
                       SET status = 'terminated', terminated_at = datetime('now')
                       WHERE id = ?""",
                    (reviewer_id,),
                )
                await record_event(
                    app.db,
                    None,
                    "reviewer_terminated",
                    actor="pool-manager",
                    old_status="draining",
                    new_status="terminated",
                    metadata={
                        "reviewer_id": reviewer_id,
                        "trigger": trigger,
                        "reason": "drain_complete",
                    },
                )
            await app.db.execute("COMMIT")
        except Exception:
            await _rollback_quietly(app)
            return

    if terminate_via_pool and pool is not None:
        try:
            await pool._terminate_reviewer(reviewer_id, app.db, app.write_lock)
        except Exception:
            logger.exception(
                "Failed to terminate draining reviewer subprocess: reviewer_id=%s",
                reviewer_id,
            )


async def reclaim_review(
    review_id: str,
    app: AppContext,
    reason: str = "claim_timeout",
) -> dict:
    """Reclaim a claimed review back to pending with claim-generation fencing."""
    old_claimed_by: str | None = None
    new_generation: int | None = None
    async with app.write_lock:
        try:
            await app.db.execute("BEGIN IMMEDIATE")
            cursor = await app.db.execute(
                "SELECT status, claimed_by, claim_generation FROM reviews WHERE id = ?",
                (review_id,),
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

            old_claimed_by = row["claimed_by"]
            new_generation = int(row["claim_generation"] or 0) + 1
            await app.db.execute(
                """UPDATE reviews
                   SET status = 'pending',
                       claimed_by = NULL,
                       claimed_at = NULL,
                       claim_generation = claim_generation + 1,
                       updated_at = datetime('now')
                   WHERE id = ?""",
                (review_id,),
            )
            await record_event(
                app.db,
                review_id,
                "review_reclaimed",
                actor="pool-manager",
                old_status="claimed",
                new_status="pending",
                metadata={
                    "old_reviewer": old_claimed_by,
                    "reason": reason,
                    "claim_generation": new_generation,
                },
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("reclaim_review", exc)

    await _maybe_finalize_draining_reviewer(app, old_claimed_by, trigger="reclaim")
    app.notifications.notify(review_id)
    app.notifications.notify(QUEUE_TOPIC)
    return {
        "review_id": review_id,
        "status": ReviewStatus.PENDING,
        "claim_generation": new_generation,
    }


async def _reactive_scale_check(app: AppContext, source: str = "unspecified") -> None:
    """Spawn reviewers reactively based on pending backlog ratio."""
    pool = getattr(app, "pool", None)
    if pool is None:
        logger.info("reactive_scale_check[%s] -> skipped (pool disabled)", source)
        return
    try:
        async with pool._spawn_lock:
            cursor = await app.db.execute(
                "SELECT COUNT(*) AS n FROM reviews WHERE status = 'pending'"
            )
            row = await cursor.fetchone()
            pending = int(row["n"]) if row is not None else 0
            active = pool.active_count
            ratio = pool.config.scaling_ratio
            target_active = min(pool.config.max_pool_size, math.ceil(pending / ratio)) if pending > 0 else 0
            spawn_needed = max(0, target_active - active)
            should_spawn = spawn_needed > 0
            reason = "target_gap" if should_spawn else "capacity_sufficient"
            logger.info(
                "reactive_scale_check[%s] -> pending=%s active=%s ratio=%.2f target=%s needed=%s decision=%s reason=%s",
                source,
                pending,
                active,
                ratio,
                target_active,
                spawn_needed,
                "spawn" if should_spawn else "skip",
                reason,
            )
            if should_spawn:
                spawned = 0
                for _ in range(spawn_needed):
                    result = await pool.spawn_reviewer(
                        app.db,
                        app.write_lock,
                        ignore_cooldown=True,
                    )
                    if "error" in result:
                        logger.warning(
                            "reactive_scale_check[%s] -> spawn failed after %s/%s: %s",
                            source,
                            spawned,
                            spawn_needed,
                            result["error"],
                        )
                        break
                    spawned += 1
                    logger.info(
                        "reactive_scale_check[%s] -> spawn succeeded reviewer_id=%s pid=%s progress=%s/%s",
                        source,
                        result.get("reviewer_id"),
                        result.get("pid"),
                        spawned,
                        spawn_needed,
                    )
                logger.info(
                    "reactive_scale_check[%s] -> spawn summary requested=%s spawned=%s active_now=%s",
                    source,
                    spawn_needed,
                    spawned,
                    pool.active_count,
                )
    except Exception:
        logger.exception("reactive scaling check failed")


@mcp.tool
async def spawn_reviewer(ctx: Context = None) -> dict:
    """Manually spawn a reviewer process when pool mode is configured."""
    app: AppContext = _app_ctx(ctx)
    pool = getattr(app, "pool", None)
    if pool is None:
        return {"error": "Reviewer pool not configured. Add reviewer_pool section to config."}
    async with pool._spawn_lock:
        result = await pool.spawn_reviewer(app.db, app.write_lock)
    logger.info("spawn_reviewer -> %s", result)
    return result


@mcp.tool
async def kill_reviewer(reviewer_id: str, ctx: Context = None) -> dict:
    """Drain and terminate a broker-managed reviewer by ID."""
    app: AppContext = _app_ctx(ctx)
    pool = getattr(app, "pool", None)
    if pool is None:
        return {"error": "Reviewer pool not configured. Add reviewer_pool section to config."}
    if reviewer_id not in pool._processes:
        return {"error": f"Unknown broker-managed reviewer_id: {reviewer_id}"}
    result = await pool.drain_reviewer(reviewer_id, app.db, app.write_lock, reason="manual")
    logger.info("kill_reviewer -> %s", result)
    return result


@mcp.tool
async def list_reviewers(ctx: Context = None) -> dict:
    """List active session reviewers in the pool."""
    app: AppContext = _app_ctx(ctx)
    pool = getattr(app, "pool", None)
    if pool is None:
        return {"error": "Reviewer pool not configured. Add reviewer_pool section to config."}

    cursor = await app.db.execute(
        """SELECT id, display_name, status, pid, spawned_at, last_active_at,
                  reviews_completed, total_review_seconds, approvals, rejections
           FROM reviewers
           WHERE session_token = ?
           ORDER BY spawned_at ASC""",
        (pool.session_token,),
    )
    rows = await cursor.fetchall()
    reviewers = [
        {
            "id": row["id"],
            "display_name": row["display_name"],
            "status": row["status"],
            "pid": row["pid"],
            "spawned_at": row["spawned_at"],
            "last_active_at": row["last_active_at"],
            "reviews_completed": row["reviews_completed"],
            "total_review_seconds": row["total_review_seconds"],
            "approvals": row["approvals"],
            "rejections": row["rejections"],
        }
        for row in rows
    ]
    return {
        "session_token": pool.session_token,
        "pool_size": pool.active_count,
        "reviewers": reviewers,
    }


@mcp.tool
async def close_review(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Close a review that has reached a terminal verdict (approved or changes_requested)."""
    app: AppContext = _app_ctx(ctx)
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
            await record_event(
                app.db, review_id, "review_closed",
                actor="system",
                old_status=str(current_status),
                new_status="closed",
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("close_review", exc)
    app.notifications.notify(review_id)
    app.notifications.cleanup(review_id)
    logger.info("close_review -> %s CLOSED", _short(review_id))
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
    app: AppContext = _app_ctx(ctx)
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
            await record_event(app.db, review_id, "counter_patch_accepted", actor="proposer")
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("accept_counter_patch", exc)

    app.notifications.notify(review_id)
    logger.info("accept_counter_patch -> %s accepted", _short(review_id))
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
    app: AppContext = _app_ctx(ctx)
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
            await record_event(app.db, review_id, "counter_patch_rejected", actor="proposer")
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("reject_counter_patch", exc)

    app.notifications.notify(review_id)
    logger.info("reject_counter_patch -> %s rejected", _short(review_id))
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
    app: AppContext = _app_ctx(ctx)

    if wait:
        await app.notifications.wait_for_change(review_id, timeout=25.0)

    cursor = await app.db.execute(
        """SELECT id, status, intent, agent_type, agent_role, phase, plan, task,
                  project, claimed_by, verdict_reason, priority, current_round, category,
                  updated_at
           FROM reviews WHERE id = ?""",
        (review_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        logger.info("get_review_status -> %s not found", _short(review_id))
        return {"error": f"Review {review_id} not found"}
    result = {
        "id": row["id"],
        "status": row["status"],
        "intent": row["intent"],
        "agent_type": row["agent_type"],
        "agent_role": row["agent_role"],
        "phase": row["phase"],
        "plan": row["plan"],
        "task": row["task"],
        "project": row["project"],
        "claimed_by": row["claimed_by"],
        "verdict_reason": row["verdict_reason"],
        "priority": row["priority"],
        "current_round": row["current_round"],
        "category": row["category"],
        "updated_at": row["updated_at"],
    }
    logger.info(
        "get_review_status -> %s %s (wait=%s)",
        _short(review_id),
        row["status"],
        wait,
    )
    return result


@mcp.tool
async def get_proposal(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Retrieve full proposal content including diff.

    Use after claim_review to read the complete unified diff for review.
    This is a read-only operation -- no state changes occur.
    """
    app: AppContext = _app_ctx(ctx)
    cursor = await app.db.execute(
        """SELECT id, status, intent, description, diff, affected_files, project, category,
                  counter_patch, counter_patch_affected_files, counter_patch_status
           FROM reviews WHERE id = ?""",
        (review_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        logger.info("get_proposal -> %s not found", _short(review_id))
        return {"error": f"Review {review_id} not found"}

    affected_files = None
    if row["affected_files"] is not None:
        try:
            affected_files = json.loads(row["affected_files"])
        except (json.JSONDecodeError, TypeError):
            affected_files = row["affected_files"]

    counter_patch_affected_files = None
    if row["counter_patch_affected_files"] is not None:
        try:
            counter_patch_affected_files = json.loads(row["counter_patch_affected_files"])
        except (json.JSONDecodeError, TypeError):
            counter_patch_affected_files = row["counter_patch_affected_files"]

    result = {
        "id": row["id"],
        "status": row["status"],
        "intent": row["intent"],
        "description": row["description"],
        "diff": row["diff"],
        "affected_files": affected_files,
        "project": row["project"],
        "category": row["category"],
        "counter_patch": row["counter_patch"],
        "counter_patch_affected_files": counter_patch_affected_files,
        "counter_patch_status": row["counter_patch_status"],
    }
    logger.info(
        "get_proposal -> %s loaded (has_diff=%s)",
        _short(review_id),
        bool(row["diff"]),
    )
    return result


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

    app: AppContext = _app_ctx(ctx)
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
                """INSERT INTO messages (
                       id, review_id, sender_role, round, body, metadata, created_at
                   ) VALUES (?, ?, ?, ?, ?, ?, datetime('now'))""",
                (msg_id, review_id, sender_role, current_round, body, metadata),
            )
            await record_event(
                app.db, review_id, "message_sent",
                actor=sender_role,
                metadata={"round": current_round, "body_preview": body[:100]},
            )
            await app.db.execute("COMMIT")
        except Exception as exc:
            await _rollback_quietly(app)
            return _db_error("add_message", exc)

    # Fire notification outside write_lock
    app.notifications.notify(review_id)

    logger.info(
        "add_message -> %s by %s (round=%s)",
        _short(review_id),
        sender_role,
        current_round,
    )
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
    app: AppContext = _app_ctx(ctx)

    # Verify review exists
    cursor = await app.db.execute(
        "SELECT id FROM reviews WHERE id = ?", (review_id,)
    )
    if await cursor.fetchone() is None:
        logger.info("get_discussion -> %s not found", _short(review_id))
        return {"error": f"Review not found: {review_id}"}

    if round is not None:
        cursor = await app.db.execute(
            """SELECT id, sender_role, round, body, metadata, created_at
               FROM messages WHERE review_id = ? AND round = ?
               ORDER BY rowid ASC""",
            (review_id, round),
        )
    else:
        cursor = await app.db.execute(
            """SELECT id, sender_role, round, body, metadata, created_at
               FROM messages WHERE review_id = ?
               ORDER BY rowid ASC""",
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

    logger.info(
        "get_discussion -> %s messages=%s (round=%s)",
        _short(review_id),
        len(messages),
        round if round is not None else "all",
    )
    return {"review_id": review_id, "messages": messages, "count": len(messages)}


@mcp.tool
async def get_activity_feed(
    status: str | None = None,
    category: str | None = None,
    project: str | None = None,
    ctx: Context = None,
) -> dict:
    """Get a live activity feed of all reviews with message previews.

    Returns all reviews sorted by most recently updated. Each entry includes
    a truncated preview of the most recent message, total message count,
    and the timestamp of the last message.

    Optionally filter by status, category, and/or project.
    """
    app: AppContext = _app_ctx(ctx)
    conditions: list[str] = []
    params: list[str] = []
    if status is not None:
        conditions.append("r.status = ?")
        params.append(status)
    if category is not None:
        conditions.append("r.category = ?")
        params.append(category)
    if project is not None:
        conditions.append("r.project = ?")
        params.append(project)

    where_clause = ""
    if conditions:
        where_clause = "WHERE " + " AND ".join(conditions)

    cursor = await app.db.execute(
        f"""SELECT
            r.id, r.status, r.intent, r.agent_type, r.phase, r.plan, r.task,
            r.priority, r.project, r.category, r.claimed_by, r.verdict_reason,
            strftime('%Y-%m-%dT%H:%M:%fZ', r.created_at) AS created_at,
            strftime('%Y-%m-%dT%H:%M:%fZ', r.updated_at) AS updated_at,
            (SELECT COUNT(*) FROM messages m WHERE m.review_id = r.id) AS message_count,
            (SELECT strftime('%Y-%m-%dT%H:%M:%fZ', MAX(m.created_at))
             FROM messages m WHERE m.review_id = r.id) AS last_message_at,
            (SELECT SUBSTR(m2.body, 1, 120)
             FROM messages m2 WHERE m2.review_id = r.id
             ORDER BY m2.rowid DESC LIMIT 1) AS last_message_preview
        FROM reviews r
        {where_clause}
        ORDER BY r.updated_at DESC, r.id DESC""",
        params,
    )
    rows = await cursor.fetchall()
    reviews = [
        {
            "id": row["id"],
            "status": row["status"],
            "intent": row["intent"],
            "agent_type": row["agent_type"],
            "phase": row["phase"],
            "plan": row["plan"],
            "task": row["task"],
            "priority": row["priority"],
            "project": row["project"],
            "category": row["category"],
            "claimed_by": row["claimed_by"],
            "verdict_reason": row["verdict_reason"],
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
            "message_count": row["message_count"],
            "last_message_at": row["last_message_at"],
            "last_message_preview": row["last_message_preview"],
        }
        for row in rows
    ]
    logger.info(
        "get_activity_feed -> %s reviews (status=%s, category=%s, project=%s)",
        len(reviews),
        status,
        category,
        project,
    )
    return {"reviews": reviews, "count": len(reviews)}


@mcp.tool
async def get_audit_log(
    review_id: str | None = None,
    ctx: Context = None,
) -> dict:
    """Get the append-only audit event history.

    If review_id is provided, returns events for that review only.
    If omitted, returns ALL events across all reviews.
    Events are ordered by insertion order (ascending).
    """
    app: AppContext = _app_ctx(ctx)

    if review_id is not None:
        # Verify review exists
        cursor = await app.db.execute(
            "SELECT id FROM reviews WHERE id = ?", (review_id,)
        )
        if await cursor.fetchone() is None:
            logger.info("get_audit_log -> %s not found", _short(review_id))
            return {"error": f"Review not found: {review_id}"}

        cursor = await app.db.execute(
            """SELECT id, review_id, event_type, actor, old_status, new_status,
                      metadata, strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AS created_at
               FROM audit_events
               WHERE review_id = ?
               ORDER BY id ASC""",
            (review_id,),
        )
    else:
        cursor = await app.db.execute(
            """SELECT id, review_id, event_type, actor, old_status, new_status,
                      metadata, strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AS created_at
               FROM audit_events
               ORDER BY id ASC"""
        )

    rows = await cursor.fetchall()
    events = []
    for row in rows:
        parsed_metadata = None
        if row["metadata"] is not None:
            try:
                parsed_metadata = json.loads(row["metadata"])
            except (json.JSONDecodeError, TypeError):
                parsed_metadata = row["metadata"]
        events.append({
            "id": row["id"],
            "review_id": row["review_id"],
            "event_type": row["event_type"],
            "actor": row["actor"],
            "old_status": row["old_status"],
            "new_status": row["new_status"],
            "metadata": parsed_metadata,
            "created_at": row["created_at"],
        })

    result: dict = {"events": events, "count": len(events)}
    if review_id is not None:
        result["review_id"] = review_id
    logger.info(
        "get_audit_log -> %s events (review=%s)",
        len(events),
        _short(review_id) if review_id is not None else "all",
    )
    return result


@mcp.tool
async def get_review_stats(project: str | None = None, ctx: Context = None) -> dict:
    """Get workflow health statistics for the broker.

    Returns total reviews, approval/rejection rates, reviews by category,
    average time-to-verdict, average review duration, and average time
    in each state. Use `project` to scope stats in a shared broker database.
    """
    app: AppContext = _app_ctx(ctx)
    review_where_clause = "WHERE project = ?" if project is not None else ""
    review_where_params: tuple[str, ...] = (project,) if project is not None else ()

    # Query 1: Status counts (COALESCE ensures 0 instead of NULL on empty table)
    cursor = await app.db.execute(
        f"""
        SELECT
            COUNT(*) AS total,
            COALESCE(SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END), 0) AS pending,
            COALESCE(SUM(CASE WHEN status = 'claimed' THEN 1 ELSE 0 END), 0) AS claimed,
            COALESCE(SUM(CASE WHEN status = 'approved' THEN 1 ELSE 0 END), 0) AS approved,
            COALESCE(
                SUM(CASE WHEN status = 'changes_requested' THEN 1 ELSE 0 END),
                0
            ) AS changes_requested,
            COALESCE(SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END), 0) AS closed
        FROM reviews
        {review_where_clause}
    """,
        review_where_params,
    )
    counts = dict(await cursor.fetchone())

    # Query 2: Category breakdown
    cursor = await app.db.execute(
        f"""
        SELECT COALESCE(category, 'uncategorized') AS cat, COUNT(*) AS cnt
        FROM reviews
        {review_where_clause}
        GROUP BY cat
    """,
        review_where_params,
    )
    by_category = {row["cat"]: row["cnt"] for row in await cursor.fetchall()}

    # Query 3: Approval rate
    approval_rate = None
    verdict_where_clause = (
        "WHERE ae.event_type = 'verdict_submitted' "
        "AND json_extract(ae.metadata, '$.verdict') = 'approved'"
    )
    verdict_where_params: list[str] = []
    if project is not None:
        verdict_where_clause += " AND r.project = ?"
        verdict_where_params.append(project)

    cursor = await app.db.execute(
        f"""
        SELECT COUNT(DISTINCT ae.review_id)
        FROM audit_events ae
        JOIN reviews r ON r.id = ae.review_id
        {verdict_where_clause}
    """,
        verdict_where_params,
    )
    approved_verdicts = (await cursor.fetchone())[0]
    total_verdict_where_clause = "WHERE ae.event_type = 'verdict_submitted'"
    total_verdict_where_params: list[str] = []
    if project is not None:
        total_verdict_where_clause += " AND r.project = ?"
        total_verdict_where_params.append(project)

    cursor = await app.db.execute(
        f"""
        SELECT COUNT(DISTINCT ae.review_id)
        FROM audit_events ae
        JOIN reviews r ON r.id = ae.review_id
        {total_verdict_where_clause}
    """,
        total_verdict_where_params,
    )
    total_verdicts = (await cursor.fetchone())[0]
    if total_verdicts > 0:
        approval_rate = round(100.0 * approved_verdicts / total_verdicts, 1)

    # Query 4: Average time-to-verdict (seconds)
    to_verdict_project_clause = "AND r.project = ?" if project is not None else ""
    to_verdict_params: tuple[str, ...] = (project,) if project is not None else ()
    cursor = await app.db.execute(
        f"""
        SELECT AVG(
            (julianday(ae.created_at) - julianday(r.created_at)) * 86400
        ) AS avg_seconds
        FROM reviews r
        JOIN audit_events ae ON ae.review_id = r.id
            AND ae.event_type = 'verdict_submitted'
        WHERE ae.id = (
            SELECT MIN(ae2.id) FROM audit_events ae2
            WHERE ae2.review_id = r.id AND ae2.event_type = 'verdict_submitted'
        )
        {to_verdict_project_clause}
    """,
        to_verdict_params,
    )
    avg_to_verdict = (await cursor.fetchone())[0]

    # Query 5: Average review duration (created to closed, seconds)
    review_duration_project_clause = "WHERE r.project = ?" if project is not None else ""
    review_duration_params: tuple[str, ...] = (project,) if project is not None else ()
    cursor = await app.db.execute(
        f"""
        SELECT AVG(
            (julianday(ae.created_at) - julianday(r.created_at)) * 86400
        ) AS avg_seconds
        FROM reviews r
        JOIN audit_events ae ON ae.review_id = r.id
            AND ae.event_type = 'review_closed'
        {review_duration_project_clause}
    """,
        review_duration_params,
    )
    avg_duration = (await cursor.fetchone())[0]

    # Query 6: Average time in each state (seconds)
    avg_state_where_clause = "WHERE ae.new_status IS NOT NULL"
    avg_state_where_params: tuple[str, ...] = ()
    if project is not None:
        avg_state_where_clause = "WHERE ae.new_status IS NOT NULL AND r.project = ?"
        avg_state_where_params = (project,)

    cursor = await app.db.execute(
        f"""
        SELECT
            new_status,
            AVG(duration_seconds) AS avg_seconds
        FROM (
            SELECT
                ae.new_status,
                (julianday(LEAD(ae.created_at) OVER (
                    PARTITION BY ae.review_id ORDER BY ae.id
                )) - julianday(ae.created_at)) * 86400 AS duration_seconds
            FROM audit_events ae
            JOIN reviews r ON r.id = ae.review_id
            {avg_state_where_clause}
        )
        WHERE duration_seconds IS NOT NULL
        GROUP BY new_status
    """,
        avg_state_where_params,
    )
    avg_time_in_state: dict = {}
    for row in await cursor.fetchall():
        avg_time_in_state[row["new_status"]] = round(row["avg_seconds"], 1)

    # Fill in default keys for expected states
    for state_key in ("pending", "claimed", "approved", "changes_requested"):
        if state_key not in avg_time_in_state:
            avg_time_in_state[state_key] = None

    result = {
        "total_reviews": counts["total"],
        "by_status": {
            "pending": counts["pending"],
            "claimed": counts["claimed"],
            "approved": counts["approved"],
            "changes_requested": counts["changes_requested"],
            "closed": counts["closed"],
        },
        "by_category": by_category,
        "approval_rate_pct": approval_rate,
        "avg_time_to_verdict_seconds": round(avg_to_verdict, 1) if avg_to_verdict else None,
        "avg_review_duration_seconds": round(avg_duration, 1) if avg_duration else None,
        "avg_time_in_state_seconds": avg_time_in_state,
    }
    logger.info(
        "get_review_stats -> total=%s approved=%s changes_requested=%s project=%s",
        result["total_reviews"],
        result["by_status"]["approved"],
        result["by_status"]["changes_requested"],
        project,
    )
    return result


@mcp.tool
async def get_review_timeline(
    review_id: str,
    ctx: Context = None,
) -> dict:
    """Get the complete chronological timeline for a single review.

    Returns all audit events in order: creation, claims, messages,
    verdicts, counter-patches, and closure. Each event includes
    its type, actor, status change, and timestamp.
    """
    app: AppContext = _app_ctx(ctx)

    # Verify review exists
    cursor = await app.db.execute(
        "SELECT id, intent, status, project, category FROM reviews WHERE id = ?",
        (review_id,),
    )
    row = await cursor.fetchone()
    if row is None:
        logger.info("get_review_timeline -> %s not found", _short(review_id))
        return {"error": f"Review not found: {review_id}"}

    cursor = await app.db.execute(
        """SELECT event_type, actor, old_status, new_status, metadata,
                  strftime('%Y-%m-%dT%H:%M:%fZ', created_at) AS timestamp
           FROM audit_events
           WHERE review_id = ?
           ORDER BY id ASC""",
        (review_id,),
    )
    events = []
    for event_row in await cursor.fetchall():
        event: dict = {
            "event_type": event_row["event_type"],
            "actor": event_row["actor"],
            "timestamp": event_row["timestamp"],
        }
        if event_row["old_status"] is not None:
            event["old_status"] = event_row["old_status"]
        if event_row["new_status"] is not None:
            event["new_status"] = event_row["new_status"]
        if event_row["metadata"] is not None:
            try:
                event["metadata"] = json.loads(event_row["metadata"])
            except (json.JSONDecodeError, TypeError):
                event["metadata"] = event_row["metadata"]
        events.append(event)

    result = {
        "review_id": review_id,
        "intent": row["intent"],
        "current_status": row["status"],
        "project": row["project"],
        "category": row["category"],
        "events": events,
        "event_count": len(events),
    }
    logger.info(
        "get_review_timeline -> %s events=%s",
        _short(review_id),
        len(events),
    )
    return result
