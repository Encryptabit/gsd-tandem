"""Counter-patch lifecycle, priority sort, and notification polling tests.

Covers: counter-patch submission via submit_verdict, validation gating,
accept_counter_patch (with re-validation and stale detection), reject_counter_patch,
revision clearing counter-patch, priority-based list_reviews sorting,
and notification-enhanced get_review_status polling.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from gsd_review_broker.tools import (
    accept_counter_patch,
    claim_review,
    close_review,
    create_review,
    get_proposal,
    get_review_status,
    list_reviews,
    reject_counter_patch,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext

# --- Sample test data ---

SAMPLE_DIFF = """\
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 line one
-line two
+line TWO modified
 line three
"""

COUNTER_PATCH = """\
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 line one
-line two
+line TWO alternative fix
 line three
"""


# ---- Helpers ----


async def _create_review(ctx: MockContext, **overrides) -> dict:
    """Create a review with sensible defaults. Returns result dict."""
    defaults = {
        "intent": "test change",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "01",
    }
    defaults.update(overrides)
    return await create_review.fn(**defaults, ctx=ctx)


async def _create_and_claim(ctx: MockContext, **overrides) -> str:
    """Create a review and claim it. Returns the review_id."""
    result = await _create_review(ctx, **overrides)
    review_id = result["review_id"]
    await claim_review.fn(review_id=review_id, reviewer_id="reviewer-1", ctx=ctx)
    return review_id


async def _create_claim_and_changes_requested(
    ctx: MockContext,
    counter_patch: str | None = None,
    **overrides,
) -> str:
    """Create, claim, and submit changes_requested verdict. Returns review_id."""
    review_id = await _create_and_claim(ctx, **overrides)
    verdict_kwargs: dict = {
        "review_id": review_id,
        "verdict": "changes_requested",
        "reason": "Needs revision",
        "ctx": ctx,
    }
    if counter_patch is not None:
        verdict_kwargs["counter_patch"] = counter_patch
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await submit_verdict.fn(**verdict_kwargs)
    else:
        await submit_verdict.fn(**verdict_kwargs)
    return review_id


# ---- TestCounterPatchSubmission ----


class TestCounterPatchSubmission:
    async def test_counter_patch_on_changes_requested(self, ctx: MockContext) -> None:
        """Submit changes_requested with counter_patch stores patch and sets status pending."""
        review_id = await _create_and_claim(ctx)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await submit_verdict.fn(
                review_id=review_id,
                verdict="changes_requested",
                reason="use this instead",
                counter_patch=COUNTER_PATCH,
                ctx=ctx,
            )

        assert "error" not in result
        assert result["has_counter_patch"] is True

        # Verify DB state
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            """SELECT counter_patch, counter_patch_status, counter_patch_affected_files
               FROM reviews WHERE id = ?""",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["counter_patch"] == COUNTER_PATCH
        assert row["counter_patch_status"] == "pending"
        assert row["counter_patch_affected_files"] is not None

    async def test_counter_patch_on_comment(self, ctx: MockContext) -> None:
        """Comment verdict with counter_patch stores it successfully."""
        review_id = await _create_and_claim(ctx)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await submit_verdict.fn(
                review_id=review_id,
                verdict="comment",
                reason="try this alternative",
                counter_patch=COUNTER_PATCH,
                ctx=ctx,
            )

        assert "error" not in result
        assert result["has_counter_patch"] is True

        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT counter_patch_status FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["counter_patch_status"] == "pending"

    async def test_get_proposal_includes_pending_counter_patch_fields(
        self, ctx: MockContext
    ) -> None:
        """get_proposal exposes pending counter-patch fields for proposer decisions."""
        review_id = await _create_and_claim(ctx)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await submit_verdict.fn(
                review_id=review_id,
                verdict="changes_requested",
                reason="use this instead",
                counter_patch=COUNTER_PATCH,
                ctx=ctx,
            )

        proposal = await get_proposal.fn(review_id=review_id, ctx=ctx)
        assert "error" not in proposal
        assert proposal["counter_patch_status"] == "pending"
        assert proposal["counter_patch"] == COUNTER_PATCH
        assert proposal["counter_patch_affected_files"] is not None

    async def test_counter_patch_on_approve_rejected(self, ctx: MockContext) -> None:
        """Counter-patch with approved verdict is rejected."""
        review_id = await _create_and_claim(ctx)

        result = await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            counter_patch=COUNTER_PATCH,
            ctx=ctx,
        )

        assert "error" in result
        assert "changes_requested or comment" in result["error"]

    async def test_counter_patch_validation_failure(self, ctx: MockContext) -> None:
        """Counter-patch that fails validation is rejected."""
        review_id = await _create_and_claim(ctx)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(False, "patch does not apply"),
        ):
            result = await submit_verdict.fn(
                review_id=review_id,
                verdict="changes_requested",
                reason="try this",
                counter_patch=COUNTER_PATCH,
                ctx=ctx,
            )

        assert "error" in result
        assert "validation" in result["error"].lower() or "validation_error" in result

    async def test_verdict_without_counter_patch_unchanged(self, ctx: MockContext) -> None:
        """Standard changes_requested without counter_patch works as before."""
        review_id = await _create_and_claim(ctx)

        result = await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )

        assert "error" not in result
        assert "has_counter_patch" not in result

        db = ctx.lifespan_context.db
        cursor = await db.execute(
            """SELECT counter_patch, counter_patch_status, counter_patch_affected_files
               FROM reviews WHERE id = ?""",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["counter_patch"] is None
        assert row["counter_patch_status"] is None
        assert row["counter_patch_affected_files"] is None


# ---- TestAcceptCounterPatch ----


class TestAcceptCounterPatch:
    async def test_accept_replaces_active_diff(self, ctx: MockContext) -> None:
        """Accepting a counter-patch replaces the active diff and clears counter_patch column."""
        review_id = await _create_claim_and_changes_requested(
            ctx, counter_patch=COUNTER_PATCH
        )

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await accept_counter_patch.fn(review_id=review_id, ctx=ctx)

        assert "error" not in result
        assert result["counter_patch_status"] == "accepted"

        # Verify DB: diff replaced, counter_patch cleared
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            """SELECT diff, affected_files, counter_patch, counter_patch_status
               FROM reviews WHERE id = ?""",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["diff"] == COUNTER_PATCH
        assert row["affected_files"] is not None
        assert row["counter_patch"] is None
        assert row["counter_patch_status"] == "accepted"

    async def test_accept_with_stale_diff(self, ctx: MockContext) -> None:
        """Accepting stale counter-patch returns error; review state unchanged."""
        review_id = await _create_claim_and_changes_requested(
            ctx, counter_patch=COUNTER_PATCH
        )

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(False, "patch does not apply"),
        ):
            result = await accept_counter_patch.fn(review_id=review_id, ctx=ctx)

        assert "error" in result
        assert "no longer applies" in result["error"]

        # Verify state unchanged
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            """SELECT counter_patch, counter_patch_status
               FROM reviews WHERE id = ?""",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["counter_patch"] == COUNTER_PATCH
        assert row["counter_patch_status"] == "pending"

    async def test_accept_no_pending_counter_patch(self, ctx: MockContext) -> None:
        """Accept on review with no counter-patch returns error."""
        review_id = await _create_and_claim(ctx)
        # Submit verdict without counter_patch
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )

        result = await accept_counter_patch.fn(review_id=review_id, ctx=ctx)

        assert "error" in result
        assert "No pending counter-patch" in result["error"]

    async def test_accept_fires_notification(self, ctx: MockContext) -> None:
        """Accepting a counter-patch fires a notification."""
        review_id = await _create_claim_and_changes_requested(
            ctx, counter_patch=COUNTER_PATCH
        )
        bus = ctx.lifespan_context.notifications

        async def waiter():
            return await bus.wait_for_change(review_id, timeout=5.0)

        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await accept_counter_patch.fn(review_id=review_id, ctx=ctx)

        result = await waiter_task
        assert result is True


# ---- TestRejectCounterPatch ----


class TestRejectCounterPatch:
    async def test_reject_clears_counter_patch(self, ctx: MockContext) -> None:
        """Rejecting counter-patch sets status rejected, clears patch columns."""
        review_id = await _create_claim_and_changes_requested(
            ctx, counter_patch=COUNTER_PATCH
        )

        result = await reject_counter_patch.fn(review_id=review_id, ctx=ctx)

        assert "error" not in result
        assert result["counter_patch_status"] == "rejected"

        db = ctx.lifespan_context.db
        cursor = await db.execute(
            """SELECT counter_patch, counter_patch_affected_files, counter_patch_status
               FROM reviews WHERE id = ?""",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["counter_patch"] is None
        assert row["counter_patch_affected_files"] is None
        assert row["counter_patch_status"] == "rejected"

    async def test_reject_no_pending_counter_patch(self, ctx: MockContext) -> None:
        """Reject on review with no counter-patch returns error."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )

        result = await reject_counter_patch.fn(review_id=review_id, ctx=ctx)

        assert "error" in result
        assert "No pending counter-patch" in result["error"]


# ---- TestRevisionClearsCounterPatch ----


class TestRevisionClearsCounterPatch:
    async def test_revision_clears_pending_counter_patch(self, ctx: MockContext) -> None:
        """Revising a review clears all counter-patch columns and increments round."""
        review_id = await _create_claim_and_changes_requested(
            ctx, counter_patch=COUNTER_PATCH
        )

        # Revise
        await create_review.fn(
            intent="revised intent",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            review_id=review_id,
            ctx=ctx,
        )

        db = ctx.lifespan_context.db
        cursor = await db.execute(
            """SELECT current_round, counter_patch, counter_patch_affected_files,
                      counter_patch_status FROM reviews WHERE id = ?""",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["current_round"] == 2
        assert row["counter_patch"] is None
        assert row["counter_patch_affected_files"] is None
        assert row["counter_patch_status"] is None


# ---- TestPrioritySort ----


class TestPrioritySort:
    async def test_list_reviews_sorted_by_priority(self, ctx: MockContext) -> None:
        """list_reviews returns critical first, then normal, then low."""
        # Create reviews with different priorities
        await _create_review(
            ctx, intent="normal task", agent_type="gsd-executor", phase="01"
        )
        await _create_review(
            ctx, intent="critical task", agent_type="gsd-planner", phase="01"
        )
        await _create_review(
            ctx, intent="low task", agent_type="gsd-verifier", phase="05-verify"
        )

        result = await list_reviews.fn(status="pending", ctx=ctx)
        reviews = result["reviews"]
        assert len(reviews) == 3

        priorities = [r["priority"] for r in reviews]
        assert priorities == ["critical", "normal", "low"]

        # Verify intent ordering matches
        intents = [r["intent"] for r in reviews]
        assert intents == ["critical task", "normal task", "low task"]

    async def test_same_priority_sorted_by_created_at(self, ctx: MockContext) -> None:
        """Reviews with same priority appear in created_at order."""
        await _create_review(ctx, intent="first normal")
        await _create_review(ctx, intent="second normal")

        result = await list_reviews.fn(status="pending", ctx=ctx)
        reviews = result["reviews"]
        assert len(reviews) == 2
        assert reviews[0]["intent"] == "first normal"
        assert reviews[1]["intent"] == "second normal"

    async def test_priority_included_in_response(self, ctx: MockContext) -> None:
        """list_reviews response dicts include 'priority' key."""
        await _create_review(ctx)
        result = await list_reviews.fn(ctx=ctx)
        assert len(result["reviews"]) == 1
        assert "priority" in result["reviews"][0]


# ---- TestNotificationPolling ----


class TestNotificationPolling:
    async def test_get_review_status_wait_wakes_on_claim_transition(
        self, ctx: MockContext
    ) -> None:
        """wait=True wakes and returns claimed status when claim_review commits."""
        result = await _create_review(ctx)
        review_id = result["review_id"]

        async def poll_with_wait():
            return await get_review_status.fn(
                review_id=review_id, wait=True, ctx=ctx
            )

        poll_task = asyncio.create_task(poll_with_wait())
        await asyncio.sleep(0.05)

        await claim_review.fn(review_id=review_id, reviewer_id="reviewer-1", ctx=ctx)

        status_result = await asyncio.wait_for(poll_task, timeout=5.0)
        assert "error" not in status_result
        assert status_result["id"] == review_id
        assert status_result["status"] == "claimed"

    async def test_get_review_status_wait_wakes_on_verdict_without_counter_patch(
        self, ctx: MockContext
    ) -> None:
        """wait=True wakes on normal verdict updates (without counter-patch)."""
        review_id = await _create_and_claim(ctx)

        async def poll_with_wait():
            return await get_review_status.fn(
                review_id=review_id, wait=True, ctx=ctx
            )

        poll_task = asyncio.create_task(poll_with_wait())
        await asyncio.sleep(0.05)

        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs revision",
            ctx=ctx,
        )

        status_result = await asyncio.wait_for(poll_task, timeout=5.0)
        assert "error" not in status_result
        assert status_result["id"] == review_id
        assert status_result["status"] == "changes_requested"

    async def test_get_review_status_wait_wakes_on_close_transition(
        self, ctx: MockContext
    ) -> None:
        """wait=True wakes and returns closed status after close_review."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            ctx=ctx,
        )

        async def poll_with_wait():
            return await get_review_status.fn(
                review_id=review_id, wait=True, ctx=ctx
            )

        poll_task = asyncio.create_task(poll_with_wait())
        await asyncio.sleep(0.05)

        await close_review.fn(review_id=review_id, closer_role="proposer", ctx=ctx)

        status_result = await asyncio.wait_for(poll_task, timeout=5.0)
        assert "error" not in status_result
        assert status_result["id"] == review_id
        assert status_result["status"] == "closed"

    async def test_get_review_status_wait_returns_on_signal(
        self, ctx: MockContext
    ) -> None:
        """get_review_status with wait=True returns after notification signal."""
        result = await _create_review(ctx)
        review_id = result["review_id"]
        bus = ctx.lifespan_context.notifications

        async def poll_with_wait():
            return await get_review_status.fn(
                review_id=review_id, wait=True, ctx=ctx
            )

        poll_task = asyncio.create_task(poll_with_wait())
        await asyncio.sleep(0.05)

        # Fire notification
        bus.notify(review_id)

        status_result = await asyncio.wait_for(poll_task, timeout=5.0)
        assert "error" not in status_result
        assert status_result["id"] == review_id

    async def test_get_review_status_wait_timeout_path(self, ctx: MockContext) -> None:
        """get_review_status with wait=True returns on timeout (monkeypatched for speed)."""
        result = await _create_review(ctx)
        review_id = result["review_id"]

        # Monkeypatch wait_for_change to return False immediately (timeout simulation)
        original_wait = ctx.lifespan_context.notifications.wait_for_change
        wait_was_called = False

        async def fast_timeout(rid, timeout=25.0):
            nonlocal wait_was_called
            wait_was_called = True
            return False

        ctx.lifespan_context.notifications.wait_for_change = fast_timeout
        try:
            status_result = await get_review_status.fn(
                review_id=review_id, wait=True, ctx=ctx
            )
        finally:
            ctx.lifespan_context.notifications.wait_for_change = original_wait

        assert "error" not in status_result
        assert status_result["id"] == review_id
        assert wait_was_called is True

    async def test_get_review_status_default_no_wait(self, ctx: MockContext) -> None:
        """Default (wait=False) returns immediately with priority and current_round."""
        result = await _create_review(ctx)
        review_id = result["review_id"]

        status_result = await get_review_status.fn(review_id=review_id, ctx=ctx)

        assert "error" not in status_result
        assert status_result["id"] == review_id
        assert "priority" in status_result
        assert "current_round" in status_result
        assert status_result["current_round"] == 1
