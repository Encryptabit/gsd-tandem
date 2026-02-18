---
phase: 07-add-reviewer-lifecycle-management-to-broker
plan: 02
subsystem: infra
tags: [reviewer-pool, subprocess, platform-spawn, lifecycle]

# Dependency graph
requires:
  - phase: 07-add-reviewer-lifecycle-management-to-broker
    provides: reviewer schema and spawn config foundation
provides:
  - shell-free platform-aware argv builder
  - reviewer prompt template loader with placeholder enforcement
  - ReviewerPool spawn/drain/terminate/shutdown lifecycle
  - reviewer subprocess orphan-protection on DB persistence failure
affects: [07-03, 07-04]

# Tech tracking
tech-stack:
  added: []
  patterns: [async subprocess lifecycle management, lock-scoped SQLite writes, DEVNULL process IO]

key-files:
  created:
    - tools/gsd-review-broker/src/gsd_review_broker/platform_spawn.py
    - tools/gsd-review-broker/src/gsd_review_broker/pool.py
    - tools/gsd-review-broker/tests/test_platform_spawn.py
    - tools/gsd-review-broker/tests/test_pool.py
  modified:
    - tools/gsd-review-broker/src/gsd_review_broker/audit.py
    - tools/gsd-review-broker/src/gsd_review_broker/db.py

key-decisions:
  - "Subprocess stdout/stderr are always DEVNULL to avoid long-running pipe deadlock"
  - "Spawn DB-write failures terminate the spawned subprocess to prevent orphan reviewers"
  - "Template placeholder substitution uses str.replace with unresolved-placeholder guard"

patterns-established:
  - "Spawn decisions enforce cooldown + max pool size before process launch"
  - "Reviewer lifecycle state is tracked both in-memory and in SQLite"

requirements-completed: [RLMC-01, RLMC-04]

# Metrics
duration: 24min
completed: 2026-02-18
---

# Phase 7 Plan 02 Summary

**ReviewerPool runtime was implemented with safe subprocess spawning, platform-specific argv construction, and lifecycle persistence.**

## Accomplishments
- Added `platform_spawn.py` for native/Windows command construction and strict prompt-template resolution.
- Added `pool.py` with spawn, drain, terminate, shutdown, active-count, and per-reviewer stats hooks.
- Integrated reviewer lifecycle events in persistence flow and upgraded audit helper to support lifecycle events.
- Added pool/platform unit tests including orphan cleanup, DEVNULL safety, and shell-free invocation constraints.

## Issues Encountered
None.

## Self-Check: PASSED
