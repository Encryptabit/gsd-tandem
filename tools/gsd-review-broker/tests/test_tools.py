"""Integration tests for MCP tool handlers using in-memory SQLite."""

from __future__ import annotations

from typing import TYPE_CHECKING

from gsd_review_broker.tools import (
    claim_review,
    close_review,
    create_review,
    list_reviews,
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


# ---- create_review tests ----


class TestCreateReview:
    async def test_create_review_returns_id_and_status(self, ctx: MockContext) -> None:
        result = await _create_review(ctx)
        assert "review_id" in result
        assert result["status"] == "pending"

    async def test_create_review_persists_to_db(self, ctx: MockContext) -> None:
        result = await _create_review(ctx)
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT id, status, intent FROM reviews WHERE id = ?",
            (result["review_id"],),
        )
        row = await cursor.fetchone()
        assert row is not None
        assert row["status"] == "pending"
        assert row["intent"] == "test change"

    async def test_create_review_generates_unique_ids(self, ctx: MockContext) -> None:
        r1 = await _create_review(ctx)
        r2 = await _create_review(ctx)
        assert r1["review_id"] != r2["review_id"]

    async def test_create_review_stores_agent_identity(self, ctx: MockContext) -> None:
        result = await _create_review(
            ctx,
            agent_type="gsd-planner",
            agent_role="reviewer",
            phase="2",
            plan="02-01",
            task="3",
        )
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT agent_type, agent_role, phase, plan, task FROM reviews WHERE id = ?",
            (result["review_id"],),
        )
        row = await cursor.fetchone()
        assert row["agent_type"] == "gsd-planner"
        assert row["agent_role"] == "reviewer"
        assert row["phase"] == "2"
        assert row["plan"] == "02-01"
        assert row["task"] == "3"


# ---- list_reviews tests ----


class TestListReviews:
    async def test_list_reviews_empty(self, ctx: MockContext) -> None:
        result = await list_reviews.fn(ctx=ctx)
        assert result == {"reviews": []}

    async def test_list_reviews_returns_all(self, ctx: MockContext) -> None:
        await _create_review(ctx, intent="first")
        await _create_review(ctx, intent="second")
        result = await list_reviews.fn(ctx=ctx)
        assert len(result["reviews"]) == 2

    async def test_list_reviews_with_status_filter(self, ctx: MockContext) -> None:
        r1 = await _create_review(ctx, intent="first")
        await _create_review(ctx, intent="second")
        # Claim the first review so it's no longer pending
        await claim_review.fn(
            review_id=r1["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        pending = await list_reviews.fn(status="pending", ctx=ctx)
        assert len(pending["reviews"]) == 1
        assert pending["reviews"][0]["intent"] == "second"


# ---- claim_review tests ----


class TestClaimReview:
    async def test_claim_review_success(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        result = await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        assert result["status"] == "claimed"
        assert result["claimed_by"] == "reviewer-1"

    async def test_claim_review_already_claimed(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-2", ctx=ctx
        )
        assert "error" in result
        assert "Invalid transition" in result["error"]

    async def test_claim_review_not_found(self, ctx: MockContext) -> None:
        result = await claim_review.fn(
            review_id="nonexistent-id", reviewer_id="reviewer-1", ctx=ctx
        )
        assert "error" in result
        assert "not found" in result["error"]


# ---- submit_verdict tests ----


class TestSubmitVerdict:
    async def test_submit_verdict_approved(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="approved",
            reason="Looks good",
            ctx=ctx,
        )
        assert result["status"] == "approved"
        assert result["verdict_reason"] == "Looks good"

    async def test_submit_verdict_changes_requested(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="changes_requested",
            reason="Needs refactor",
            ctx=ctx,
        )
        assert result["status"] == "changes_requested"
        assert result["verdict_reason"] == "Needs refactor"

    async def test_submit_verdict_invalid_verdict_string(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"], verdict="maybe", ctx=ctx
        )
        assert "error" in result
        assert "Invalid verdict" in result["error"]

    async def test_submit_verdict_from_pending_fails(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        result = await submit_verdict.fn(
            review_id=created["review_id"], verdict="approved", ctx=ctx
        )
        assert "error" in result
        assert "Invalid transition" in result["error"]

    async def test_submit_verdict_not_found(self, ctx: MockContext) -> None:
        result = await submit_verdict.fn(
            review_id="nonexistent-id", verdict="approved", ctx=ctx
        )
        assert "error" in result
        assert "not found" in result["error"]


# ---- close_review tests ----


class TestCloseReview:
    async def test_close_review_after_approval(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        await submit_verdict.fn(
            review_id=created["review_id"], verdict="approved", ctx=ctx
        )
        result = await close_review.fn(review_id=created["review_id"], ctx=ctx)
        assert result["status"] == "closed"

    async def test_close_review_after_changes_requested(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        await submit_verdict.fn(
            review_id=created["review_id"], verdict="changes_requested", ctx=ctx
        )
        result = await close_review.fn(review_id=created["review_id"], ctx=ctx)
        assert result["status"] == "closed"

    async def test_close_review_from_pending_fails(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        result = await close_review.fn(review_id=created["review_id"], ctx=ctx)
        assert "error" in result
        assert "Invalid transition" in result["error"]

    async def test_close_review_not_found(self, ctx: MockContext) -> None:
        result = await close_review.fn(review_id="nonexistent-id", ctx=ctx)
        assert "error" in result
        assert "not found" in result["error"]


# ---- Full lifecycle test ----


class TestFullLifecycle:
    async def test_full_lifecycle_create_to_close(self, ctx: MockContext) -> None:
        """End-to-end: create -> list -> claim -> verdict -> close."""
        # Step 1: Create review
        created = await _create_review(ctx, intent="implement auth module")
        review_id = created["review_id"]
        assert created["status"] == "pending"

        # Step 2: List and find it
        listed = await list_reviews.fn(status="pending", ctx=ctx)
        assert len(listed["reviews"]) == 1
        assert listed["reviews"][0]["id"] == review_id

        # Step 3: Claim it
        claimed = await claim_review.fn(
            review_id=review_id, reviewer_id="reviewer-agent", ctx=ctx
        )
        assert claimed["status"] == "claimed"
        assert claimed["claimed_by"] == "reviewer-agent"

        # Step 4: Submit verdict
        verdict = await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            reason="Implementation looks correct",
            ctx=ctx,
        )
        assert verdict["status"] == "approved"
        assert verdict["verdict_reason"] == "Implementation looks correct"

        # Step 5: Close it
        closed = await close_review.fn(review_id=review_id, ctx=ctx)
        assert closed["status"] == "closed"

        # Verify final DB state
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT status, claimed_by, verdict_reason FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["status"] == "closed"
        assert row["claimed_by"] == "reviewer-agent"
        assert row["verdict_reason"] == "Implementation looks correct"
