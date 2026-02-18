"""State machine for review lifecycle transitions."""

from __future__ import annotations

from gsd_review_broker.models import ReviewStatus

VALID_TRANSITIONS: dict[ReviewStatus, set[ReviewStatus]] = {
    ReviewStatus.PENDING: {ReviewStatus.CLAIMED},
    ReviewStatus.CLAIMED: {
        ReviewStatus.PENDING,  # reclaim on timeout
        ReviewStatus.IN_REVIEW,
        ReviewStatus.APPROVED,
        ReviewStatus.CHANGES_REQUESTED,
    },
    ReviewStatus.IN_REVIEW: {ReviewStatus.APPROVED, ReviewStatus.CHANGES_REQUESTED},
    ReviewStatus.APPROVED: {ReviewStatus.CLOSED},
    ReviewStatus.CHANGES_REQUESTED: {ReviewStatus.CLOSED, ReviewStatus.PENDING},  # resubmit
    ReviewStatus.CLOSED: set(),  # terminal
}


def validate_transition(current: ReviewStatus, target: ReviewStatus) -> None:
    """Validate a state transition. Raises ValueError if invalid."""
    allowed = VALID_TRANSITIONS.get(current)
    if allowed is None:
        raise ValueError(f"Unknown state: {current}")
    if target not in allowed:
        raise ValueError(
            f"Invalid transition: {current} -> {target}. "
            f"Valid targets from {current}: {sorted(allowed)}"
        )
