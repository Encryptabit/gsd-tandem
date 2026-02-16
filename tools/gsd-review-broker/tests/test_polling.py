"""Tests for the get_review_status polling tool and agent identity round-tripping."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gsd_review_broker.tools import (
    claim_review,
    create_review,
    get_review_status,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext


# ---- Helpers ----


async def _create_review(ctx: MockContext, **overrides) -> dict:
    """Shortcut to create a review with default values."""
    defaults = {
        "intent": "test change",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "1",
    }
    defaults.update(overrides)
    return await create_review.fn(**defaults, ctx=ctx)


# ---- get_review_status tests ----


class TestGetReviewStatus:
    async def test_get_review_status_returns_all_fields(self, ctx: MockContext) -> None:
        """INTER-01 round-trip: agent identity persists through create -> get_status."""
        created = await _create_review(
            ctx,
            intent="implement auth",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            plan="01-01",
            task="2",
        )
        result = await get_review_status.fn(review_id=created["review_id"], ctx=ctx)
        assert result["id"] == created["review_id"]
        assert result["status"] == "pending"
        assert result["intent"] == "implement auth"
        assert result["agent_type"] == "gsd-executor"
        assert result["agent_role"] == "proposer"
        assert result["phase"] == "1"
        assert result["plan"] == "01-01"
        assert result["task"] == "2"
        assert result["claimed_by"] is None
        assert result["verdict_reason"] is None
        assert result["updated_at"] is not None

    async def test_get_review_status_not_found(self, ctx: MockContext) -> None:
        result = await get_review_status.fn(review_id="nonexistent-id", ctx=ctx)
        assert "error" in result
        assert "nonexistent-id" in result["error"]

    async def test_get_review_status_reflects_transitions(self, ctx: MockContext) -> None:
        """Polling shows status changes: pending -> claimed -> approved."""
        created = await _create_review(ctx)
        review_id = created["review_id"]

        # Pending
        status1 = await get_review_status.fn(review_id=review_id, ctx=ctx)
        assert status1["status"] == "pending"

        # Claimed
        await claim_review.fn(review_id=review_id, reviewer_id="reviewer-1", ctx=ctx)
        status2 = await get_review_status.fn(review_id=review_id, ctx=ctx)
        assert status2["status"] == "claimed"
        assert status2["claimed_by"] == "reviewer-1"

        # Approved
        await submit_verdict.fn(
            review_id=review_id, verdict="approved", reason="LGTM", ctx=ctx
        )
        status3 = await get_review_status.fn(review_id=review_id, ctx=ctx)
        assert status3["status"] == "approved"

    async def test_get_review_status_includes_verdict_reason(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        review_id = created["review_id"]
        await claim_review.fn(review_id=review_id, reviewer_id="reviewer-1", ctx=ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            reason="LGTM, clean implementation",
            ctx=ctx,
        )
        result = await get_review_status.fn(review_id=review_id, ctx=ctx)
        assert result["verdict_reason"] == "LGTM, clean implementation"

    async def test_agent_identity_persisted_with_nulls(self, ctx: MockContext) -> None:
        """Agent identity with None plan/task round-trips correctly."""
        created = await _create_review(
            ctx,
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            plan=None,
            task=None,
        )
        result = await get_review_status.fn(review_id=created["review_id"], ctx=ctx)
        assert result["plan"] is None
        assert result["task"] is None
        assert result["agent_type"] == "gsd-executor"
        assert result["agent_role"] == "proposer"
        assert result["phase"] == "1"
