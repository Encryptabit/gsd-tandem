"""Integration tests for MCP tool handlers using in-memory SQLite."""

from __future__ import annotations

import asyncio
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
            project="alpha",
        )
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT agent_type, agent_role, phase, plan, task, project FROM reviews WHERE id = ?",
            (result["review_id"],),
        )
        row = await cursor.fetchone()
        assert row["agent_type"] == "gsd-planner"
        assert row["agent_role"] == "reviewer"
        assert row["phase"] == "2"
        assert row["plan"] == "02-01"
        assert row["task"] == "3"
        assert row["project"] == "alpha"


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

    async def test_list_reviews_with_project_filter(self, ctx: MockContext) -> None:
        await _create_review(ctx, intent="alpha review", project="alpha")
        await _create_review(ctx, intent="beta review", project="beta")
        scoped = await list_reviews.fn(project="alpha", ctx=ctx)
        assert len(scoped["reviews"]) == 1
        assert scoped["reviews"][0]["intent"] == "alpha review"
        assert scoped["reviews"][0]["project"] == "alpha"

    async def test_list_reviews_with_projects_filter(self, ctx: MockContext) -> None:
        await _create_review(ctx, intent="alpha review", project="alpha")
        await _create_review(ctx, intent="beta review", project="beta")
        await _create_review(ctx, intent="gamma review", project="gamma")
        scoped = await list_reviews.fn(projects=["alpha", "gamma"], ctx=ctx)
        intents = {item["intent"] for item in scoped["reviews"]}
        assert intents == {"alpha review", "gamma review"}

    async def test_list_reviews_rejects_project_and_projects_together(
        self, ctx: MockContext
    ) -> None:
        result = await list_reviews.fn(project="alpha", projects=["beta"], ctx=ctx)
        assert "error" in result
        assert "either 'project' or 'projects'" in result["error"]

    async def test_list_reviews_rejects_empty_projects_filter(self, ctx: MockContext) -> None:
        result = await list_reviews.fn(projects=[], ctx=ctx)
        assert "error" in result
        assert "at least one" in result["error"]


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

    async def test_claim_review_concurrent_calls_are_serialized(
        self, ctx: MockContext
    ) -> None:
        created = await _create_review(ctx)
        review_id = created["review_id"]

        async def claim(reviewer_id: str) -> dict:
            return await claim_review.fn(
                review_id=review_id, reviewer_id=reviewer_id, ctx=ctx
            )

        r1, r2 = await asyncio.gather(claim("reviewer-1"), claim("reviewer-2"))
        successes = [result for result in (r1, r2) if result.get("status") == "claimed"]
        errors = [result for result in (r1, r2) if "error" in result]

        assert len(successes) == 1
        assert len(errors) == 1
        assert "Invalid transition" in errors[0]["error"]


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
            reviewer_id="reviewer-1",
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
            reviewer_id="reviewer-1",
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

    async def test_submit_verdict_comment_records_feedback(self, ctx: MockContext) -> None:
        """Comment verdict records feedback without changing review status."""
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="comment",
            reason="Consider using a dataclass here",
            reviewer_id="reviewer-1",
            ctx=ctx,
        )
        assert result["verdict"] == "comment"
        assert result["status"] == "claimed"
        assert result["verdict_reason"] == "Consider using a dataclass here"
        # Verify status is still claimed in DB
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT status, verdict_reason FROM reviews WHERE id = ?",
            (created["review_id"],),
        )
        row = await cursor.fetchone()
        assert row["status"] == "claimed"
        assert row["verdict_reason"] == "Consider using a dataclass here"

    async def test_submit_verdict_comment_requires_notes(self, ctx: MockContext) -> None:
        """Comment verdict without reason returns error."""
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"], verdict="comment", ctx=ctx
        )
        assert "error" in result
        assert "comment" in result["error"].lower()

    async def test_submit_verdict_comment_whitespace_only_notes_rejected(
        self, ctx: MockContext
    ) -> None:
        """Comment verdict with whitespace-only reason returns error."""
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"], verdict="comment", reason="   ", ctx=ctx
        )
        assert "error" in result
        assert "comment" in result["error"].lower()

    async def test_submit_verdict_changes_requested_requires_notes(
        self, ctx: MockContext
    ) -> None:
        """changes_requested verdict without reason returns error."""
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"], verdict="changes_requested", ctx=ctx
        )
        assert "error" in result
        assert "changes_requested" in result["error"]

    async def test_submit_verdict_changes_requested_whitespace_notes_rejected(
        self, ctx: MockContext
    ) -> None:
        """changes_requested verdict with whitespace-only reason returns error."""
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="changes_requested",
            reason="   ",
            ctx=ctx,
        )
        assert "error" in result
        assert "changes_requested" in result["error"]

    async def test_submit_verdict_approved_without_notes_succeeds(
        self, ctx: MockContext
    ) -> None:
        """Approved verdict without reason succeeds (notes optional)."""
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        result = await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="approved",
            reviewer_id="reviewer-1",
            ctx=ctx,
        )
        assert result["status"] == "approved"
        assert result["verdict_reason"] is None


# ---- close_review tests ----


class TestCloseReview:
    async def test_close_review_after_approval(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="approved",
            reviewer_id="reviewer-1",
            ctx=ctx,
        )
        result = await close_review.fn(review_id=created["review_id"], closer_role="proposer", ctx=ctx)
        assert result["status"] == "closed"

    async def test_close_review_after_changes_requested_fails(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="changes_requested",
            reason="Needs refactor",
            reviewer_id="reviewer-1",
            ctx=ctx,
        )
        result = await close_review.fn(review_id=created["review_id"], closer_role="proposer", ctx=ctx)
        assert "error" in result
        assert "Invalid transition" in result["error"]

    async def test_close_review_reviewer_role_rejected(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        await submit_verdict.fn(
            review_id=created["review_id"],
            verdict="approved",
            reviewer_id="reviewer-1",
            ctx=ctx,
        )
        result = await close_review.fn(
            review_id=created["review_id"], closer_role="reviewer", ctx=ctx
        )
        assert "error" in result
        assert "Only proposer may close" in result["error"]

    async def test_close_review_from_pending_fails(self, ctx: MockContext) -> None:
        created = await _create_review(ctx)
        result = await close_review.fn(review_id=created["review_id"], closer_role="proposer", ctx=ctx)
        assert "error" in result
        assert "Invalid transition" in result["error"]

    async def test_close_review_not_found(self, ctx: MockContext) -> None:
        result = await close_review.fn(review_id="nonexistent-id", closer_role="proposer", ctx=ctx)
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
            reviewer_id="reviewer-agent",
            ctx=ctx,
        )
        assert verdict["status"] == "approved"
        assert verdict["verdict_reason"] == "Implementation looks correct"

        # Step 5: Close it
        closed = await close_review.fn(review_id=review_id, closer_role="proposer", ctx=ctx)
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
