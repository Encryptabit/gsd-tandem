"""Pydantic models and enums for the GSD Review Broker."""

from __future__ import annotations

import uuid
from enum import StrEnum

from pydantic import BaseModel, Field


class ReviewStatus(StrEnum):
    """Review lifecycle states per PROTO-01."""

    PENDING = "pending"
    CLAIMED = "claimed"
    IN_REVIEW = "in_review"
    APPROVED = "approved"
    CHANGES_REQUESTED = "changes_requested"
    CLOSED = "closed"


class Priority(StrEnum):
    """Review priority inferred from agent identity."""

    CRITICAL = "critical"
    NORMAL = "normal"
    LOW = "low"


class CounterPatchStatus(StrEnum):
    """Status of a counter-patch proposed by a reviewer."""

    PENDING = "pending"
    ACCEPTED = "accepted"
    REJECTED = "rejected"


class Category(StrEnum):
    """Review category for filtering and prioritization."""

    PLAN_REVIEW = "plan_review"
    CODE_CHANGE = "code_change"
    VERIFICATION = "verification"
    HANDOFF = "handoff"


class AgentIdentity(BaseModel):
    """Identity of an agent interacting with the broker."""

    agent_type: str = Field(description="e.g. 'gsd-executor', 'gsd-planner'")
    agent_role: str = Field(description="'proposer' or 'reviewer'")
    phase: str = Field(description="e.g. '1', '3.2'")
    plan: str | None = Field(default=None, description="Plan name, if applicable")
    task: str | None = Field(default=None, description="Task number, if applicable")


class Review(BaseModel):
    """A review record tracking the lifecycle of a proposed change."""

    id: str = Field(default_factory=lambda: str(uuid.uuid4()))
    status: ReviewStatus = ReviewStatus.PENDING
    intent: str
    description: str | None = None
    diff: str | None = None
    affected_files: str | None = None
    agent_type: str
    agent_role: str
    phase: str
    plan: str | None = None
    task: str | None = None
    claimed_by: str | None = None
    verdict_reason: str | None = None
    parent_id: str | None = None
    created_at: str | None = None
    updated_at: str | None = None
