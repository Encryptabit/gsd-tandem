"""Category support tests for the GSD Review Broker.

Covers: category creation, filtering, retrieval via get_review_status,
get_proposal, claim_review, and list_reviews.
"""

from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import AsyncMock, patch

from gsd_review_broker.tools import (
    claim_review,
    create_review,
    get_proposal,
    get_review_status,
    list_reviews,
)

if TYPE_CHECKING:
    from conftest import MockContext


SAMPLE_DIFF = """\
--- a/hello.txt
+++ b/hello.txt
@@ -1,3 +1,3 @@
 line one
-line two
+line TWO modified
 line three
"""


async def _create_with_category(
    ctx: MockContext,
    category: str | None = None,
    **overrides,
) -> dict:
    """Helper to create a review with optional category."""
    defaults = {
        "intent": "test change",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "4",
    }
    defaults.update(overrides)
    if category is not None:
        defaults["category"] = category
    return await create_review.fn(**defaults, ctx=ctx)


class TestCategoryCreation:
    async def test_create_review_with_category(self, ctx: MockContext) -> None:
        """Creating a review with category stores it in the database."""
        result = await _create_with_category(ctx, category="plan_review")
        assert "review_id" in result
        assert "error" not in result

        # Verify in database
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT category FROM reviews WHERE id = ?",
            (result["review_id"],),
        )
        row = await cursor.fetchone()
        assert row["category"] == "plan_review"

    async def test_create_review_without_category(self, ctx: MockContext) -> None:
        """Creating a review without category stores NULL."""
        result = await _create_with_category(ctx)
        assert "review_id" in result
        assert "error" not in result

        # Verify in database
        cursor = await ctx.lifespan_context.db.execute(
            "SELECT category FROM reviews WHERE id = ?",
            (result["review_id"],),
        )
        row = await cursor.fetchone()
        assert row["category"] is None


class TestCategoryFiltering:
    async def test_list_reviews_filter_by_category(self, ctx: MockContext) -> None:
        """list_reviews with category filter returns only matching reviews."""
        await _create_with_category(ctx, category="plan_review", intent="plan task")
        await _create_with_category(ctx, category="code_change", intent="code task")
        await _create_with_category(ctx, category="verification", intent="verify task")

        result = await list_reviews.fn(category="code_change", ctx=ctx)
        assert "error" not in result
        assert len(result["reviews"]) == 1
        assert result["reviews"][0]["intent"] == "code task"
        assert result["reviews"][0]["category"] == "code_change"

    async def test_list_reviews_filter_by_status_and_category(
        self, ctx: MockContext
    ) -> None:
        """list_reviews with both status and category filters returns intersection."""
        r1 = await _create_with_category(
            ctx, category="plan_review", intent="pending plan"
        )
        r2 = await _create_with_category(
            ctx, category="plan_review", intent="claimed plan"
        )
        # Claim the second review to change its status
        await claim_review.fn(
            review_id=r2["review_id"], reviewer_id="reviewer-1", ctx=ctx
        )

        # Filter by status=pending AND category=plan_review
        result = await list_reviews.fn(
            status="pending", category="plan_review", ctx=ctx
        )
        assert len(result["reviews"]) == 1
        assert result["reviews"][0]["intent"] == "pending plan"
        assert result["reviews"][0]["category"] == "plan_review"


class TestCategoryRetrieval:
    async def test_get_review_status_includes_category(
        self, ctx: MockContext
    ) -> None:
        """get_review_status response includes the category field."""
        created = await _create_with_category(ctx, category="plan_review")
        result = await get_review_status.fn(
            review_id=created["review_id"], ctx=ctx
        )
        assert "error" not in result
        assert result["category"] == "plan_review"

    async def test_get_proposal_includes_category(self, ctx: MockContext) -> None:
        """get_proposal response includes the category field."""
        created = await _create_with_category(ctx, category="code_change")
        result = await get_proposal.fn(review_id=created["review_id"], ctx=ctx)
        assert "error" not in result
        assert result["category"] == "code_change"

    async def test_claim_review_includes_category(self, ctx: MockContext) -> None:
        """claim_review response includes the category field."""
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            created = await _create_with_category(
                ctx, category="verification", diff=SAMPLE_DIFF
            )
            result = await claim_review.fn(
                review_id=created["review_id"],
                reviewer_id="reviewer-1",
                ctx=ctx,
            )
        assert "error" not in result
        assert result["status"] == "claimed"
        assert result["category"] == "verification"

    async def test_claim_review_auto_rejected_includes_category(
        self, ctx: MockContext
    ) -> None:
        """Auto-rejected claim responses still include category."""
        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(True, ""),
        ):
            created = await _create_with_category(
                ctx, category="code_change", diff=SAMPLE_DIFF
            )

        with patch(
            "gsd_review_broker.tools.validate_diff",
            new_callable=AsyncMock,
            return_value=(False, "diff no longer applies"),
        ):
            result = await claim_review.fn(
                review_id=created["review_id"],
                reviewer_id="reviewer-1",
                ctx=ctx,
            )

        assert "error" not in result
        assert result["status"] == "changes_requested"
        assert result["auto_rejected"] is True
        assert result["category"] == "code_change"

    async def test_list_reviews_includes_category_field(
        self, ctx: MockContext
    ) -> None:
        """list_reviews includes category in each review dict."""
        await _create_with_category(ctx, category="handoff")
        result = await list_reviews.fn(ctx=ctx)
        assert len(result["reviews"]) == 1
        assert result["reviews"][0]["category"] == "handoff"
