"""Tests for fenced claim reclaim behavior and stale verdict protection."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from gsd_review_broker.tools import (
    claim_review,
    close_review,
    create_review,
    reclaim_review,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext


async def _create_review(ctx: MockContext, **overrides) -> dict:
    payload = {
        "intent": "test reclaim",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "7",
    }
    payload.update(overrides)
    return await create_review.fn(**payload, ctx=ctx)


async def _insert_reviewer(ctx: MockContext, reviewer_id: str, status: str = "active") -> None:
    await ctx.lifespan_context.db.execute(
        """INSERT INTO reviewers (id, display_name, session_token, status)
           VALUES (?, ?, ?, ?)""",
        (reviewer_id, reviewer_id, "session-x", status),
    )


async def test_claim_review_returns_claim_generation(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    claim = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id="reviewer-a",
        ctx=ctx,
    )
    assert claim["claim_generation"] >= 1


async def test_claim_review_sets_claimed_at(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT claimed_at FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    row = await cursor.fetchone()
    assert row["claimed_at"] is not None


async def test_submit_verdict_with_valid_claim_generation(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    assert result["status"] == "approved"


async def test_submit_verdict_with_stale_claim_generation(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    claim_a = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="timeout")
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-b", ctx=ctx)
    stale = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        claim_generation=claim_a["claim_generation"],
        ctx=ctx,
    )
    assert "error" in stale
    assert "Stale claim" in stale["error"]


async def test_submit_verdict_without_claim_generation_backward_compat(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        ctx=ctx,
    )
    assert result["status"] == "approved"


async def test_reclaim_review_transitions_to_pending(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    assert result["status"] == "pending"
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, claimed_by FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] == "pending"
    assert row["claimed_by"] is None


async def test_reclaim_review_increments_claim_generation(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    assert result["claim_generation"] == claim["claim_generation"] + 1


async def test_reclaim_review_clears_claimed_at(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT claimed_at FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    row = await cursor.fetchone()
    assert row["claimed_at"] is None


async def test_reclaim_review_only_claimed_status(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    result = await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    assert "error" in result


async def test_full_race_scenario(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    claim_a = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    claim_b = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-b", ctx=ctx)

    stale = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        claim_generation=claim_a["claim_generation"],
        ctx=ctx,
    )
    assert "error" in stale
    valid = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-b",
        claim_generation=claim_b["claim_generation"],
        ctx=ctx,
    )
    assert valid["status"] == "approved"


async def test_reclaim_records_audit_event(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT event_type FROM audit_events WHERE review_id = ? ORDER BY id",
        (created["review_id"],),
    )
    events = [row["event_type"] for row in await cursor.fetchall()]
    assert "review_reclaimed" in events


async def test_comment_verdict_with_stale_claim_generation(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    claim_a = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-b", ctx=ctx)
    stale = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="comment",
        reason="late comment",
        reviewer_id="reviewer-a",
        claim_generation=claim_a["claim_generation"],
        ctx=ctx,
    )
    assert "error" in stale
    assert "Stale claim" in stale["error"]


async def test_submit_verdict_wrong_reviewer_rejected(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-b",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    assert "error" in result
    assert "Unauthorized" in result["error"]


async def test_submit_verdict_correct_reviewer_accepted(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    assert result["status"] == "approved"


async def test_submit_verdict_no_reviewer_id_with_claim_generation_accepted(
    ctx: MockContext,
) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    assert result["status"] == "approved"


async def test_submit_verdict_claimed_review_both_omitted_rejected(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        ctx=ctx,
    )
    assert "error" in result
    assert "require reviewer_id or claim_generation" in result["error"]


async def test_submit_verdict_non_claimed_review_both_omitted_accepted(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviews SET status = 'in_review' WHERE id = ?",
        (created["review_id"],),
    )
    result = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        ctx=ctx,
    )
    assert result["status"] == "approved"


async def test_claim_review_rejects_draining_reviewer(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-draining", status="draining")
    created = await _create_review(ctx)
    result = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id="reviewer-draining",
        ctx=ctx,
    )
    assert "error" in result
    assert "cannot claim new reviews" in result["error"]


async def test_claim_review_rejects_terminated_reviewer(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-terminated", status="terminated")
    created = await _create_review(ctx)
    result = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id="reviewer-terminated",
        ctx=ctx,
    )
    assert "error" in result
    assert "cannot claim new reviews" in result["error"]


async def test_drain_finalization_after_terminal_verdict(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    assert "error" not in claim
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET status = 'draining' WHERE id = ?",
        ("reviewer-a",),
    )
    await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, terminated_at FROM reviewers WHERE id = ?",
        ("reviewer-a",),
    )
    row = await cursor.fetchone()
    assert row["status"] == "draining"
    assert row["terminated_at"] is None

    await close_review.fn(review_id=created["review_id"], closer_role="proposer", ctx=ctx)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, terminated_at FROM reviewers WHERE id = ?",
        ("reviewer-a",),
    )
    row = await cursor.fetchone()
    assert row["status"] == "terminated"
    assert row["terminated_at"] is not None


async def test_drain_finalization_after_reclaim(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET status = 'draining' WHERE id = ?",
        ("reviewer-a",),
    )
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, terminated_at FROM reviewers WHERE id = ?",
        ("reviewer-a",),
    )
    row = await cursor.fetchone()
    assert row["status"] == "terminated"
    assert row["terminated_at"] is not None


async def test_drain_finalization_not_triggered_with_remaining_claims(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "reviewer-a", status="active")
    first = await _create_review(ctx, intent="one")
    second = await _create_review(ctx, intent="two")
    claim_one = await claim_review.fn(review_id=first["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await claim_review.fn(review_id=second["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET status = 'draining' WHERE id = ?",
        ("reviewer-a",),
    )
    await submit_verdict.fn(
        review_id=first["review_id"],
        verdict="approved",
        reviewer_id="reviewer-a",
        claim_generation=claim_one["claim_generation"],
        ctx=ctx,
    )
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        ("reviewer-a",),
    )
    row = await cursor.fetchone()
    assert row["status"] == "draining"


async def test_claim_review_allows_unknown_reviewer_id(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    result = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id="manual-reviewer-xyz",
        ctx=ctx,
    )
    assert result["status"] == "claimed"
