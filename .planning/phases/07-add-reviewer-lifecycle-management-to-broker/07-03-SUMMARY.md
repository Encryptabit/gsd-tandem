---
phase: 07-add-reviewer-lifecycle-management-to-broker
plan: 03
subsystem: api
tags: [fenced-reclaim, claim-generation, authorization, stale-verdict]

# Dependency graph
requires:
  - phase: 07-add-reviewer-lifecycle-management-to-broker
    provides: reviewer schema, claim token columns, reviewer pool base
provides:
  - claim_review fence-token increment + claimed_at write
  - reclaim_review transition claimed -> pending with fence increment
  - submit_verdict stale-claim rejection and claimed_by authorization
  - drain finalization after terminal verdict or reclaim
affects: [07-04]

# Tech tracking
tech-stack:
  added: []
  patterns: [fence-token optimistic concurrency, managed-vs-legacy compatibility, reclaim-safe verdicting]

key-files:
  created:
    - tools/gsd-review-broker/tests/test_reclaim.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/tools.py
    - tools/gsd-review-broker/tests/test_tools.py
    - tools/gsd-review-broker/tests/test_proposals.py

key-decisions:
  - "Strict claimed-review fencing is enforced for broker-managed reviewers while preserving manual legacy compatibility"
  - "submit_verdict accepts reviewer_id and claim_generation to resolve stale-claim races"
  - "Draining reviewers auto-terminate only when their final claimed review is resolved"

patterns-established:
  - "claim_generation increments on claim and reclaim operations"
  - "Managed-claim authorization requires reviewer_id to match claimed_by"

requirements-completed: [RLMC-03]

# Metrics
duration: 31min
completed: 2026-02-18
---

# Phase 7 Plan 03 Summary

**Fenced reclaim and managed reviewer authorization were added to prevent stale/foreign verdict submissions under timeout reclaim races.**

## Accomplishments
- Added `reclaim_review` and drain-finalization helpers to `tools.py`.
- Updated `claim_review` to persist `claimed_at` and increment/return `claim_generation`.
- Updated `submit_verdict` to enforce stale claim checks and claimed reviewer authorization for managed claims.
- Added extensive reclaim/race-condition tests and updated existing proposal/tool tests for new verdict call semantics.

## Issues Encountered
None.

## Self-Check: PASSED
