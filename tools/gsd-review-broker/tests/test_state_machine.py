"""Unit tests for the review lifecycle state machine."""

from __future__ import annotations

import pytest

from gsd_review_broker.models import ReviewStatus
from gsd_review_broker.state_machine import VALID_TRANSITIONS, validate_transition


class TestValidTransitions:
    """Tests for valid state transitions that should succeed."""

    def test_valid_transition_pending_to_claimed(self) -> None:
        validate_transition(ReviewStatus.PENDING, ReviewStatus.CLAIMED)

    def test_valid_transition_claimed_to_approved(self) -> None:
        validate_transition(ReviewStatus.CLAIMED, ReviewStatus.APPROVED)

    def test_valid_transition_claimed_to_changes_requested(self) -> None:
        validate_transition(ReviewStatus.CLAIMED, ReviewStatus.CHANGES_REQUESTED)

    def test_valid_transition_claimed_to_in_review(self) -> None:
        validate_transition(ReviewStatus.CLAIMED, ReviewStatus.IN_REVIEW)

    def test_valid_transition_in_review_to_approved(self) -> None:
        validate_transition(ReviewStatus.IN_REVIEW, ReviewStatus.APPROVED)

    def test_valid_transition_in_review_to_changes_requested(self) -> None:
        validate_transition(ReviewStatus.IN_REVIEW, ReviewStatus.CHANGES_REQUESTED)

    def test_valid_transition_approved_to_closed(self) -> None:
        validate_transition(ReviewStatus.APPROVED, ReviewStatus.CLOSED)

    def test_valid_transition_changes_requested_to_closed(self) -> None:
        validate_transition(ReviewStatus.CHANGES_REQUESTED, ReviewStatus.CLOSED)

    def test_valid_transition_changes_requested_to_pending(self) -> None:
        validate_transition(ReviewStatus.CHANGES_REQUESTED, ReviewStatus.PENDING)


class TestInvalidTransitions:
    """Tests for invalid state transitions that should raise ValueError."""

    def test_invalid_pending_to_approved(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.PENDING, ReviewStatus.APPROVED)

    def test_invalid_pending_to_closed(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.PENDING, ReviewStatus.CLOSED)

    def test_invalid_closed_to_pending(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.CLOSED, ReviewStatus.PENDING)

    def test_invalid_closed_to_claimed(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.CLOSED, ReviewStatus.CLAIMED)

    def test_invalid_closed_to_approved(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.CLOSED, ReviewStatus.APPROVED)

    def test_invalid_approved_to_pending(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.APPROVED, ReviewStatus.PENDING)

    def test_invalid_claimed_to_closed(self) -> None:
        with pytest.raises(ValueError, match="Invalid transition"):
            validate_transition(ReviewStatus.CLAIMED, ReviewStatus.CLOSED)


class TestTransitionCoverage:
    """Tests ensuring all states are covered in the transition map."""

    def test_all_states_have_transition_entry(self) -> None:
        for status in ReviewStatus:
            assert status in VALID_TRANSITIONS, f"Missing transition entry for {status}"

    def test_closed_is_terminal(self) -> None:
        assert VALID_TRANSITIONS[ReviewStatus.CLOSED] == set()
