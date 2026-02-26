"""Message threading, round tracking, priority inference, and notification tests.

Covers: add_message turn alternation, get_discussion retrieval with round filtering,
create_review round increment on revision, priority inference on creation, and
notification firing on message/review events.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from gsd_review_broker.tools import (
    add_message,
    claim_review,
    close_review,
    create_review,
    get_discussion,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext


# ---- Helpers ----


async def _create_and_claim(ctx: MockContext, **overrides) -> str:
    """Create a review and claim it. Returns the review_id."""
    defaults = {
        "intent": "test",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "01",
    }
    defaults.update(overrides)
    result = await create_review.fn(**defaults, ctx=ctx)
    review_id = result["review_id"]
    await claim_review.fn(review_id=review_id, reviewer_id="reviewer-1", ctx=ctx)
    return review_id


# ---- TestAddMessage ----


class TestAddMessage:
    async def test_alternating_messages(self, ctx: MockContext) -> None:
        """Reviewer sends message, proposer responds -- both succeed."""
        review_id = await _create_and_claim(ctx)
        r1 = await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="Please clarify", ctx=ctx
        )
        assert "message_id" in r1
        assert r1["review_id"] == review_id

        r2 = await add_message.fn(
            review_id=review_id, sender_role="proposer", body="Sure, here it is", ctx=ctx
        )
        assert "message_id" in r2
        assert r2["review_id"] == review_id

    async def test_consecutive_same_sender_rejected(self, ctx: MockContext) -> None:
        """Second consecutive message from same sender is rejected."""
        review_id = await _create_and_claim(ctx)
        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="First message", ctx=ctx
        )
        result = await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="Second message", ctx=ctx
        )
        assert "error" in result
        assert "Turn violation" in result["error"]

    async def test_either_role_can_start(self, ctx: MockContext) -> None:
        """Proposer can send the first message (no prior messages)."""
        review_id = await _create_and_claim(ctx)
        result = await add_message.fn(
            review_id=review_id, sender_role="proposer", body="I'll start", ctx=ctx
        )
        assert "message_id" in result

    async def test_message_includes_round(self, ctx: MockContext) -> None:
        """Message response includes round matching review's current_round."""
        review_id = await _create_and_claim(ctx)
        result = await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="Feedback", ctx=ctx
        )
        assert result["round"] == 1

    async def test_metadata_stored(self, ctx: MockContext) -> None:
        """Message with metadata stores and retrieves parsed metadata."""
        review_id = await _create_and_claim(ctx)
        metadata_str = '{"file": "foo.py", "line": 42}'
        await add_message.fn(
            review_id=review_id,
            sender_role="reviewer",
            body="Issue on this line",
            metadata=metadata_str,
            ctx=ctx,
        )
        discussion = await get_discussion.fn(review_id=review_id, ctx=ctx)
        assert discussion["count"] == 1
        msg = discussion["messages"][0]
        assert msg["metadata"] == {"file": "foo.py", "line": 42}

    async def test_message_on_pending_review_rejected(self, ctx: MockContext) -> None:
        """Adding message to unclaimed (pending) review returns error."""
        result = await create_review.fn(
            intent="test",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            ctx=ctx,
        )
        review_id = result["review_id"]
        msg_result = await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="Hello", ctx=ctx
        )
        assert "error" in msg_result
        assert "pending" in msg_result["error"]

    async def test_message_on_closed_review_rejected(self, ctx: MockContext) -> None:
        """Adding message to closed review returns error."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id, verdict="approved", ctx=ctx
        )
        await close_review.fn(review_id=review_id, closer_role="proposer", ctx=ctx)
        msg_result = await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="Late feedback", ctx=ctx
        )
        assert "error" in msg_result
        assert "closed" in msg_result["error"]

    async def test_invalid_sender_role_rejected(self, ctx: MockContext) -> None:
        """Invalid sender_role is rejected with clear error."""
        review_id = await _create_and_claim(ctx)
        result = await add_message.fn(
            review_id=review_id, sender_role="admin", body="Hi", ctx=ctx
        )
        assert "error" in result
        assert "Invalid sender_role" in result["error"]

    async def test_message_on_changes_requested_accepted(self, ctx: MockContext) -> None:
        """Messages are accepted in changes_requested state."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )
        result = await add_message.fn(
            review_id=review_id,
            sender_role="proposer",
            body="What should I fix?",
            ctx=ctx,
        )
        assert "message_id" in result

    async def test_proposer_message_requeues_changes_requested_to_pending(
        self, ctx: MockContext
    ) -> None:
        """Proposer follow-up re-queues review so reviewer loop can reclaim it."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )
        result = await add_message.fn(
            review_id=review_id,
            sender_role="proposer",
            body="Can you clarify the exact blocker?",
            ctx=ctx,
        )
        assert "message_id" in result

        cursor = await ctx.lifespan_context.db.execute(
            "SELECT status, claimed_by, claimed_at FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["claimed_by"] is None
        assert row["claimed_at"] is None

    async def test_requeue_reserves_active_reviewer_for_context_reuse(
        self, ctx: MockContext
    ) -> None:
        """Active reviewer ownership is preserved across proposer follow-up requeue."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )
        await ctx.lifespan_context.db.execute(
            """INSERT INTO reviewers (id, display_name, session_token, status)
               VALUES ('reviewer-1', 'reviewer-1', 'session-a', 'active')"""
        )
        await ctx.lifespan_context.db.execute(
            """INSERT INTO reviewers (id, display_name, session_token, status)
               VALUES ('reviewer-2', 'reviewer-2', 'session-a', 'active')"""
        )
        await add_message.fn(
            review_id=review_id,
            sender_role="proposer",
            body="Clarification request",
            ctx=ctx,
        )

        cursor = await ctx.lifespan_context.db.execute(
            "SELECT status, claimed_by FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "pending"
        assert row["claimed_by"] == "reviewer-1"

        wrong_reviewer = await claim_review.fn(
            review_id=review_id,
            reviewer_id="reviewer-2",
            ctx=ctx,
        )
        assert "error" in wrong_reviewer
        assert "reserved for reviewer reviewer-1" in wrong_reviewer["error"]

        right_reviewer = await claim_review.fn(
            review_id=review_id,
            reviewer_id="reviewer-1",
            ctx=ctx,
        )
        assert right_reviewer["status"] == "claimed"

    async def test_message_on_approved_accepted(self, ctx: MockContext) -> None:
        """Messages are accepted in approved state until proposer closes."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            reason="LGTM",
            ctx=ctx,
        )
        result = await add_message.fn(
            review_id=review_id,
            sender_role="proposer",
            body="Thanks for the review, closing next",
            ctx=ctx,
        )
        assert "message_id" in result


# ---- TestGetDiscussion ----


class TestGetDiscussion:
    async def test_empty_discussion(self, ctx: MockContext) -> None:
        """New review with no messages returns empty list, count=0."""
        review_id = await _create_and_claim(ctx)
        result = await get_discussion.fn(review_id=review_id, ctx=ctx)
        assert result["messages"] == []
        assert result["count"] == 0

    async def test_full_history(self, ctx: MockContext) -> None:
        """Three alternating messages returned in chronological order."""
        review_id = await _create_and_claim(ctx)
        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="msg 1", ctx=ctx
        )
        await add_message.fn(
            review_id=review_id, sender_role="proposer", body="msg 2", ctx=ctx
        )
        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="msg 3", ctx=ctx
        )
        result = await get_discussion.fn(review_id=review_id, ctx=ctx)
        assert result["count"] == 3
        bodies = [m["body"] for m in result["messages"]]
        assert bodies == ["msg 1", "msg 2", "msg 3"]

    async def test_filter_by_round(self, ctx: MockContext) -> None:
        """get_discussion filters by round number correctly."""
        # Round 1: create, claim, add messages
        review_id = await _create_and_claim(ctx)
        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="round 1 msg", ctx=ctx
        )

        # Push to changes_requested and revise -> round 2
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs revision",
            ctx=ctx,
        )
        await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            review_id=review_id,
            ctx=ctx,
        )
        # Re-claim for round 2
        await claim_review.fn(
            review_id=review_id, reviewer_id="reviewer-1", ctx=ctx
        )
        # Last message was reviewer in round 1, so round 2 first message must be proposer
        await add_message.fn(
            review_id=review_id, sender_role="proposer", body="round 2 msg", ctx=ctx
        )

        # Check round filter
        r1 = await get_discussion.fn(review_id=review_id, round=1, ctx=ctx)
        assert r1["count"] == 1
        assert r1["messages"][0]["body"] == "round 1 msg"
        assert r1["messages"][0]["round"] == 1

        r2 = await get_discussion.fn(review_id=review_id, round=2, ctx=ctx)
        assert r2["count"] == 1
        assert r2["messages"][0]["body"] == "round 2 msg"
        assert r2["messages"][0]["round"] == 2

    async def test_review_not_found(self, ctx: MockContext) -> None:
        """get_discussion for nonexistent review returns error."""
        result = await get_discussion.fn(review_id="nonexistent-id", ctx=ctx)
        assert "error" in result
        assert "not found" in result["error"]

    async def test_order_stable_when_timestamps_equal(self, ctx: MockContext) -> None:
        """Discussion order remains insertion-ordered even when created_at values match."""
        review_id = await _create_and_claim(ctx)
        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="first", ctx=ctx
        )
        await add_message.fn(
            review_id=review_id, sender_role="proposer", body="second", ctx=ctx
        )
        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="third", ctx=ctx
        )

        # Force same timestamp across all rows to exercise deterministic ordering.
        db = ctx.lifespan_context.db
        await db.execute(
            "UPDATE messages SET created_at = '2026-01-01 00:00:00' WHERE review_id = ?",
            (review_id,),
        )

        result = await get_discussion.fn(review_id=review_id, ctx=ctx)
        bodies = [m["body"] for m in result["messages"]]
        assert bodies == ["first", "second", "third"]


# ---- TestRoundTracking ----


class TestRoundTracking:
    async def test_revision_increments_round(self, ctx: MockContext) -> None:
        """Revising a review increments current_round from 1 to 2."""
        review_id = await _create_and_claim(ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Fix it",
            ctx=ctx,
        )
        await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            review_id=review_id,
            ctx=ctx,
        )

        # Verify round was incremented via DB
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT current_round FROM reviews WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        assert row["current_round"] == 2

    async def test_messages_after_revision_have_new_round(self, ctx: MockContext) -> None:
        """Messages added after revision use the incremented round number."""
        review_id = await _create_and_claim(ctx)
        # Round 1 message
        r1_msg = await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="first round", ctx=ctx
        )
        assert r1_msg["round"] == 1

        # Revise to round 2
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Fix",
            ctx=ctx,
        )
        await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            review_id=review_id,
            ctx=ctx,
        )
        await claim_review.fn(
            review_id=review_id, reviewer_id="reviewer-1", ctx=ctx
        )
        # Last message was reviewer in round 1, so round 2 must start with proposer
        r2_msg = await add_message.fn(
            review_id=review_id, sender_role="proposer", body="second round", ctx=ctx
        )
        assert r2_msg["round"] == 2

    async def test_revision_clears_counter_patch(self, ctx: MockContext) -> None:
        """Revision clears counter-patch columns and increments round."""
        review_id = await _create_and_claim(ctx)
        db = ctx.lifespan_context.db

        # Manually set counter-patch columns to simulate Plan 02 scenario
        await db.execute(
            """UPDATE reviews
               SET counter_patch = 'some-patch',
                   counter_patch_affected_files = '["foo.py"]',
                   counter_patch_status = 'pending'
               WHERE id = ?""",
            (review_id,),
        )

        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs revision",
            ctx=ctx,
        )
        await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            review_id=review_id,
            ctx=ctx,
        )

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


# ---- TestCreateReviewNotifications ----


class TestCreateReviewNotifications:
    async def test_add_message_fires_notification(self, ctx: MockContext) -> None:
        """After add_message, a concurrent wait_for_change returns True."""
        review_id = await _create_and_claim(ctx)
        bus = ctx.lifespan_context.notifications

        async def waiter():
            return await bus.wait_for_change(review_id, timeout=5.0)

        waiter_task = asyncio.create_task(waiter())
        # Small delay to ensure waiter is registered
        await asyncio.sleep(0.05)

        await add_message.fn(
            review_id=review_id, sender_role="reviewer", body="Hello", ctx=ctx
        )

        result = await waiter_task
        assert result is True

    async def test_revision_fires_notification(self, ctx: MockContext) -> None:
        """Revision fires notification on the review_id."""
        review_id = await _create_and_claim(ctx)
        bus = ctx.lifespan_context.notifications

        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )

        async def waiter():
            return await bus.wait_for_change(review_id, timeout=5.0)

        waiter_task = asyncio.create_task(waiter())
        await asyncio.sleep(0.05)

        await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            review_id=review_id,
            ctx=ctx,
        )

        result = await waiter_task
        assert result is True


# ---- TestCreateReviewPriority ----


class TestCreateReviewPriority:
    async def test_planner_gets_critical_priority(self, ctx: MockContext) -> None:
        """Create review with agent_type gsd-planner stores critical priority."""
        result = await create_review.fn(
            intent="plan change",
            agent_type="gsd-planner",
            agent_role="proposer",
            phase="01",
            ctx=ctx,
        )
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT priority FROM reviews WHERE id = ?", (result["review_id"],)
        )
        row = await cursor.fetchone()
        assert row["priority"] == "critical"

    async def test_executor_gets_normal_priority(self, ctx: MockContext) -> None:
        """Create review with agent_type gsd-executor stores normal priority."""
        result = await create_review.fn(
            intent="code change",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            ctx=ctx,
        )
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT priority FROM reviews WHERE id = ?", (result["review_id"],)
        )
        row = await cursor.fetchone()
        assert row["priority"] == "normal"

    async def test_verification_gets_low_priority(self, ctx: MockContext) -> None:
        """Create review with verify phase stores low priority."""
        result = await create_review.fn(
            intent="verify check",
            agent_type="gsd-verifier",
            agent_role="proposer",
            phase="05-verify",
            ctx=ctx,
        )
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT priority FROM reviews WHERE id = ?", (result["review_id"],)
        )
        row = await cursor.fetchone()
        assert row["priority"] == "low"
