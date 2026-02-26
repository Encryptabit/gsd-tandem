## Tandem Review

When `review.enabled=true` in `.planning/config.json` and the review broker is running:

All planned artifacts and code changes should be submitted through the broker before committing.
The discuss-phase, plan-phase, execute-phase, and verify-work orchestrators handle this automatically.

For ad-hoc changes (direct user requests outside GSD workflow):
1. Make changes with Edit/Write
2. Capture diff: `git diff HEAD`
3. Resolve project scope: `PROJECT_SCOPE=${GSD_REVIEW_PROJECT:-$(git rev-parse --show-toplevel 2>/dev/null || pwd)}`
4. Submit via `mcp__gsdreview__create_review` with `project=PROJECT_SCOPE` and `skip_diff_validation=true`
5. Wait for approval via `mcp__gsdreview__get_review_status(wait=true)`
6. On approval, commit. On rejection, address feedback and resubmit.
