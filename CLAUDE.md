## Tandem Review

When `tandem_enabled=true` in `.planning/config.json` and the review broker is running:

All code changes should be submitted through the broker before committing.
The execute-phase orchestrator handles this automatically for planned work.

For ad-hoc changes (direct user requests outside GSD workflow):
1. Make changes with Edit/Write
2. Capture diff: `git diff HEAD`
3. Submit via `mcp__gsdreview__create_review` with `skip_diff_validation=true`
4. Wait for approval via `mcp__gsdreview__get_review_status(wait=true)`
5. On approval, commit. On rejection, address feedback and resubmit.
