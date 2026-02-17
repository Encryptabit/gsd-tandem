"""Priority inference for reviews based on agent identity.

Priority is determined at review creation time and remains fixed for the
lifetime of the review. The inference rules are:

1. If the agent_type contains "planner" -> CRITICAL (planning changes affect all downstream work)
2. If the phase contains "verify" -> LOW (verification reviews are advisory)
3. Otherwise -> NORMAL (default for executor and other agent types)

Planner check takes precedence over phase check (Rule 1 before Rule 2).
"""

from __future__ import annotations

from gsd_review_broker.models import Priority


def infer_priority(
    agent_type: str,
    agent_role: str,
    phase: str,
    plan: str | None = None,
    task: str | None = None,
) -> Priority:
    """Infer review priority from agent identity fields.

    Args:
        agent_type: The type of agent submitting the review (e.g. "gsd-planner", "gsd-executor").
        agent_role: The role of the agent ("proposer" or "reviewer").
        phase: The phase identifier (e.g. "01", "05-verify").
        plan: Optional plan identifier.
        task: Optional task identifier.

    Returns:
        Priority enum value: CRITICAL, NORMAL, or LOW.
    """
    # Rule 1: Planner agent -> critical (highest precedence)
    if "planner" in agent_type.lower():
        return Priority.CRITICAL

    # Rule 2: Verification phase -> low
    if "verify" in phase.lower():
        return Priority.LOW

    # Rule 3: Default -> normal
    return Priority.NORMAL
