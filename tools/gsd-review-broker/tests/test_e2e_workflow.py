"""End-to-end broker lifecycle tests with observability assertions.

Covers: happy path (create -> claim -> verdict -> close), revision cycle,
counter-patch flow, multi-category data, and observability outputs after
each lifecycle step (feed/status, timeline sequence, audit log ordering,
stats consistency).
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from gsd_review_broker.tools import (
    accept_counter_patch,
    add_message,
    claim_review,
    close_review,
    create_review,
    get_activity_feed,
    get_audit_log,
    get_review_stats,
    get_review_timeline,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext


# ---- Helpers ----

COUNTER_PATCH = """\
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 line one
-line two
+line TWO alternative fix
 line three
"""


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


# ---- TestHappyPathLifecycle ----


class TestHappyPathLifecycle:
    """Full happy path: create -> claim -> message -> verdict -> close with observability."""

    async def test_happy_path_with_observability(self, ctx: MockContext) -> None:
        """Create, claim, message, approve, close -- verify observability at each step."""
        # Step 1: Create review
        created = await _create_review(
            ctx, intent="implement auth module", category="code_change"
        )
        rid = created["review_id"]
        assert created["status"] == "pending"

        # Verify feed after creation
        feed = await get_activity_feed.fn(ctx=ctx)
        assert feed["count"] == 1
        assert feed["reviews"][0]["id"] == rid
        assert feed["reviews"][0]["status"] == "pending"

        # Verify audit log has creation event
        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        assert log["count"] == 1
        assert log["events"][0]["event_type"] == "review_created"

        # Step 2: Claim
        claimed = await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        assert claimed["status"] == "claimed"

        feed = await get_activity_feed.fn(status="claimed", ctx=ctx)
        assert feed["count"] == 1

        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        assert log["count"] == 2
        assert log["events"][1]["event_type"] == "review_claimed"

        # Step 3: Add messages
        await add_message.fn(
            review_id=rid, sender_role="reviewer", body="Looks mostly good", ctx=ctx
        )
        await add_message.fn(
            review_id=rid, sender_role="proposer", body="Thanks, noted", ctx=ctx
        )

        feed = await get_activity_feed.fn(ctx=ctx)
        entry = feed["reviews"][0]
        assert entry["message_count"] == 2
        assert entry["last_message_preview"] == "Thanks, noted"

        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        msg_events = [e for e in log["events"] if e["event_type"] == "message_sent"]
        assert len(msg_events) == 2

        # Step 4: Approve
        verdict = await submit_verdict.fn(
            review_id=rid, verdict="approved", reason="LGTM", ctx=ctx
        )
        assert verdict["status"] == "approved"

        stats = await get_review_stats.fn(ctx=ctx)
        assert stats["by_status"]["approved"] == 1
        assert stats["approval_rate_pct"] == 100.0

        # Step 5: Close
        closed = await close_review.fn(review_id=rid, ctx=ctx)
        assert closed["status"] == "closed"

        # Verify final timeline
        timeline = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        assert timeline["current_status"] == "closed"
        types = [e["event_type"] for e in timeline["events"]]
        assert types[0] == "review_created"
        assert "review_claimed" in types
        assert "message_sent" in types
        assert "verdict_submitted" in types
        assert types[-1] == "review_closed"

        # Verify final stats
        stats = await get_review_stats.fn(ctx=ctx)
        assert stats["total_reviews"] == 1
        assert stats["by_status"]["closed"] == 1
        assert stats["by_category"]["code_change"] == 1
        assert stats["avg_time_to_verdict_seconds"] is not None
        assert stats["avg_review_duration_seconds"] is not None


# ---- TestRevisionCycle ----


class TestRevisionCycle:
    """Revision flow: create -> claim -> changes_requested -> revise -> reclaim -> approve -> close."""

    async def test_revision_cycle_with_observability(self, ctx: MockContext) -> None:
        """Full revision cycle preserves audit trail and updates stats."""
        # Create and claim
        created = await _create_review(ctx, intent="first attempt", category="code_change")
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)

        # Request changes
        await submit_verdict.fn(
            review_id=rid, verdict="changes_requested", reason="Fix typo", ctx=ctx
        )

        stats = await get_review_stats.fn(ctx=ctx)
        assert stats["by_status"]["changes_requested"] == 1

        # Revise
        revised = await create_review.fn(
            intent="second attempt",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            review_id=rid,
            ctx=ctx,
        )
        assert revised["revised"] is True
        assert revised["status"] == "pending"

        # Verify timeline shows revision
        timeline = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        types = [e["event_type"] for e in timeline["events"]]
        assert "review_revised" in types

        # Reclaim and approve
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        await submit_verdict.fn(
            review_id=rid, verdict="approved", reason="Fixed", ctx=ctx
        )
        await close_review.fn(review_id=rid, ctx=ctx)

        # Verify final timeline has full revision cycle
        timeline = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        types = [e["event_type"] for e in timeline["events"]]
        expected_sequence = [
            "review_created",
            "review_claimed",
            "verdict_submitted",  # changes_requested
            "review_revised",
            "review_claimed",  # second claim
            "verdict_submitted",  # approved
            "review_closed",
        ]
        assert types == expected_sequence

        # Verify audit log ordering (all events for this review, ascending by id)
        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        ids = [e["id"] for e in log["events"]]
        assert ids == sorted(ids)
        assert log["count"] == len(expected_sequence)


# ---- TestCounterPatchFlow ----


class TestCounterPatchFlow:
    """Counter-patch flow: create -> claim -> changes_requested+counter_patch -> accept -> close."""

    async def test_counter_patch_flow_with_observability(self, ctx: MockContext) -> None:
        """Counter-patch accept flow generates correct audit trail."""
        created = await _create_review(
            ctx, intent="original proposal", category="code_change"
        )
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)

        # Submit changes_requested with counter-patch
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await submit_verdict.fn(
                review_id=rid,
                verdict="changes_requested",
                reason="Use this instead",
                counter_patch=COUNTER_PATCH,
                ctx=ctx,
            )

        # Accept counter-patch
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await accept_counter_patch.fn(review_id=rid, ctx=ctx)
        assert result["counter_patch_status"] == "accepted"

        # Close after counter-patch acceptance (status is still changes_requested, close it)
        await close_review.fn(review_id=rid, ctx=ctx)

        # Verify timeline has counter_patch_accepted
        timeline = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        types = [e["event_type"] for e in timeline["events"]]
        assert "counter_patch_accepted" in types
        assert "review_created" in types
        assert "review_claimed" in types
        assert "verdict_submitted" in types
        assert "review_closed" in types

        # Verify audit log has counter-patch events
        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        cp_events = [e for e in log["events"] if "counter_patch" in e["event_type"]]
        assert len(cp_events) == 1
        assert cp_events[0]["event_type"] == "counter_patch_accepted"


# ---- TestMultiCategoryData ----


class TestMultiCategoryData:
    """Multi-category data: multiple reviews across categories with observability checks."""

    async def test_multi_category_stats_and_feed(self, ctx: MockContext) -> None:
        """Reviews across categories produce correct stats and feed data."""
        # Create reviews in different categories
        plan = await _create_review(
            ctx,
            intent="plan review",
            agent_type="gsd-planner",
            category="plan_review",
        )
        code = await _create_review(
            ctx,
            intent="code change",
            agent_type="gsd-executor",
            category="code_change",
        )
        verify = await _create_review(
            ctx,
            intent="verification check",
            agent_type="gsd-verifier",
            category="verification",
        )

        # Complete one review
        await claim_review.fn(
            review_id=plan["review_id"], reviewer_id="rev-1", ctx=ctx
        )
        await submit_verdict.fn(
            review_id=plan["review_id"], verdict="approved", ctx=ctx
        )
        await close_review.fn(review_id=plan["review_id"], ctx=ctx)

        # Verify stats
        stats = await get_review_stats.fn(ctx=ctx)
        assert stats["total_reviews"] == 3
        assert stats["by_category"]["plan_review"] == 1
        assert stats["by_category"]["code_change"] == 1
        assert stats["by_category"]["verification"] == 1
        assert stats["by_status"]["closed"] == 1
        assert stats["by_status"]["pending"] == 2

        # Verify filtered feeds
        plan_feed = await get_activity_feed.fn(category="plan_review", ctx=ctx)
        assert plan_feed["count"] == 1
        assert plan_feed["reviews"][0]["status"] == "closed"

        code_feed = await get_activity_feed.fn(category="code_change", ctx=ctx)
        assert code_feed["count"] == 1
        assert code_feed["reviews"][0]["status"] == "pending"

    async def test_agent_types_in_feed(self, ctx: MockContext) -> None:
        """Feed entries correctly report agent_type for different agent types."""
        await _create_review(
            ctx, intent="by planner", agent_type="gsd-planner", category="plan_review"
        )
        await _create_review(
            ctx, intent="by executor", agent_type="gsd-executor", category="code_change"
        )
        await _create_review(
            ctx, intent="by verifier", agent_type="gsd-verifier", category="verification"
        )

        feed = await get_activity_feed.fn(ctx=ctx)
        agent_types = {r["agent_type"] for r in feed["reviews"]}
        assert agent_types == {"gsd-planner", "gsd-executor", "gsd-verifier"}


# ---- TestObservabilityConsistency ----


class TestObservabilityConsistency:
    """Cross-tool consistency: feed, stats, timeline, and audit log agree."""

    async def test_feed_count_matches_stats_total(self, ctx: MockContext) -> None:
        """Activity feed count equals stats total_reviews."""
        await _create_review(ctx, intent="one")
        await _create_review(ctx, intent="two")
        await _create_review(ctx, intent="three")

        feed = await get_activity_feed.fn(ctx=ctx)
        stats = await get_review_stats.fn(ctx=ctx)
        assert feed["count"] == stats["total_reviews"]

    async def test_audit_log_global_count(self, ctx: MockContext) -> None:
        """Global audit log count is at least total_reviews (one event per create)."""
        await _create_review(ctx, intent="one")
        await _create_review(ctx, intent="two")

        log = await get_audit_log.fn(ctx=ctx)
        stats = await get_review_stats.fn(ctx=ctx)
        assert log["count"] >= stats["total_reviews"]

    async def test_timeline_event_count_matches_audit_log(
        self, ctx: MockContext
    ) -> None:
        """Timeline event_count for a review matches audit log count for same review."""
        created = await _create_review(ctx, intent="consistency test")
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        await submit_verdict.fn(review_id=rid, verdict="approved", ctx=ctx)
        await close_review.fn(review_id=rid, ctx=ctx)

        timeline = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        assert timeline["event_count"] == log["count"]
