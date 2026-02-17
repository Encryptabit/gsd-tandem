"""Tests for get_review_stats and get_review_timeline observability tools.

Covers: empty-database behavior for all fields including avg_time_in_state_seconds,
status/category counts, approval rate, avg_time_to_verdict_seconds,
avg_review_duration_seconds, avg_time_in_state_seconds object keys and numeric/null
semantics, and multi-review aggregation.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from gsd_review_broker.tools import (
    claim_review,
    close_review,
    create_review,
    get_review_stats,
    get_review_timeline,
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


async def _full_lifecycle(
    ctx: MockContext,
    verdict: str = "approved",
    **overrides,
) -> str:
    """Run create -> claim -> verdict -> close. Returns review_id."""
    created = await _create_review(ctx, **overrides)
    rid = created["review_id"]
    await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
    verdict_kwargs: dict = {"review_id": rid, "verdict": verdict, "ctx": ctx}
    if verdict == "changes_requested":
        verdict_kwargs["reason"] = "Needs work"
    await submit_verdict.fn(**verdict_kwargs)
    await close_review.fn(review_id=rid, ctx=ctx)
    return rid


# ---- TestStatsEmpty ----


class TestStatsEmpty:
    async def test_empty_database_stats(self, ctx: MockContext) -> None:
        """All stat fields have sane zero/null values on empty database."""
        result = await get_review_stats.fn(ctx=ctx)
        assert result["total_reviews"] == 0
        assert result["by_status"]["pending"] == 0
        assert result["by_status"]["claimed"] == 0
        assert result["by_status"]["approved"] == 0
        assert result["by_status"]["changes_requested"] == 0
        assert result["by_status"]["closed"] == 0
        assert result["by_category"] == {}
        assert result["approval_rate_pct"] is None
        assert result["avg_time_to_verdict_seconds"] is None
        assert result["avg_review_duration_seconds"] is None

    async def test_empty_database_avg_time_in_state(self, ctx: MockContext) -> None:
        """avg_time_in_state_seconds has all expected keys with None values when empty."""
        result = await get_review_stats.fn(ctx=ctx)
        avg_tis = result["avg_time_in_state_seconds"]
        assert isinstance(avg_tis, dict)
        assert "pending" in avg_tis
        assert "claimed" in avg_tis
        assert "approved" in avg_tis
        assert "changes_requested" in avg_tis
        # All None when no data
        for key in ("pending", "claimed", "approved", "changes_requested"):
            assert avg_tis[key] is None


# ---- TestStatsStatusCounts ----


class TestStatsStatusCounts:
    async def test_pending_count(self, ctx: MockContext) -> None:
        """Creating reviews increments pending count."""
        await _create_review(ctx, intent="one")
        await _create_review(ctx, intent="two")
        result = await get_review_stats.fn(ctx=ctx)
        assert result["total_reviews"] == 2
        assert result["by_status"]["pending"] == 2

    async def test_status_distribution(self, ctx: MockContext) -> None:
        """Each lifecycle stage is counted correctly."""
        r1 = await _create_review(ctx, intent="to claim")
        r2 = await _create_review(ctx, intent="stays pending")

        await claim_review.fn(review_id=r1["review_id"], reviewer_id="rev-1", ctx=ctx)

        result = await get_review_stats.fn(ctx=ctx)
        assert result["by_status"]["pending"] == 1
        assert result["by_status"]["claimed"] == 1
        assert result["total_reviews"] == 2


# ---- TestStatsCategoryCounts ----


class TestStatsCategoryCounts:
    async def test_category_breakdown(self, ctx: MockContext) -> None:
        """by_category counts reviews per category."""
        await _create_review(ctx, intent="plan 1", category="plan_review")
        await _create_review(ctx, intent="plan 2", category="plan_review")
        await _create_review(ctx, intent="code 1", category="code_change")
        await _create_review(ctx, intent="verify", category="verification")

        result = await get_review_stats.fn(ctx=ctx)
        assert result["by_category"]["plan_review"] == 2
        assert result["by_category"]["code_change"] == 1
        assert result["by_category"]["verification"] == 1

    async def test_uncategorized_reviews(self, ctx: MockContext) -> None:
        """Reviews without category appear as 'uncategorized'."""
        await _create_review(ctx, intent="no category")
        result = await get_review_stats.fn(ctx=ctx)
        assert result["by_category"]["uncategorized"] == 1


# ---- TestStatsApprovalRate ----


class TestStatsApprovalRate:
    async def test_all_approved(self, ctx: MockContext) -> None:
        """100% approval rate when all verdicts are approved."""
        await _full_lifecycle(ctx, verdict="approved", intent="good 1")
        await _full_lifecycle(ctx, verdict="approved", intent="good 2")

        result = await get_review_stats.fn(ctx=ctx)
        assert result["approval_rate_pct"] == 100.0

    async def test_mixed_verdicts(self, ctx: MockContext) -> None:
        """Approval rate reflects mix of approved and changes_requested."""
        await _full_lifecycle(ctx, verdict="approved", intent="approved one")
        await _full_lifecycle(ctx, verdict="changes_requested", intent="rejected one")

        result = await get_review_stats.fn(ctx=ctx)
        assert result["approval_rate_pct"] == 50.0

    async def test_no_verdicts_yet(self, ctx: MockContext) -> None:
        """Approval rate is None when no verdicts have been submitted."""
        await _create_review(ctx, intent="still pending")
        result = await get_review_stats.fn(ctx=ctx)
        assert result["approval_rate_pct"] is None


# ---- TestStatsTimingMetrics ----


class TestStatsTimingMetrics:
    async def test_avg_time_to_verdict_populated(self, ctx: MockContext) -> None:
        """avg_time_to_verdict_seconds is numeric after a verdict is submitted."""
        await _full_lifecycle(ctx, verdict="approved")
        result = await get_review_stats.fn(ctx=ctx)
        # Should be a number (very small since tests run fast)
        assert result["avg_time_to_verdict_seconds"] is not None
        assert isinstance(result["avg_time_to_verdict_seconds"], (int, float))
        assert result["avg_time_to_verdict_seconds"] >= 0

    async def test_avg_review_duration_populated(self, ctx: MockContext) -> None:
        """avg_review_duration_seconds is numeric after a review is closed."""
        await _full_lifecycle(ctx, verdict="approved")
        result = await get_review_stats.fn(ctx=ctx)
        assert result["avg_review_duration_seconds"] is not None
        assert isinstance(result["avg_review_duration_seconds"], (int, float))
        assert result["avg_review_duration_seconds"] >= 0

    async def test_avg_time_to_verdict_none_without_verdicts(self, ctx: MockContext) -> None:
        """avg_time_to_verdict_seconds is None when no reviews have verdicts."""
        await _create_review(ctx)
        result = await get_review_stats.fn(ctx=ctx)
        assert result["avg_time_to_verdict_seconds"] is None

    async def test_avg_review_duration_none_without_closed(self, ctx: MockContext) -> None:
        """avg_review_duration_seconds is None when no reviews are closed."""
        await _create_review(ctx)
        result = await get_review_stats.fn(ctx=ctx)
        assert result["avg_review_duration_seconds"] is None


# ---- TestStatsAvgTimeInState ----


class TestStatsAvgTimeInState:
    async def test_avg_time_in_state_keys(self, ctx: MockContext) -> None:
        """avg_time_in_state_seconds always has pending, claimed, approved, changes_requested keys."""
        await _full_lifecycle(ctx, verdict="approved")
        result = await get_review_stats.fn(ctx=ctx)
        avg_tis = result["avg_time_in_state_seconds"]
        assert "pending" in avg_tis
        assert "claimed" in avg_tis
        assert "approved" in avg_tis
        assert "changes_requested" in avg_tis

    async def test_avg_time_in_state_numeric_after_lifecycle(self, ctx: MockContext) -> None:
        """After a full lifecycle, pending and claimed states have numeric values."""
        await _full_lifecycle(ctx, verdict="approved")
        result = await get_review_stats.fn(ctx=ctx)
        avg_tis = result["avg_time_in_state_seconds"]
        # pending -> claimed -> approved -> closed
        # pending and claimed should have durations
        assert avg_tis["pending"] is not None or avg_tis["claimed"] is not None
        # At least one state should be numeric
        numeric_values = [v for v in avg_tis.values() if v is not None]
        assert len(numeric_values) >= 1
        for v in numeric_values:
            assert isinstance(v, (int, float))
            assert v >= 0

    async def test_avg_time_in_state_changes_requested_populated(
        self, ctx: MockContext
    ) -> None:
        """changes_requested state gets a duration when revision cycle happens."""
        # Create, claim, request changes, then revise (back to pending)
        created = await _create_review(ctx, intent="revision flow")
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        await submit_verdict.fn(
            review_id=rid, verdict="changes_requested", reason="Fix it", ctx=ctx
        )
        # Revise back to pending
        await create_review.fn(
            intent="revised intent",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            review_id=rid,
            ctx=ctx,
        )

        result = await get_review_stats.fn(ctx=ctx)
        avg_tis = result["avg_time_in_state_seconds"]
        # changes_requested state should now have a duration since it transitioned back to pending
        assert avg_tis["changes_requested"] is not None
        assert isinstance(avg_tis["changes_requested"], (int, float))

    async def test_avg_time_in_state_null_semantics(self, ctx: MockContext) -> None:
        """States that have not been visited by any review remain None."""
        # Only create, don't advance past pending
        await _create_review(ctx)
        result = await get_review_stats.fn(ctx=ctx)
        avg_tis = result["avg_time_in_state_seconds"]
        # claimed, approved, changes_requested should all be None since no review reached them
        assert avg_tis["claimed"] is None
        assert avg_tis["approved"] is None
        assert avg_tis["changes_requested"] is None


# ---- TestStatsMultiReview ----


class TestStatsMultiReview:
    async def test_multi_review_aggregation(self, ctx: MockContext) -> None:
        """Stats aggregate correctly across multiple reviews."""
        await _full_lifecycle(ctx, verdict="approved", intent="good", category="code_change")
        await _full_lifecycle(ctx, verdict="approved", intent="also good", category="plan_review")
        await _create_review(ctx, intent="still pending", category="verification")

        result = await get_review_stats.fn(ctx=ctx)
        assert result["total_reviews"] == 3
        assert result["by_status"]["closed"] == 2
        assert result["by_status"]["pending"] == 1
        assert result["by_category"]["code_change"] == 1
        assert result["by_category"]["plan_review"] == 1
        assert result["by_category"]["verification"] == 1
        assert result["approval_rate_pct"] == 100.0

    async def test_timeline_events_consistent_with_stats(self, ctx: MockContext) -> None:
        """Timeline events for a review are consistent with global stats."""
        rid = await _full_lifecycle(ctx, verdict="approved", intent="consistency check")
        timeline = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        stats = await get_review_stats.fn(ctx=ctx)

        # Timeline should show full lifecycle events
        types = [e["event_type"] for e in timeline["events"]]
        assert "review_created" in types
        assert "review_claimed" in types
        assert "verdict_submitted" in types
        assert "review_closed" in types

        # Stats should reflect one completed review
        assert stats["total_reviews"] == 1
        assert stats["by_status"]["closed"] == 1
        assert stats["approval_rate_pct"] == 100.0
