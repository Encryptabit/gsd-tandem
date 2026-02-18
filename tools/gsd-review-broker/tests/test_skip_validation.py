"""Tests for the skip_diff_validation feature on create_review and claim_review."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from gsd_review_broker.tools import claim_review, close_review, create_review, submit_verdict

SAMPLE_DIFF = """\
diff --git a/hello.txt b/hello.txt
new file mode 100644
--- /dev/null
+++ b/hello.txt
@@ -0,0 +1 @@
+hello world
"""


class TestCreateReviewSkipValidation:
    """create_review with skip_diff_validation=True stores diffs without git apply --check."""

    async def test_skip_validation_stores_diff_without_calling_validate(self, ctx) -> None:
        """When skip_diff_validation=True, validate_diff is NOT called."""
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock) as mock_vd:
            result = await create_review.fn(
                intent="post-commit diff",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=True,
                ctx=ctx,
            )
        assert "error" not in result
        assert "review_id" in result
        assert result["status"] == "pending"
        mock_vd.assert_not_called()

    async def test_skip_validation_still_extracts_affected_files(self, ctx) -> None:
        """Even with skip_diff_validation=True, affected_files are parsed from the diff."""
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock):
            result = await create_review.fn(
                intent="post-commit diff",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=True,
                ctx=ctx,
            )
        review_id = result["review_id"]
        # Check the stored affected_files
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT affected_files FROM reviews WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        assert row["affected_files"] is not None
        assert "hello.txt" in row["affected_files"]

    async def test_default_still_validates(self, ctx) -> None:
        """When skip_diff_validation is False (default), validate_diff IS called."""
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ) as mock_vd:
            result = await create_review.fn(
                intent="normal review",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                ctx=ctx,
            )
        assert "error" not in result
        mock_vd.assert_called_once()

    async def test_skip_validation_persists_flag(self, ctx) -> None:
        """The skip_diff_validation flag is stored in the reviews table."""
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock):
            result = await create_review.fn(
                intent="post-commit diff",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=True,
                ctx=ctx,
            )
        review_id = result["review_id"]
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT skip_diff_validation FROM reviews WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        assert row["skip_diff_validation"] == 1

    async def test_default_stores_zero_flag(self, ctx) -> None:
        """When skip_diff_validation is False (default), 0 is stored."""
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await create_review.fn(
                intent="normal review",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                ctx=ctx,
            )
        review_id = result["review_id"]
        db = ctx.lifespan_context.db
        cursor = await db.execute(
            "SELECT skip_diff_validation FROM reviews WHERE id = ?", (review_id,)
        )
        row = await cursor.fetchone()
        assert row["skip_diff_validation"] == 0


class TestClaimReviewSkipValidation:
    """claim_review respects the persisted skip_diff_validation flag."""

    async def _create_review_with_flag(self, ctx, skip: bool) -> str:
        """Helper: create a review with skip_diff_validation flag."""
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            result = await create_review.fn(
                intent="test review",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=skip,
                ctx=ctx,
            )
        return result["review_id"]

    async def test_claim_skips_validation_when_flag_true(self, ctx) -> None:
        """claim_review does NOT call validate_diff when skip_diff_validation=True."""
        review_id = await self._create_review_with_flag(ctx, skip=True)

        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock) as mock_vd:
            result = await claim_review.fn(
                review_id=review_id,
                reviewer_id="reviewer-1",
                ctx=ctx,
            )
        assert "error" not in result
        assert result["status"] == "claimed"
        mock_vd.assert_not_called()

    async def test_claim_validates_when_flag_false(self, ctx) -> None:
        """claim_review DOES call validate_diff when skip_diff_validation=False."""
        review_id = await self._create_review_with_flag(ctx, skip=False)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ) as mock_vd:
            result = await claim_review.fn(
                review_id=review_id,
                reviewer_id="reviewer-1",
                ctx=ctx,
            )
        assert "error" not in result
        assert result["status"] == "claimed"
        mock_vd.assert_called_once()

    async def test_claim_no_auto_reject_when_skip_validation_true(self, ctx) -> None:
        """Even if diff wouldn't apply, claim succeeds when skip_diff_validation=True."""
        review_id = await self._create_review_with_flag(ctx, skip=True)

        # validate_diff should not even be called, but if it were, it would fail
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(False, "patch does not apply"),
        ) as mock_vd:
            result = await claim_review.fn(
                review_id=review_id,
                reviewer_id="reviewer-1",
                ctx=ctx,
            )
        assert "error" not in result
        assert result["status"] == "claimed"
        assert result.get("auto_rejected") is None
        mock_vd.assert_not_called()

    async def test_claim_auto_rejects_when_flag_false_and_diff_invalid(self, ctx) -> None:
        """claim_review auto-rejects when skip_diff_validation=False and diff is stale."""
        review_id = await self._create_review_with_flag(ctx, skip=False)

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(False, "patch does not apply"),
        ):
            result = await claim_review.fn(
                review_id=review_id,
                reviewer_id="reviewer-1",
                ctx=ctx,
            )
        assert result["auto_rejected"] is True
        assert result["status"] == "changes_requested"


class TestRevisionWithSkipValidation:
    """Revision flow respects skip_diff_validation on the revision path."""

    async def test_revision_with_skip_validation(self, ctx) -> None:
        """Revising a review with skip_diff_validation=True skips validate_diff."""
        # Create -> claim -> reject -> revise with skip
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            create_result = await create_review.fn(
                intent="original",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                ctx=ctx,
            )
        review_id = create_result["review_id"]

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await claim_review.fn(review_id=review_id, reviewer_id="r1", ctx=ctx)

        await submit_verdict.fn(
            review_id=review_id, verdict="changes_requested", reason="needs work", ctx=ctx
        )

        # Revise with skip_diff_validation=True
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock) as mock_vd:
            result = await create_review.fn(
                review_id=review_id,
                intent="revised",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=True,
                ctx=ctx,
            )
        assert "error" not in result
        assert result["revised"] is True
        mock_vd.assert_not_called()

    async def test_claim_after_revision_respects_updated_flag_true(self, ctx) -> None:
        """Claim after revision with skip=True skips validate_diff."""
        # Create with skip=False -> claim -> reject -> revise with skip=True -> claim
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            create_result = await create_review.fn(
                intent="original",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=False,
                ctx=ctx,
            )
        review_id = create_result["review_id"]

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await claim_review.fn(review_id=review_id, reviewer_id="r1", ctx=ctx)

        await submit_verdict.fn(
            review_id=review_id, verdict="changes_requested", reason="needs work", ctx=ctx
        )

        # Revise with skip=True
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock):
            await create_review.fn(
                review_id=review_id,
                intent="revised",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=True,
                ctx=ctx,
            )

        # Claim should NOT call validate_diff (flag updated to True on revision)
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock) as mock_vd:
            result = await claim_review.fn(
                review_id=review_id, reviewer_id="r2", ctx=ctx
            )
        assert "error" not in result
        assert result["status"] == "claimed"
        mock_vd.assert_not_called()

    async def test_claim_after_revision_respects_updated_flag_false(self, ctx) -> None:
        """Claim after revision with skip=False calls validate_diff even if original had skip=True."""
        # Create with skip=True -> claim -> reject -> revise with skip=False -> claim
        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock):
            create_result = await create_review.fn(
                intent="original",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=True,
                ctx=ctx,
            )
        review_id = create_result["review_id"]

        with patch("gsd_review_broker.tools.validate_diff", new_callable=AsyncMock):
            await claim_review.fn(review_id=review_id, reviewer_id="r1", ctx=ctx)

        await submit_verdict.fn(
            review_id=review_id, verdict="changes_requested", reason="needs work", ctx=ctx
        )

        # Revise with skip=False
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            await create_review.fn(
                review_id=review_id,
                intent="revised",
                agent_type="gsd-executor",
                agent_role="proposer",
                phase="6",
                diff=SAMPLE_DIFF,
                skip_diff_validation=False,
                ctx=ctx,
            )

        # Claim SHOULD call validate_diff (flag updated to False on revision)
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ) as mock_vd:
            result = await claim_review.fn(
                review_id=review_id, reviewer_id="r2", ctx=ctx
            )
        assert "error" not in result
        assert result["status"] == "claimed"
        mock_vd.assert_called_once()
