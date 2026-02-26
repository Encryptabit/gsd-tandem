"""Tests for get_activity_feed and get_audit_log observability tools.

Covers: empty state, status/category filters, combined filters, message preview
truncation, ISO 8601 timestamp fields, deterministic ordering on updated_at ties,
audit log retrieval (per-review, all-events, nonexistent review), and event ordering.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from gsd_review_broker.tools import (
    add_message,
    claim_review,
    close_review,
    create_review,
    get_activity_feed,
    get_audit_log,
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


# ---- TestActivityFeedBasic ----


class TestActivityFeedBasic:
    async def test_empty_feed(self, ctx: MockContext) -> None:
        """Empty database returns empty feed with count 0."""
        result = await get_activity_feed.fn(ctx=ctx)
        assert result["reviews"] == []
        assert result["count"] == 0

    async def test_single_review_in_feed(self, ctx: MockContext) -> None:
        """A single review appears in feed with correct fields."""
        created = await _create_review(ctx, intent="implement auth", category="code_change")
        result = await get_activity_feed.fn(ctx=ctx)
        assert result["count"] == 1
        entry = result["reviews"][0]
        assert entry["id"] == created["review_id"]
        assert entry["status"] == "pending"
        assert entry["intent"] == "implement auth"
        assert entry["agent_type"] == "gsd-executor"
        assert entry["phase"] == "1"
        assert entry["category"] == "code_change"
        assert entry["message_count"] == 0
        assert entry["last_message_at"] is None
        assert entry["last_message_preview"] is None

    async def test_feed_iso8601_timestamps(self, ctx: MockContext) -> None:
        """Activity feed timestamps are ISO 8601 with T separator and Z suffix."""
        await _create_review(ctx)
        result = await get_activity_feed.fn(ctx=ctx)
        entry = result["reviews"][0]
        iso_pattern = r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z"
        assert re.match(
            iso_pattern, entry["created_at"]
        ), f"created_at not ISO 8601: {entry['created_at']}"
        assert re.match(
            iso_pattern, entry["updated_at"]
        ), f"updated_at not ISO 8601: {entry['updated_at']}"

    async def test_feed_message_preview_and_count(self, ctx: MockContext) -> None:
        """Feed entry includes message count, last_message_at, and truncated preview."""
        created = await _create_review(ctx)
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        await add_message.fn(
            review_id=rid, sender_role="reviewer", body="First message body", ctx=ctx
        )
        await add_message.fn(
            review_id=rid, sender_role="proposer", body="Second message body", ctx=ctx
        )

        result = await get_activity_feed.fn(ctx=ctx)
        entry = result["reviews"][0]
        assert entry["message_count"] == 2
        assert entry["last_message_at"] is not None
        assert entry["last_message_preview"] == "Second message body"

    async def test_feed_message_preview_truncation(self, ctx: MockContext) -> None:
        """Message previews longer than 120 chars are truncated."""
        created = await _create_review(ctx)
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        long_body = "A" * 200
        await add_message.fn(review_id=rid, sender_role="reviewer", body=long_body, ctx=ctx)

        result = await get_activity_feed.fn(ctx=ctx)
        preview = result["reviews"][0]["last_message_preview"]
        assert len(preview) == 120
        assert preview == "A" * 120


# ---- TestActivityFeedFilters ----


class TestActivityFeedFilters:
    async def test_filter_by_status(self, ctx: MockContext) -> None:
        """Filtering by status returns only matching reviews."""
        r1 = await _create_review(ctx, intent="pending one")
        await _create_review(ctx, intent="pending two")
        await claim_review.fn(review_id=r1["review_id"], reviewer_id="rev-1", ctx=ctx)

        pending = await get_activity_feed.fn(status="pending", ctx=ctx)
        assert pending["count"] == 1
        assert pending["reviews"][0]["intent"] == "pending two"

        claimed = await get_activity_feed.fn(status="claimed", ctx=ctx)
        assert claimed["count"] == 1
        assert claimed["reviews"][0]["id"] == r1["review_id"]

    async def test_filter_by_category(self, ctx: MockContext) -> None:
        """Filtering by category returns only matching reviews."""
        await _create_review(ctx, intent="plan", category="plan_review")
        await _create_review(ctx, intent="code", category="code_change")
        await _create_review(ctx, intent="verify", category="verification")

        result = await get_activity_feed.fn(category="code_change", ctx=ctx)
        assert result["count"] == 1
        assert result["reviews"][0]["intent"] == "code"

    async def test_filter_by_project(self, ctx: MockContext) -> None:
        """Filtering by project returns only matching reviews."""
        await _create_review(ctx, intent="alpha plan", project="alpha")
        await _create_review(ctx, intent="beta plan", project="beta")

        result = await get_activity_feed.fn(project="alpha", ctx=ctx)
        assert result["count"] == 1
        assert result["reviews"][0]["intent"] == "alpha plan"
        assert result["reviews"][0]["project"] == "alpha"

    async def test_combined_filters(self, ctx: MockContext) -> None:
        """Filtering by status AND category narrows results correctly."""
        r1 = await _create_review(ctx, intent="pending plan", category="plan_review")
        await _create_review(ctx, intent="pending code", category="code_change")
        await claim_review.fn(review_id=r1["review_id"], reviewer_id="rev-1", ctx=ctx)

        result = await get_activity_feed.fn(status="pending", category="code_change", ctx=ctx)
        assert result["count"] == 1
        assert result["reviews"][0]["intent"] == "pending code"

        # Claimed plan_review
        result2 = await get_activity_feed.fn(status="claimed", category="plan_review", ctx=ctx)
        assert result2["count"] == 1
        assert result2["reviews"][0]["id"] == r1["review_id"]


# ---- TestActivityFeedOrdering ----


class TestActivityFeedOrdering:
    async def test_most_recently_updated_first(self, ctx: MockContext) -> None:
        """Feed is ordered by updated_at DESC: most recently updated review first."""
        db = ctx.lifespan_context.db
        # Insert two reviews with explicitly different updated_at timestamps
        await db.execute(
            """INSERT INTO reviews (id, status, intent, agent_type, agent_role,
                                    phase, created_at, updated_at)
               VALUES ('older-id', 'pending', 'older', 'gsd-executor', 'proposer', '1',
                        '2026-01-10 10:00:00', '2026-01-10 10:00:00')""",
        )
        await db.execute(
            """INSERT INTO reviews (id, status, intent, agent_type, agent_role,
                                    phase, created_at, updated_at)
               VALUES ('newer-id', 'pending', 'newer', 'gsd-executor', 'proposer', '1',
                        '2026-01-10 11:00:00', '2026-01-10 12:00:00')""",
        )

        result = await get_activity_feed.fn(ctx=ctx)
        assert result["count"] == 2
        # newer-id has later updated_at, so it should be first
        assert result["reviews"][0]["id"] == "newer-id"
        assert result["reviews"][1]["id"] == "older-id"

    async def test_deterministic_ordering_on_updated_at_tie(self, ctx: MockContext) -> None:
        """When two reviews have identical updated_at, feed orders by id DESC for determinism."""
        db = ctx.lifespan_context.db
        # Insert two reviews with identical updated_at timestamps
        fixed_ts = "2026-01-15 12:00:00"
        for label in ("alpha", "beta"):
            rid = f"tie-{label}"
            await db.execute(
                """INSERT INTO reviews (id, status, intent, agent_type, agent_role,
                                        phase, category, created_at, updated_at)
                   VALUES (?, 'pending', ?, 'gsd-executor', 'proposer', '1', 'code_change',
                           ?, ?)""",
                (rid, f"intent {label}", fixed_ts, fixed_ts),
            )

        result = await get_activity_feed.fn(ctx=ctx)
        assert result["count"] == 2
        ids = [r["id"] for r in result["reviews"]]
        # 'tie-beta' > 'tie-alpha' lexicographically, so DESC puts beta first
        assert ids == ["tie-beta", "tie-alpha"]


# ---- TestAuditLog ----


class TestAuditLog:
    async def test_audit_log_per_review(self, ctx: MockContext) -> None:
        """get_audit_log with review_id returns events for that review only."""
        r1 = await _create_review(ctx, intent="review one")
        await _create_review(ctx, intent="review two")

        log1 = await get_audit_log.fn(review_id=r1["review_id"], ctx=ctx)
        assert log1["review_id"] == r1["review_id"]
        assert log1["count"] >= 1
        # All events belong to r1
        for event in log1["events"]:
            assert event["review_id"] == r1["review_id"]

    async def test_audit_log_all_events(self, ctx: MockContext) -> None:
        """get_audit_log without review_id returns events from all reviews."""
        await _create_review(ctx, intent="review one")
        await _create_review(ctx, intent="review two")

        log = await get_audit_log.fn(ctx=ctx)
        assert "review_id" not in log  # No review_id key when querying all
        assert log["count"] >= 2  # At least one event per review

    async def test_audit_log_nonexistent_review(self, ctx: MockContext) -> None:
        """get_audit_log with nonexistent review_id returns error."""
        result = await get_audit_log.fn(review_id="nonexistent-id", ctx=ctx)
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_audit_log_event_ordering(self, ctx: MockContext) -> None:
        """Audit events are ordered by id ASC (insertion order)."""
        created = await _create_review(ctx)
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        await submit_verdict.fn(review_id=rid, verdict="approved", ctx=ctx)
        await close_review.fn(review_id=rid, closer_role="proposer", ctx=ctx)

        log = await get_audit_log.fn(review_id=rid, ctx=ctx)
        events = log["events"]
        assert len(events) >= 4  # created, claimed, verdict, closed
        # IDs should be monotonically increasing
        ids = [e["id"] for e in events]
        assert ids == sorted(ids)
        # Event types follow lifecycle
        types = [e["event_type"] for e in events]
        assert types[0] == "review_created"
        assert "review_claimed" in types
        assert "verdict_submitted" in types
        assert types[-1] == "review_closed"

    async def test_audit_log_iso8601_timestamps(self, ctx: MockContext) -> None:
        """Audit log event timestamps are ISO 8601."""
        await _create_review(ctx)
        log = await get_audit_log.fn(ctx=ctx)
        assert log["count"] >= 1
        ts = log["events"][0]["created_at"]
        assert "T" in ts
        assert ts.endswith("Z")
        assert re.match(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}\.\d+Z", ts)

    async def test_audit_log_metadata_parsed(self, ctx: MockContext) -> None:
        """Audit log events have metadata parsed as dict (not raw JSON string)."""
        created = await _create_review(ctx, intent="test meta", category="plan_review")
        log = await get_audit_log.fn(review_id=created["review_id"], ctx=ctx)
        # review_created event should have metadata with intent and category
        event = log["events"][0]
        assert event["event_type"] == "review_created"
        assert isinstance(event["metadata"], dict)
        assert event["metadata"]["intent"] == "test meta"
        assert event["metadata"]["category"] == "plan_review"


# ---- TestReviewTimeline ----


class TestReviewTimeline:
    async def test_timeline_full_lifecycle(self, ctx: MockContext) -> None:
        """Timeline shows chronological events for a complete lifecycle."""
        created = await _create_review(ctx, intent="timeline test", category="code_change")
        rid = created["review_id"]
        await claim_review.fn(review_id=rid, reviewer_id="rev-1", ctx=ctx)
        await submit_verdict.fn(review_id=rid, verdict="approved", reason="LGTM", ctx=ctx)
        await close_review.fn(review_id=rid, closer_role="proposer", ctx=ctx)

        result = await get_review_timeline.fn(review_id=rid, ctx=ctx)
        assert result["review_id"] == rid
        assert result["intent"] == "timeline test"
        assert result["current_status"] == "closed"
        assert result["category"] == "code_change"
        assert result["event_count"] >= 4

        types = [e["event_type"] for e in result["events"]]
        assert types[0] == "review_created"
        assert types[-1] == "review_closed"

    async def test_timeline_nonexistent_review(self, ctx: MockContext) -> None:
        """Timeline for nonexistent review returns error."""
        result = await get_review_timeline.fn(review_id="nonexistent-id", ctx=ctx)
        assert "error" in result
        assert "not found" in result["error"].lower()

    async def test_timeline_events_have_timestamps(self, ctx: MockContext) -> None:
        """Each timeline event has a timestamp field."""
        created = await _create_review(ctx)
        result = await get_review_timeline.fn(review_id=created["review_id"], ctx=ctx)
        for event in result["events"]:
            assert "timestamp" in event
            assert "T" in event["timestamp"]
