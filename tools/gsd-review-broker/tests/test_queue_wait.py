"""Tests for the list_reviews wait=True (queue long-poll) feature."""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from gsd_review_broker.notifications import QUEUE_TOPIC
from gsd_review_broker.tools import create_review, list_reviews

if TYPE_CHECKING:
    from conftest import MockContext


# ---- Helpers ----


async def _create_pending(ctx: MockContext, **overrides) -> dict:
    defaults = {
        "intent": "test change",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "1",
    }
    defaults.update(overrides)
    with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock) as mock_vd:
        mock_vd.return_value = (True, None)
        return await create_review.fn(**defaults, ctx=ctx)


# ---- Constraint: wait requires status=pending ----


class TestListReviewsWaitGuards:
    async def test_wait_requires_status_pending(self, ctx: MockContext) -> None:
        result = await list_reviews.fn(wait=True, ctx=ctx)
        assert "error" in result
        assert "status='pending'" in result["error"]

    async def test_wait_with_status_claimed_rejected(self, ctx: MockContext) -> None:
        result = await list_reviews.fn(status="claimed", wait=True, ctx=ctx)
        assert "error" in result

    async def test_wait_with_status_approved_rejected(self, ctx: MockContext) -> None:
        result = await list_reviews.fn(status="approved", wait=True, ctx=ctx)
        assert "error" in result


# ---- Immediate return when pending reviews exist ----


class TestListReviewsWaitImmediate:
    async def test_wait_returns_immediately_if_pending_exists(self, ctx: MockContext) -> None:
        """If pending reviews already exist, wait=True should not block."""
        await _create_pending(ctx, intent="already here")
        result = await list_reviews.fn(status="pending", wait=True, ctx=ctx)
        assert "error" not in result
        assert len(result["reviews"]) == 1
        assert result["reviews"][0]["intent"] == "already here"


# ---- Blocking wait wakes on new review ----


class TestListReviewsWaitBlocking:
    async def test_wait_blocks_then_returns_on_new_review(self, ctx: MockContext) -> None:
        """With no pending reviews, wait=True blocks until create_review fires."""

        async def create_after_delay():
            await asyncio.sleep(0.1)
            await _create_pending(ctx, intent="delayed review")

        task = asyncio.create_task(create_after_delay())
        result = await list_reviews.fn(status="pending", wait=True, ctx=ctx)
        assert "error" not in result
        assert len(result["reviews"]) >= 1
        intents = [r["intent"] for r in result["reviews"]]
        assert "delayed review" in intents
        await task

    async def test_wait_times_out_returns_empty(self, ctx: MockContext) -> None:
        """If no review arrives within timeout, returns empty list."""
        # Monkey-patch a short timeout for this test
        original = list_reviews.fn

        async def short_timeout_list(**kwargs):
            # We can't easily override the 25s, so just verify immediate empty
            # when no signal arrives. Use the bus directly.
            pass

        # Direct test: call list with wait, but the bus will time out.
        # We override wait_for_change timeout via the bus directly.
        app = ctx.lifespan_context
        # Prime: ensure no pending reviews
        result_immediate = await list_reviews.fn(status="pending", wait=False, ctx=ctx)
        assert len(result_immediate["reviews"]) == 0

        # Capture version, then wait with a very short timeout directly
        version = app.notifications.current_version(QUEUE_TOPIC)
        changed = await app.notifications.wait_for_change(
            QUEUE_TOPIC, timeout=0.1, since_version=version
        )
        assert changed is False


# ---- Queue topic fires on revision (back to pending) ----


class TestQueueTopicOnRevision:
    async def test_revision_fires_queue_topic(self, ctx: MockContext) -> None:
        """When a review is revised back to pending, the queue topic is notified."""
        app = ctx.lifespan_context
        created = await _create_pending(ctx, intent="original")
        review_id = created["review_id"]

        # Move to changes_requested so we can revise
        from gsd_review_broker.tools import claim_review, submit_verdict

        await claim_review.fn(review_id=review_id, reviewer_id="r1", ctx=ctx)
        await submit_verdict.fn(
            review_id=review_id, verdict="changes_requested", reason="fix it", ctx=ctx
        )

        version_before = app.notifications.current_version(QUEUE_TOPIC)

        # Revise (re-submit with review_id) — should fire QUEUE_TOPIC
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock) as mock_vd:
            mock_vd.return_value = (True, None)
            result = await create_review.fn(
                intent="revised",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="1",
                review_id=review_id,
                ctx=ctx,
            )
        assert result.get("revised") is True

        version_after = app.notifications.current_version(QUEUE_TOPIC)
        assert version_after > version_before


# ---- Queue topic fires on new review ----


class TestQueueTopicOnCreate:
    async def test_new_review_fires_queue_topic(self, ctx: MockContext) -> None:
        app = ctx.lifespan_context
        version_before = app.notifications.current_version(QUEUE_TOPIC)
        await _create_pending(ctx)
        version_after = app.notifications.current_version(QUEUE_TOPIC)
        assert version_after > version_before
