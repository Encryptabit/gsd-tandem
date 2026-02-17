"""Tests for the NotificationBus async event system."""

from __future__ import annotations

import asyncio

from gsd_review_broker.notifications import NotificationBus


class TestNotificationBus:
    async def test_notify_without_waiter(self) -> None:
        """Notify on a review_id with no waiter should not raise."""
        bus = NotificationBus()
        bus.notify("no-waiter-review")
        # No exception = pass

    async def test_wait_returns_true_on_signal(self) -> None:
        """wait_for_change returns True when notified before timeout."""
        bus = NotificationBus()
        review_id = "test-review-1"

        async def signal_after_delay():
            await asyncio.sleep(0.05)
            bus.notify(review_id)

        task = asyncio.create_task(signal_after_delay())
        result = await bus.wait_for_change(review_id, timeout=5.0)
        assert result is True
        await task

    async def test_wait_returns_false_on_timeout(self) -> None:
        """wait_for_change returns False when no signal arrives within timeout."""
        bus = NotificationBus()
        result = await bus.wait_for_change("timeout-review", timeout=0.1)
        assert result is False

    async def test_cleanup_removes_event(self) -> None:
        """cleanup removes the event entry for a review_id."""
        bus = NotificationBus()
        # Create an event by calling _get_event
        bus._get_event("cleanup-review")
        assert "cleanup-review" in bus._events
        bus.cleanup("cleanup-review")
        assert "cleanup-review" not in bus._events

    async def test_cleanup_nonexistent_is_noop(self) -> None:
        """cleanup on non-existent review_id does not raise."""
        bus = NotificationBus()
        bus.cleanup("does-not-exist")
        # No exception = pass

    async def test_event_cleared_after_signal(self) -> None:
        """After wait_for_change returns True, the event is cleared for reuse."""
        bus = NotificationBus()
        review_id = "reuse-review"

        # First signal
        async def signal():
            await asyncio.sleep(0.05)
            bus.notify(review_id)

        task = asyncio.create_task(signal())
        result = await bus.wait_for_change(review_id, timeout=5.0)
        assert result is True
        await task

        # Event should be cleared, so a second wait without signal should timeout
        result2 = await bus.wait_for_change(review_id, timeout=0.1)
        assert result2 is False

    async def test_multiple_reviews_independent(self) -> None:
        """Events for different review_ids are independent."""
        bus = NotificationBus()

        async def signal_one():
            await asyncio.sleep(0.05)
            bus.notify("review-A")

        task = asyncio.create_task(signal_one())

        result_a = await bus.wait_for_change("review-A", timeout=5.0)
        result_b = await bus.wait_for_change("review-B", timeout=0.1)

        assert result_a is True
        assert result_b is False
        await task
