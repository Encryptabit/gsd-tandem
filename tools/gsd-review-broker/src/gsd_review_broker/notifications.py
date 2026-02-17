"""Internal asyncio event bus for review change notifications.

Provides a lightweight pub/sub mechanism so that tools can signal
when a review changes (new message, status update, etc.) and waiters
can be notified without polling the database.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field


@dataclass
class NotificationBus:
    """Per-review asyncio.Event bus for internal change signaling.

    Usage:
        bus = NotificationBus()

        # Waiter (e.g. long-poll endpoint):
        changed = await bus.wait_for_change("review-123", timeout=25.0)

        # Notifier (e.g. after message insert):
        bus.notify("review-123")

        # Cleanup (e.g. after review closes):
        bus.cleanup("review-123")
    """

    _events: dict[str, asyncio.Event] = field(default_factory=dict)

    def _get_event(self, review_id: str) -> asyncio.Event:
        """Get or create the event for a review_id."""
        if review_id not in self._events:
            self._events[review_id] = asyncio.Event()
        return self._events[review_id]

    def notify(self, review_id: str) -> None:
        """Signal that a review has changed.

        No-op if no waiter exists for this review_id.
        """
        event = self._events.get(review_id)
        if event is not None:
            event.set()

    async def wait_for_change(self, review_id: str, timeout: float = 25.0) -> bool:
        """Wait for a change notification on a review.

        Returns True if signaled within timeout, False on timeout.
        Clears the event after being signaled so it can be reused.
        """
        event = self._get_event(review_id)
        try:
            await asyncio.wait_for(event.wait(), timeout=timeout)
            event.clear()
            return True
        except asyncio.TimeoutError:
            return False

    def cleanup(self, review_id: str) -> None:
        """Remove the event for a closed review."""
        self._events.pop(review_id, None)
