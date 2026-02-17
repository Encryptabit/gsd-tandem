"""Proposal lifecycle tests for the GSD Review Broker.

Covers: proposal creation with description/diff, affected files extraction,
revision flow, claim with diff validation, get_proposal, and full lifecycle
including revision.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from gsd_review_broker.tools import (
    claim_review,
    close_review,
    create_review,
    get_proposal,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext

# --- Sample test data ---

SAMPLE_DIFF = """\
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 line one
-line two
+line TWO modified
 line three
"""

SAMPLE_MULTI_FILE_DIFF = """\
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+# New file
+def greet():
+    return "hello"
--- a/existing.py
+++ b/existing.py
@@ -1,3 +1,3 @@
 import os
-print("old")
+print("new")
 # end
"""

SAMPLE_DESCRIPTION = """\
## Summary
Refactor the greeting module to improve readability.

## Changes
- Updated hello.txt with corrected line formatting
- No breaking changes expected
"""


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


# ---- TestCreateReviewWithProposal ----


class TestCreateReviewWithProposal:
    async def test_create_review_with_description_and_diff(self, ctx: MockContext) -> None:
        """Creating a review with description and diff stores all fields."""
        result = await _create_review(
            ctx, description=SAMPLE_DESCRIPTION, diff=SAMPLE_DIFF
        )
        assert result["status"] == "pending"
        review_id = result["review_id"]

        cursor = await ctx.lifespan_context.db.execute(
            "SELECT description, diff, affected_files FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["description"] == SAMPLE_DESCRIPTION
        assert row["diff"] == SAMPLE_DIFF
        assert row["affected_files"] is not None
        files = json.loads(row["affected_files"])
        assert len(files) >= 1

    async def test_create_review_with_description_only(self, ctx: MockContext) -> None:
        """Creating a review with description but no diff stores description only."""
        result = await _create_review(ctx, description="Just a description, no diff")
        review_id = result["review_id"]

        cursor = await ctx.lifespan_context.db.execute(
            "SELECT description, diff, affected_files FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["description"] == "Just a description, no diff"
        assert row["diff"] is None
        assert row["affected_files"] is None

    async def test_create_review_extracts_affected_files(self, ctx: MockContext) -> None:
        """Multi-file diff produces correct affected_files JSON."""
        result = await _create_review(ctx, diff=SAMPLE_MULTI_FILE_DIFF)
        review_id = result["review_id"]

        cursor = await ctx.lifespan_context.db.execute(
            "SELECT affected_files FROM reviews WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        files = json.loads(row["affected_files"])
        assert len(files) == 2

        paths = {f["path"] for f in files}
        assert "new_file.py" in paths
        assert "existing.py" in paths

        # Check operations
        ops = {f["path"]: f["operation"] for f in files}
        assert ops["new_file.py"] == "create"
        assert ops["existing.py"] == "modify"


# ---- TestRevisionFlow ----


class TestRevisionFlow:
    async def test_revision_replaces_content(self, ctx: MockContext) -> None:
        """Revision replaces description/diff/affected_files and clears reviewer state."""
        # Create and push through to changes_requested
        created = await _create_review(
            ctx,
            intent="original intent",
            description="original desc",
            diff=SAMPLE_DIFF,
        )
        review_id = created["review_id"]
        await claim_review.fn(review_id=review_id, reviewer_id="reviewer-1", ctx=ctx)
        await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Needs work",
            ctx=ctx,
        )

        # Revise with new content
        revised = await create_review.fn(
            intent="revised intent",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            description="revised desc",
            diff=SAMPLE_MULTI_FILE_DIFF,
            review_id=review_id,
            ctx=ctx,
        )
        assert revised["status"] == "pending"
        assert revised["revised"] is True
        assert revised["review_id"] == review_id

        # Verify DB state
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT intent, description, diff, affected_files, status, "
            "claimed_by, verdict_reason FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["intent"] == "revised intent"
        assert row["description"] == "revised desc"
        assert row["diff"] == SAMPLE_MULTI_FILE_DIFF
        assert row["status"] == "pending"
        assert row["claimed_by"] is None
        assert row["verdict_reason"] is None
        # affected_files should reflect the new diff
        files = json.loads(row["affected_files"])
        assert len(files) == 2

    async def test_revision_from_wrong_state_fails(self, ctx: MockContext) -> None:
        """Revising a review that is not in changes_requested state fails."""
        created = await _create_review(ctx, intent="original")
        review_id = created["review_id"]
        # Review is in pending state -- not valid for revision (pending -> pending is invalid)
        result = await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            review_id=review_id,
            ctx=ctx,
        )
        assert "error" in result
        assert "Invalid transition" in result["error"]

    async def test_revision_not_found_fails(self, ctx: MockContext) -> None:
        """Revising a non-existent review returns error."""
        result = await create_review.fn(
            intent="revised",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            review_id="nonexistent-id",
            ctx=ctx,
        )
        assert "error" in result
        assert "not found" in result["error"]


# ---- TestClaimWithDiffValidation ----


class TestClaimWithDiffValidation:
    async def test_claim_review_without_diff_succeeds_normally(
        self, ctx: MockContext
    ) -> None:
        """Claiming a review without a diff works normally (no validation)."""
        created = await _create_review(ctx)
        result = await claim_review.fn(
            review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )
        assert result["status"] == "claimed"
        assert result["claimed_by"] == "reviewer-1"
        # No has_diff key when there's no diff
        assert "has_diff" not in result

    async def test_claim_review_returns_proposal_metadata(self, ctx: MockContext) -> None:
        """Claiming a review with description and diff returns metadata."""
        created = await _create_review(
            ctx,
            intent="update greeting",
            description=SAMPLE_DESCRIPTION,
            diff=SAMPLE_DIFF,
        )
        # Mock validate_diff to return success (no real git repo)
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await claim_review.fn(
                review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
            )

        assert result["status"] == "claimed"
        assert result["intent"] == "update greeting"
        assert result["description"] == SAMPLE_DESCRIPTION
        assert result["has_diff"] is True
        assert isinstance(result["affected_files"], list)
        assert len(result["affected_files"]) >= 1
        # Full diff text should NOT be in the claim response
        assert "diff" not in result or result.get("diff") is None

    async def test_claim_review_auto_rejects_bad_diff(self, ctx: MockContext) -> None:
        """Claiming a review with a diff that fails validation auto-rejects it."""
        created = await _create_review(
            ctx,
            diff=SAMPLE_DIFF,
        )
        error_msg = "error: hello.txt: No such file or directory"
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(False, error_msg),
        ):
            result = await claim_review.fn(
                review_id=created["review_id"], reviewer_id="reviewer-1", ctx=ctx
            )

        assert result["auto_rejected"] is True
        assert result["status"] == "changes_requested"
        assert result["validation_error"] == error_msg

        # Verify DB state
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT status, claimed_by, verdict_reason FROM reviews WHERE id = ?",
            (created["review_id"],),
        )
        row = await cursor.fetchone()
        assert row["status"] == "changes_requested"
        assert row["claimed_by"] == "broker-validator"
        assert "Auto-rejected" in row["verdict_reason"]
        assert error_msg in row["verdict_reason"]


# ---- TestGetProposal ----


class TestGetProposal:
    async def test_get_proposal_returns_content(self, ctx: MockContext) -> None:
        """get_proposal returns full proposal content including diff."""
        created = await _create_review(
            ctx,
            intent="update module",
            description=SAMPLE_DESCRIPTION,
            diff=SAMPLE_DIFF,
        )
        result = await get_proposal.fn(review_id=created["review_id"], ctx=ctx)

        assert result["id"] == created["review_id"]
        assert result["intent"] == "update module"
        assert result["description"] == SAMPLE_DESCRIPTION
        assert result["diff"] == SAMPLE_DIFF
        assert isinstance(result["affected_files"], list)
        assert len(result["affected_files"]) >= 1
        assert result["status"] == "pending"

    async def test_get_proposal_not_found(self, ctx: MockContext) -> None:
        """get_proposal with non-existent ID returns error."""
        result = await get_proposal.fn(review_id="nonexistent-id", ctx=ctx)
        assert "error" in result
        assert "not found" in result["error"]

    async def test_get_proposal_without_diff(self, ctx: MockContext) -> None:
        """get_proposal on review without diff returns None for diff fields."""
        created = await _create_review(ctx, intent="simple change")
        result = await get_proposal.fn(review_id=created["review_id"], ctx=ctx)

        assert result["id"] == created["review_id"]
        assert result["description"] is None
        assert result["diff"] is None
        assert result["affected_files"] is None


# ---- TestFullProposalLifecycle ----


class TestFullProposalLifecycle:
    async def test_proposal_lifecycle_submit_claim_approve_close(
        self, ctx: MockContext
    ) -> None:
        """Full lifecycle: create with proposal -> claim -> approve -> close."""
        # Create review with proposal content
        created = await _create_review(
            ctx,
            intent="add logging module",
            description="Add structured logging",
            diff=SAMPLE_DIFF,
        )
        review_id = created["review_id"]
        assert created["status"] == "pending"

        # Claim (mock diff validation)
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            claimed = await claim_review.fn(
                review_id=review_id, reviewer_id="reviewer-agent", ctx=ctx
            )
        assert claimed["status"] == "claimed"
        assert claimed["has_diff"] is True

        # Approve
        verdict = await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            reason="LGTM",
            ctx=ctx,
        )
        assert verdict["status"] == "approved"

        # Close
        closed = await close_review.fn(review_id=review_id, ctx=ctx)
        assert closed["status"] == "closed"

    async def test_proposal_lifecycle_submit_reject_revise_approve(
        self, ctx: MockContext
    ) -> None:
        """Full lifecycle: create -> claim -> reject -> revise -> re-claim -> approve -> close."""
        # Step 1: Create with initial diff
        created = await _create_review(
            ctx,
            intent="initial implementation",
            description="First attempt",
            diff=SAMPLE_DIFF,
        )
        review_id = created["review_id"]

        # Step 2: Claim (mock validation)
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            claimed = await claim_review.fn(
                review_id=review_id, reviewer_id="reviewer-1", ctx=ctx
            )
        assert claimed["status"] == "claimed"

        # Step 3: Request changes
        rejected = await submit_verdict.fn(
            review_id=review_id,
            verdict="changes_requested",
            reason="Missing error handling",
            ctx=ctx,
        )
        assert rejected["status"] == "changes_requested"

        # Step 4: Revise
        revised = await create_review.fn(
            intent="revised implementation",
            agent_type="gsd-executor",
            agent_role="proposer",
            phase="1",
            description="Second attempt with error handling",
            diff=SAMPLE_MULTI_FILE_DIFF,
            review_id=review_id,
            ctx=ctx,
        )
        assert revised["status"] == "pending"
        assert revised["revised"] is True

        # Verify revision cleared old state
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT claimed_by, verdict_reason FROM reviews WHERE id = ?",
            (review_id,),
        )
        row = await cursor.fetchone()
        assert row["claimed_by"] is None
        assert row["verdict_reason"] is None

        # Step 5: Re-claim
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            re_claimed = await claim_review.fn(
                review_id=review_id, reviewer_id="reviewer-2", ctx=ctx
            )
        assert re_claimed["status"] == "claimed"
        assert re_claimed["claimed_by"] == "reviewer-2"

        # Step 6: Approve
        approved = await submit_verdict.fn(
            review_id=review_id,
            verdict="approved",
            reason="Looks good now",
            ctx=ctx,
        )
        assert approved["status"] == "approved"

        # Step 7: Close
        closed = await close_review.fn(review_id=review_id, ctx=ctx)
        assert closed["status"] == "closed"

        # Verify get_proposal still works on closed review
        proposal = await get_proposal.fn(review_id=review_id, ctx=ctx)
        assert proposal["intent"] == "revised implementation"
        assert proposal["description"] == "Second attempt with error handling"
        assert proposal["diff"] == SAMPLE_MULTI_FILE_DIFF
