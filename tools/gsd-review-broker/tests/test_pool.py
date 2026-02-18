"""Tests for ReviewerPool subprocess lifecycle management."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock

import aiosqlite
import pytest

from gsd_review_broker.config_schema import SpawnConfig
from gsd_review_broker.pool import ReviewerPool


class _FakeStdin:
    def __init__(self) -> None:
        self.writes: list[bytes] = []
        self.closed = False

    def write(self, data: bytes) -> None:
        self.writes.append(data)

    async def drain(self) -> None:
        return None

    def close(self) -> None:
        self.closed = True


class _FakeProcess:
    def __init__(self, pid: int = 4321) -> None:
        self.pid = pid
        self.returncode: int | None = None
        self.stdin = _FakeStdin()
        self.terminated = False
        self.killed = False

    def terminate(self) -> None:
        self.terminated = True
        self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    async def wait(self) -> int | None:
        return self.returncode


def _spawn_config(tmp_path: Path) -> SpawnConfig:
    return SpawnConfig(
        workspace_path=str(tmp_path),
        prompt_template_path=str(tmp_path / "reviewer_prompt.md"),
        spawn_cooldown_seconds=1.0,
        max_pool_size=3,
        model="o4-mini",
    )


@pytest.fixture
def pool(tmp_path: Path) -> ReviewerPool:
    prompt = tmp_path / "reviewer_prompt.md"
    prompt.write_text("Reviewer {reviewer_id}\n{claim_generation_note}\n", encoding="utf-8")
    return ReviewerPool(session_token="abcd1234", config=_spawn_config(tmp_path))


async def test_spawn_reviewer_creates_process(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = _FakeProcess()
    mock_spawn = AsyncMock(return_value=fake_proc)
    monkeypatch.setattr("gsd_review_broker.pool.asyncio.create_subprocess_exec", mock_spawn)
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: f"prompt:{reviewer_id}",
    )

    result = await pool.spawn_reviewer(db, asyncio.Lock())
    assert "error" not in result
    reviewer_id = result["reviewer_id"]
    assert reviewer_id in pool._processes

    cursor = await db.execute("SELECT id FROM reviewers WHERE id = ?", (reviewer_id,))
    assert await cursor.fetchone() is not None


async def test_spawn_reviewer_rate_limited(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool._last_spawn_time = asyncio.get_running_loop().time()
    monkeypatch.setattr("gsd_review_broker.pool.time.monotonic", lambda: pool._last_spawn_time)
    result = await pool.spawn_reviewer(db, asyncio.Lock())
    assert "error" in result
    assert "cooldown" in result["error"].lower()


async def test_spawn_reviewer_pool_cap(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    pool.config.max_pool_size = 1
    fake_proc = _FakeProcess()
    pool._processes["existing-reviewer"] = fake_proc
    result = await pool.spawn_reviewer(db, asyncio.Lock())
    assert "error" in result
    assert "cap" in result["error"].lower()


async def test_spawn_reviewer_ids_contain_session_token(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = _FakeProcess()
    monkeypatch.setattr(
        "gsd_review_broker.pool.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    result = await pool.spawn_reviewer(db, asyncio.Lock())
    assert result["reviewer_id"].endswith("-abcd1234")


async def test_drain_reviewer_marks_draining(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = _FakeProcess()
    monkeypatch.setattr(
        "gsd_review_broker.pool.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    spawned = await pool.spawn_reviewer(db, asyncio.Lock())
    reviewer_id = spawned["reviewer_id"]
    result = await pool.drain_reviewer(reviewer_id, db, asyncio.Lock(), reason="manual")
    assert result["status"] == "draining"


async def test_terminate_reviewer_kills_process(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = _FakeProcess()
    monkeypatch.setattr(
        "gsd_review_broker.pool.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    spawned = await pool.spawn_reviewer(db, asyncio.Lock())
    reviewer_id = spawned["reviewer_id"]
    await pool._terminate_reviewer(reviewer_id, db, asyncio.Lock())
    assert reviewer_id not in pool._processes
    assert fake_proc.terminated is True


async def test_shutdown_all(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    procs = [_FakeProcess(pid=1001), _FakeProcess(pid=1002)]
    mock_spawn = AsyncMock(side_effect=procs)
    monkeypatch.setattr("gsd_review_broker.pool.asyncio.create_subprocess_exec", mock_spawn)
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    await pool.spawn_reviewer(db, asyncio.Lock())
    # bypass cooldown for second spawn
    pool._last_spawn_time = 0.0
    await pool.spawn_reviewer(db, asyncio.Lock())
    await pool.shutdown_all(db, asyncio.Lock())
    assert pool._processes == {}
    assert all(proc.terminated for proc in procs)


async def test_spawn_records_audit_event(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gsd_review_broker.pool.asyncio.create_subprocess_exec",
        AsyncMock(return_value=_FakeProcess()),
    )
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    await pool.spawn_reviewer(db, asyncio.Lock())
    cursor = await db.execute(
        "SELECT event_type FROM audit_events WHERE event_type = 'reviewer_spawned'"
    )
    assert await cursor.fetchone() is not None


async def test_terminate_records_audit_event(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "gsd_review_broker.pool.asyncio.create_subprocess_exec",
        AsyncMock(return_value=_FakeProcess()),
    )
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    spawned = await pool.spawn_reviewer(db, asyncio.Lock())
    await pool._terminate_reviewer(spawned["reviewer_id"], db, asyncio.Lock())
    cursor = await db.execute(
        "SELECT event_type FROM audit_events WHERE event_type = 'reviewer_terminated'"
    )
    assert await cursor.fetchone() is not None


def test_active_count(pool: ReviewerPool) -> None:
    alive = _FakeProcess(pid=1)
    dead = _FakeProcess(pid=2)
    dead.returncode = 0
    pool._processes = {"a": alive, "b": dead}
    pool._draining = {"a"}
    assert pool.active_count == 0


async def test_spawn_no_shell_true(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_spawn = AsyncMock(return_value=_FakeProcess())
    monkeypatch.setattr("gsd_review_broker.pool.asyncio.create_subprocess_exec", mock_spawn)
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    await pool.spawn_reviewer(db, asyncio.Lock())
    _, kwargs = mock_spawn.call_args
    assert "shell" not in kwargs


async def test_spawn_uses_devnull_for_stdout_stderr(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    mock_spawn = AsyncMock(return_value=_FakeProcess())
    monkeypatch.setattr("gsd_review_broker.pool.asyncio.create_subprocess_exec", mock_spawn)
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )
    await pool.spawn_reviewer(db, asyncio.Lock())
    _, kwargs = mock_spawn.call_args
    assert kwargs["stdout"] is asyncio.subprocess.DEVNULL
    assert kwargs["stderr"] is asyncio.subprocess.DEVNULL


async def test_spawn_db_failure_terminates_orphan(
    pool: ReviewerPool, db: aiosqlite.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_proc = _FakeProcess()
    monkeypatch.setattr(
        "gsd_review_broker.pool.asyncio.create_subprocess_exec",
        AsyncMock(return_value=fake_proc),
    )
    monkeypatch.setattr("gsd_review_broker.pool.build_codex_argv", lambda _cfg: ["codex", "-"])
    monkeypatch.setattr(
        "gsd_review_broker.pool.load_prompt_template",
        lambda _path, reviewer_id: reviewer_id,
    )

    async def _explode(*args, **kwargs):  # noqa: ANN002, ANN003
        raise RuntimeError("boom")

    monkeypatch.setattr("gsd_review_broker.pool.record_event", _explode)
    result = await pool.spawn_reviewer(db, asyncio.Lock())
    assert "error" in result
    assert fake_proc.terminated is True
    assert pool._processes == {}
