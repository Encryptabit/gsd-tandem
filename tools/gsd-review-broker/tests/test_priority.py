"""Tests for priority inference from agent identity."""

from __future__ import annotations

from gsd_review_broker.models import Priority
from gsd_review_broker.priority import infer_priority


class TestInferPriority:
    def test_planner_is_critical(self) -> None:
        """Planner agent type infers CRITICAL priority."""
        result = infer_priority(
            agent_type="gsd-planner",
            agent_role="proposer",
            phase="01",
        )
        assert result == Priority.CRITICAL

    def test_executor_is_normal(self) -> None:
        """Executor agent type infers NORMAL priority."""
        result = infer_priority(
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
        )
        assert result == Priority.NORMAL

    def test_verification_is_low(self) -> None:
        """Phase containing 'verify' infers LOW priority."""
        result = infer_priority(
            agent_type="gsd-verifier",
            agent_role="proposer",
            phase="05-verify",
        )
        assert result == Priority.LOW

    def test_default_is_normal(self) -> None:
        """Unknown agent type defaults to NORMAL priority."""
        result = infer_priority(
            agent_type="unknown-agent",
            agent_role="proposer",
            phase="03",
        )
        assert result == Priority.NORMAL

    def test_planner_takes_precedence_over_verify_phase(self) -> None:
        """Planner agent type overrides verify phase (Rule 1 before Rule 2)."""
        result = infer_priority(
            agent_type="gsd-planner",
            agent_role="proposer",
            phase="05-verify",
        )
        assert result == Priority.CRITICAL

    def test_optional_params_accepted(self) -> None:
        """Plan and task params are accepted without affecting result."""
        result = infer_priority(
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="01",
            plan="01",
            task="3",
        )
        assert result == Priority.NORMAL

    def test_case_insensitive_planner(self) -> None:
        """Planner check is case-insensitive."""
        result = infer_priority(
            agent_type="GSD-Planner",
            agent_role="proposer",
            phase="01",
        )
        assert result == Priority.CRITICAL

    def test_case_insensitive_verify(self) -> None:
        """Verify phase check is case-insensitive."""
        result = infer_priority(
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="05-Verify-all",
        )
        assert result == Priority.LOW
