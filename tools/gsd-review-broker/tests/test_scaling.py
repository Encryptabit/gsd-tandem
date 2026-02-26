"""Integration tests for scaling checks and reviewer pool tooling."""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import TYPE_CHECKING
from unittest.mock import AsyncMock

import pytest

from gsd_review_broker.config_schema import SpawnConfig
from gsd_review_broker.db import (
    _check_claim_timeouts,
    _check_dead_processes,
    _check_idle_timeouts,
    _check_reactive_scaling,
    _check_ttl_expiry,
    _startup_ownership_sweep,
    _startup_reactive_scale_check,
    _startup_terminate_stale_reviewers,
)
from gsd_review_broker.pool import ReviewerPool
from gsd_review_broker.tools import (
    _reactive_scale_check,
    add_message,
    claim_review,
    close_review,
    create_review,
    kill_reviewer,
    list_reviewers,
    reclaim_review,
    spawn_reviewer,
    submit_verdict,
)

if TYPE_CHECKING:
    from conftest import MockContext


class _FakeStdin:
    def __init__(self) -> None:
        self.closed = False

    def write(self, _data: bytes) -> None:
        return

    async def drain(self) -> None:
        return

    def close(self) -> None:
        self.closed = True


class _FakeStream:
    async def readline(self) -> bytes:
        return b""


class _FakeProcess:
    def __init__(self, pid: int = 7777) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = _FakeStdin()
        self.stdout = _FakeStream()
        self.stderr = _FakeStream()
        self.terminated = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


async def _create_review(ctx: MockContext, **overrides) -> dict:
    payload = {
        "intent": "scaling test review",
        "agent_type": "gsd-executor",
        "agent_role": "proposer",
        "phase": "7",
    }
    payload.update(overrides)
    return await create_review.fn(**payload, ctx=ctx)


def _spawn_config(tmp_path: Path) -> SpawnConfig:
    return SpawnConfig(
        workspace_path=str(tmp_path),
        prompt_template_path=str(tmp_path / "reviewer_prompt.md"),
        model="o4-mini",
        max_pool_size=3,
        scaling_ratio=3.0,
        idle_timeout_seconds=60.0,
        max_ttl_seconds=300.0,
        claim_timeout_seconds=60.0,
        spawn_cooldown_seconds=1.0,
        background_check_interval_seconds=5.0,
    )


async def _attach_pool(
    ctx: MockContext,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[ReviewerPool, AsyncMock]:
    prompt = tmp_path / "reviewer_prompt.md"
    prompt.write_text("Reviewer {reviewer_id}\n{claim_generation_note}\n", encoding="utf-8")
    pool = ReviewerPool(session_token="current-session", config=_spawn_config(tmp_path))
    ctx.lifespan_context.pool = pool

    spawn_mock = AsyncMock(return_value=_FakeProcess())
    monkeypatch.setattr("gsd_review_broker.pool.asyncio.create_subprocess_exec", spawn_mock)
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    return pool, spawn_mock


async def _insert_reviewer(
    ctx: MockContext,
    reviewer_id: str,
    *,
    session_token: str,
    status: str = "active",
) -> None:
    await ctx.lifespan_context.db.execute(
        """INSERT INTO reviewers (id, display_name, session_token, status)
           VALUES (?, ?, ?, ?)""",
        (reviewer_id, reviewer_id, session_token, status),
    )


async def test_spawn_reviewer_tool(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    result = await spawn_reviewer.fn(ctx=ctx)
    assert "error" not in result
    assert "reviewer_id" in result


async def test_spawn_reviewer_pool_not_configured(ctx: MockContext) -> None:
    ctx.lifespan_context.pool = None
    result = await spawn_reviewer.fn(ctx=ctx)
    assert "error" in result


async def test_kill_reviewer_tool(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    pool._last_spawn_time = 0.0
    killed = await kill_reviewer.fn(spawned["reviewer_id"], ctx=ctx)
    assert "error" not in killed
    assert killed["status"] == "draining"


async def test_kill_reviewer_unknown_id(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    await _attach_pool(ctx, tmp_path, monkeypatch)
    result = await kill_reviewer.fn("unknown", ctx=ctx)
    assert "error" in result


async def test_kill_reviewer_pool_not_configured(ctx: MockContext) -> None:
    ctx.lifespan_context.pool = None
    result = await kill_reviewer.fn("any", ctx=ctx)
    assert "error" in result


async def test_reactive_scaling_cold_start(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, spawn_mock = await _attach_pool(ctx, tmp_path, monkeypatch)
    real_create_task = asyncio.create_task
    scheduled: list[asyncio.Task] = []

    def _track_task(coro):  # noqa: ANN001
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("gsd_review_broker.tools.asyncio.create_task", _track_task)
    await _create_review(ctx, intent="cold-start")
    await asyncio.gather(*scheduled)
    assert spawn_mock.await_count >= 1
    assert pool.active_count >= 1


async def test_reactive_scaling_ratio(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, spawn_mock = await _attach_pool(ctx, tmp_path, monkeypatch)
    pool._processes["alive"] = _FakeProcess(pid=1001)
    for i in range(4):
        await _create_review(ctx, intent=f"pending-{i}")
    await _reactive_scale_check(ctx.lifespan_context)
    assert spawn_mock.await_count >= 1


async def test_reactive_scaling_sufficient_reviewers(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, spawn_mock = await _attach_pool(ctx, tmp_path, monkeypatch)
    pool._processes["alive"] = _FakeProcess(pid=1001)
    for i in range(2):
        await _create_review(ctx, intent=f"pending-{i}")
    spawn_mock.reset_mock()
    await _reactive_scale_check(ctx.lifespan_context)
    assert spawn_mock.await_count == 0


async def test_reactive_scaling_scopes_spawns_by_project(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned_projects: list[str | None] = []

    async def _fake_spawn(_db, _lock, *, project=None, ignore_cooldown=False):  # noqa: ANN001
        del ignore_cooldown
        spawned_projects.append(project)
        return {
            "reviewer_id": f"r-{len(spawned_projects)}",
            "pid": 1000 + len(spawned_projects),
            "status": "active",
            "project_scope": project,
        }

    pool.spawn_reviewer = AsyncMock(side_effect=_fake_spawn)  # type: ignore[method-assign]

    await ctx.lifespan_context.db.execute(
        """INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase, project)
           VALUES ('proj-a', 'pending', 'p1', 'gsd-executor', 'proposer', '7', 'code2obsidian')"""
    )
    await ctx.lifespan_context.db.execute(
        """INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase, project)
           VALUES ('proj-b', 'pending', 'p2', 'gsd-executor', 'proposer', '7', 'gsd-tandem')"""
    )
    await _reactive_scale_check(ctx.lifespan_context)

    assert len(spawned_projects) == 2
    assert set(spawned_projects) == {"code2obsidian", "gsd-tandem"}


async def test_proposer_followup_requeue_triggers_reactive_scaling(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, spawn_mock = await _attach_pool(ctx, tmp_path, monkeypatch)
    real_create_task = asyncio.create_task
    scheduled: list[asyncio.Task] = []

    def _track_task(coro):  # noqa: ANN001
        task = real_create_task(coro)
        scheduled.append(task)
        return task

    monkeypatch.setattr("gsd_review_broker.tools.asyncio.create_task", _track_task)

    created = await _create_review(ctx, intent="followup-scale")
    await asyncio.gather(*scheduled)
    scheduled.clear()
    spawn_mock.reset_mock()

    claim = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id="reviewer-a",
        ctx=ctx,
    )
    await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="changes_requested",
        reason="needs follow-up",
        reviewer_id="reviewer-a",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )

    pool._processes.clear()
    await add_message.fn(
        review_id=created["review_id"],
        sender_role="proposer",
        body="Can you clarify this blocker?",
        ctx=ctx,
    )
    await asyncio.gather(*scheduled)
    assert spawn_mock.await_count >= 1


async def test_stale_requeue_reservation_is_auto_cleared_on_claim(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    reviewer_id = spawned["reviewer_id"]

    created = await _create_review(ctx, intent="stale-reservation")
    claim = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id=reviewer_id,
        ctx=ctx,
    )
    await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="changes_requested",
        reason="needs clarification",
        reviewer_id=reviewer_id,
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    await add_message.fn(
        review_id=created["review_id"],
        sender_role="proposer",
        body="Can you clarify this?",
        ctx=ctx,
    )

    proc = pool._processes[reviewer_id]
    proc.returncode = 0
    fallback_claim = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id="fallback-reviewer",
        ctx=ctx,
    )
    assert fallback_claim["status"] == "claimed"
    assert fallback_claim["claimed_by"] == "fallback-reviewer"


async def test_background_reactive_scaling_pass(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, spawn_mock = await _attach_pool(ctx, tmp_path, monkeypatch)
    for i in range(5):
        await ctx.lifespan_context.db.execute(
            """INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase)
               VALUES (?, 'pending', ?, 'gsd-executor', 'proposer', '7')""",
            (f"bg-scale-{i}", f"bg-scale-{i}"),
        )
    assert pool.active_count == 0
    await _check_reactive_scaling(ctx.lifespan_context)
    assert spawn_mock.await_count >= 1
    assert pool.active_count >= 1


async def test_idle_timeout_triggers_drain(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET last_active_at = datetime('now', '-3600 seconds') WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    await _check_idle_timeouts(ctx.lifespan_context)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] in {"draining", "terminated"}


async def test_idle_timeout_skips_attached_active_reviewer(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    created = await _create_review(ctx, intent="idle-attached")
    await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id=spawned["reviewer_id"],
        ctx=ctx,
    )
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET last_active_at = datetime('now', '-3600 seconds') WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    await _check_idle_timeouts(ctx.lifespan_context)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] == "active"


async def test_ttl_expiry_triggers_drain(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET spawned_at = datetime('now', '-7200 seconds') WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    await _check_ttl_expiry(ctx.lifespan_context)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] in {"draining", "terminated"}


async def test_ttl_expiry_skips_attached_active_reviewer(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    created = await _create_review(ctx, intent="ttl-attached")
    await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id=spawned["reviewer_id"],
        ctx=ctx,
    )
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET spawned_at = datetime('now', '-7200 seconds') WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    await _check_ttl_expiry(ctx.lifespan_context)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (spawned["reviewer_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] == "active"


async def test_claim_timeout_triggers_reclaim(ctx: MockContext) -> None:
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="reviewer-a", ctx=ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviews SET claimed_at = datetime('now', '-3600 seconds') WHERE id = ?",
        (created["review_id"],),
    )
    ctx.lifespan_context.pool = ReviewerPool(
        session_token="s",
        config=SpawnConfig(workspace_path=".", model="o4-mini", prompt_template_path="x"),
    )
    await _check_claim_timeouts(ctx.lifespan_context)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] == "pending"


async def test_stale_session_recovery_on_startup(ctx: MockContext) -> None:
    ctx.lifespan_context.pool = ReviewerPool(
        session_token="current-session",
        config=SpawnConfig(workspace_path=".", model="o4-mini", prompt_template_path="x"),
    )
    await _insert_reviewer(ctx, "foreign-r1", session_token="foreign-session", status="active")
    created = await _create_review(ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviews SET status='claimed', claimed_by='foreign-r1' WHERE id = ?",
        (created["review_id"],),
    )
    terminated = await _startup_terminate_stale_reviewers(ctx.lifespan_context)
    reclaimed = await _startup_ownership_sweep(ctx.lifespan_context)
    assert terminated >= 1
    assert reclaimed >= 1


async def test_startup_reactive_scale_check_spawns_for_pending(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, spawn_mock = await _attach_pool(ctx, tmp_path, monkeypatch)
    for i in range(12):
        await ctx.lifespan_context.db.execute(
            """INSERT INTO reviews (id, status, intent, agent_type, agent_role, phase)
               VALUES (?, 'pending', ?, 'gsd-executor', 'proposer', '7')""",
            (f"startup-pending-{i}", f"startup backlog {i}"),
        )
    assert pool.active_count == 0
    await _startup_reactive_scale_check(ctx.lifespan_context)
    assert spawn_mock.await_count == pool.config.max_pool_size
    assert pool.active_count == pool.config.max_pool_size


async def test_list_reviewers_tool(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    listed = await list_reviewers.fn(ctx=ctx)
    ids = {reviewer["id"] for reviewer in listed["reviewers"]}
    assert spawned["reviewer_id"] in ids


async def test_dead_process_cleanup(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    dead = _FakeProcess(pid=9988)
    dead.returncode = 0
    reviewer_id = "dead-r1"
    pool._processes[reviewer_id] = dead
    await _insert_reviewer(ctx, reviewer_id, session_token=pool.session_token, status="active")
    await _check_dead_processes(ctx.lifespan_context)
    assert reviewer_id not in pool._processes
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (reviewer_id,),
    )
    row = await cursor.fetchone()
    assert row["status"] == "terminated"


async def test_dead_process_reclaims_claimed_review_then_terminates(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    reviewer_id = "dead-r2"
    await _insert_reviewer(ctx, reviewer_id, session_token=pool.session_token, status="active")
    created = await _create_review(ctx, intent="claimed-then-dead")
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id=reviewer_id, ctx=ctx)
    assert "error" not in claim

    dead = _FakeProcess(pid=9989)
    dead.returncode = 0
    pool._processes[reviewer_id] = dead
    await _check_dead_processes(ctx.lifespan_context)

    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, claimed_by FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    review_row = await cursor.fetchone()
    assert review_row["status"] == "pending"
    assert review_row["claimed_by"] is None

    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (reviewer_id,),
    )
    reviewer_row = await cursor.fetchone()
    assert reviewer_row["status"] == "terminated"
    assert reviewer_id not in pool._processes


async def test_dead_process_with_open_changes_requested_stays_draining(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    reviewer_id = "dead-r3"
    await _insert_reviewer(ctx, reviewer_id, session_token=pool.session_token, status="active")
    created = await _create_review(ctx, intent="changes-requested-then-dead")
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id=reviewer_id, ctx=ctx)
    assert "error" not in claim
    verdict = await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="changes_requested",
        reason="needs fix",
        reviewer_id=reviewer_id,
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    assert "error" not in verdict

    dead = _FakeProcess(pid=9990)
    dead.returncode = 0
    pool._processes[reviewer_id] = dead
    await _check_dead_processes(ctx.lifespan_context)

    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, claimed_by FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    review_row = await cursor.fetchone()
    assert review_row["status"] == "changes_requested"
    assert review_row["claimed_by"] == reviewer_id

    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviewers WHERE id = ?",
        (reviewer_id,),
    )
    reviewer_row = await cursor.fetchone()
    assert reviewer_row["status"] == "draining"
    assert reviewer_id not in pool._processes


async def test_reject_claim_for_draining_reviewer(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "r-draining", session_token="x", status="draining")
    created = await _create_review(ctx)
    result = await claim_review.fn(
        review_id=created["review_id"], reviewer_id="r-draining", ctx=ctx
    )
    assert "error" in result


async def test_reject_claim_for_terminated_reviewer(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "r-terminated", session_token="x", status="terminated")
    created = await _create_review(ctx)
    result = await claim_review.fn(
        review_id=created["review_id"], reviewer_id="r-terminated", ctx=ctx
    )
    assert "error" in result


async def test_terminate_draining_reviewer_after_terminal_verdict(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "r-a", session_token="x", status="active")
    created = await _create_review(ctx)
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="r-a", ctx=ctx)
    await ctx.lifespan_context.db.execute("UPDATE reviewers SET status='draining' WHERE id='r-a'")
    await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id="r-a",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    cursor = await ctx.lifespan_context.db.execute("SELECT status FROM reviewers WHERE id='r-a'")
    row = await cursor.fetchone()
    assert row["status"] == "draining"

    await close_review.fn(review_id=created["review_id"], closer_role="proposer", ctx=ctx)
    cursor = await ctx.lifespan_context.db.execute("SELECT status FROM reviewers WHERE id='r-a'")
    row = await cursor.fetchone()
    assert row["status"] == "terminated"


async def test_terminal_verdict_terminates_live_draining_process(
    ctx: MockContext, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool, _ = await _attach_pool(ctx, tmp_path, monkeypatch)
    spawned = await spawn_reviewer.fn(ctx=ctx)
    created = await _create_review(ctx)
    claim = await claim_review.fn(
        review_id=created["review_id"],
        reviewer_id=spawned["reviewer_id"],
        ctx=ctx,
    )
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET status='draining' WHERE id = ?",
        (spawned["reviewer_id"],),
    )

    proc = pool._processes[spawned["reviewer_id"]]
    await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="approved",
        reviewer_id=spawned["reviewer_id"],
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )

    assert proc.terminated is False
    assert spawned["reviewer_id"] in pool._processes

    await close_review.fn(review_id=created["review_id"], closer_role="proposer", ctx=ctx)
    assert proc.terminated is True
    assert spawned["reviewer_id"] not in pool._processes


async def test_terminate_draining_reviewer_after_reclaim(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "r-a", session_token="x", status="active")
    created = await _create_review(ctx)
    await claim_review.fn(review_id=created["review_id"], reviewer_id="r-a", ctx=ctx)
    await ctx.lifespan_context.db.execute("UPDATE reviewers SET status='draining' WHERE id='r-a'")
    await reclaim_review(created["review_id"], ctx.lifespan_context, reason="claim_timeout")
    cursor = await ctx.lifespan_context.db.execute("SELECT status FROM reviewers WHERE id='r-a'")
    row = await cursor.fetchone()
    assert row["status"] == "terminated"


async def test_revise_changes_requested_finalizes_draining_reviewer(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "r-drain", session_token="x", status="active")
    created = await _create_review(ctx, intent="needs revision")
    claim = await claim_review.fn(review_id=created["review_id"], reviewer_id="r-drain", ctx=ctx)
    await submit_verdict.fn(
        review_id=created["review_id"],
        verdict="changes_requested",
        reason="fix this",
        reviewer_id="r-drain",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    await ctx.lifespan_context.db.execute(
        "UPDATE reviewers SET status='draining' WHERE id='r-drain'",
    )

    revised = await create_review.fn(
        review_id=created["review_id"],
        intent="revised implementation",
        agent_type="gsd-executor",
        agent_role="proposer",
        phase="7",
        ctx=ctx,
    )
    assert revised.get("revised") is True

    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status, terminated_at FROM reviewers WHERE id='r-drain'",
    )
    row = await cursor.fetchone()
    assert row["status"] == "terminated"
    assert row["terminated_at"] is not None


async def test_no_termination_when_other_claims_remain(ctx: MockContext) -> None:
    await _insert_reviewer(ctx, "r-a", session_token="x", status="active")
    first = await _create_review(ctx, intent="one")
    second = await _create_review(ctx, intent="two")
    claim = await claim_review.fn(review_id=first["review_id"], reviewer_id="r-a", ctx=ctx)
    await claim_review.fn(review_id=second["review_id"], reviewer_id="r-a", ctx=ctx)
    await ctx.lifespan_context.db.execute("UPDATE reviewers SET status='draining' WHERE id='r-a'")
    await submit_verdict.fn(
        review_id=first["review_id"],
        verdict="approved",
        reviewer_id="r-a",
        claim_generation=claim["claim_generation"],
        ctx=ctx,
    )
    cursor = await ctx.lifespan_context.db.execute("SELECT status FROM reviewers WHERE id='r-a'")
    row = await cursor.fetchone()
    assert row["status"] == "draining"


async def test_startup_reclaims_claimed_review_with_missing_reviewer_row(ctx: MockContext) -> None:
    ctx.lifespan_context.pool = ReviewerPool(
        session_token="current-session",
        config=SpawnConfig(workspace_path=".", model="o4-mini", prompt_template_path="x"),
    )
    created = await _create_review(ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviews SET status='claimed', claimed_by='missing-row' WHERE id = ?",
        (created["review_id"],),
    )
    reclaimed = await _startup_ownership_sweep(ctx.lifespan_context)
    assert reclaimed == 1


async def test_startup_reclaims_claimed_review_from_foreign_session(ctx: MockContext) -> None:
    ctx.lifespan_context.pool = ReviewerPool(
        session_token="current-session",
        config=SpawnConfig(workspace_path=".", model="o4-mini", prompt_template_path="x"),
    )
    await _insert_reviewer(ctx, "foreign-r1", session_token="foreign", status="active")
    created = await _create_review(ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviews SET status='claimed', claimed_by='foreign-r1' WHERE id = ?",
        (created["review_id"],),
    )
    reclaimed = await _startup_ownership_sweep(ctx.lifespan_context)
    assert reclaimed == 1


async def test_startup_preserves_claimed_review_from_live_current_session_reviewer(
    ctx: MockContext,
) -> None:
    ctx.lifespan_context.pool = ReviewerPool(
        session_token="current-session",
        config=SpawnConfig(workspace_path=".", model="o4-mini", prompt_template_path="x"),
    )
    await _insert_reviewer(ctx, "live-r1", session_token="current-session", status="active")
    created = await _create_review(ctx)
    await ctx.lifespan_context.db.execute(
        "UPDATE reviews SET status='claimed', claimed_by='live-r1' WHERE id = ?",
        (created["review_id"],),
    )
    reclaimed = await _startup_ownership_sweep(ctx.lifespan_context)
    assert reclaimed == 0


async def test_claim_timeout_reclaim_with_null_claimed_at(ctx: MockContext) -> None:
    ctx.lifespan_context.pool = ReviewerPool(
        session_token="current-session",
        config=SpawnConfig(
            workspace_path=".",
            model="o4-mini",
            prompt_template_path="x",
            claim_timeout_seconds=60.0,
        ),
    )
    created = await _create_review(ctx)
    await ctx.lifespan_context.db.execute(
        """UPDATE reviews
           SET status='claimed',
               claimed_by='legacy-r1',
               claimed_at=NULL,
               updated_at=datetime('now', '-3600 seconds')
           WHERE id = ?""",
        (created["review_id"],),
    )
    await _check_claim_timeouts(ctx.lifespan_context)
    cursor = await ctx.lifespan_context.db.execute(
        "SELECT status FROM reviews WHERE id = ?",
        (created["review_id"],),
    )
    row = await cursor.fetchone()
    assert row["status"] == "pending"
