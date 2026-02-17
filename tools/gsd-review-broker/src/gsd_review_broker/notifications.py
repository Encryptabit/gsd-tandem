"""Internal asyncio event bus for review change notifications.

Provides a lightweight pub/sub mechanism so that tools can signal
when a review changes (new message, status update, etc.) and waiters
can be notified without polling the database.
"""

from __future__ import annotations

import asyncio
import time
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
    _versions: dict[str, int] = field(default_factory=dict)

    def _get_event(self, review_id: str) -> asyncio.Event:
        """Get or create the event for a review_id."""
        if review_id not in self._events:
            self._events[review_id] = asyncio.Event()
        return self._events[review_id]

    def current_version(self, review_id: str) -> int:
        """Return the current notification version for a review."""
        return self._versions.get(review_id, 0)

    def notify(self, review_id: str) -> None:
        """Signal that a review has changed.

        Increments the review version and sets the event so waiters can wake.
        """
        self._versions[review_id] = self.current_version(review_id) + 1
        event = self._get_event(review_id)
        event.set()

    async def wait_for_change(
        self,
        review_id: str,
        timeout: float = 25.0,
        since_version: int | None = None,
    ) -> bool:
        """Wait for a change notification on a review.

        Returns True if signaled within timeout, False on timeout.
        If since_version is provided, waits until the review version changes
        from that value. Without since_version, it waits for the next change
        from the current point-in-time.
        """
        event = self._get_event(review_id)
        baseline = self.current_version(review_id) if since_version is None else since_version
        deadline = time.monotonic() + timeout

        while True:
            if self.current_version(review_id) != baseline:
                return True

            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return False

            try:
                await asyncio.wait_for(event.wait(), timeout=remaining)
            except TimeoutError:
                return False

            # Consume this wake and re-check version in loop.
            event.clear()

    def cleanup(self, review_id: str) -> None:
        """Remove the event for a closed review."""
        self._events.pop(review_id, None)
        self._versions.pop(review_id, None)
