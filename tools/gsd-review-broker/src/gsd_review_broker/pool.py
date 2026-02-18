"""Reviewer subprocess pool management."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from dataclasses import dataclass, field
from pathlib import Path

import aiosqlite

from gsd_review_broker.audit import record_event
from gsd_review_broker.config_schema import SpawnConfig
from gsd_review_broker.platform_spawn import build_codex_argv, load_prompt_template

logger = logging.getLogger("gsd_review_broker")


async def _rollback_quietly(db: aiosqlite.Connection) -> None:
    try:
        await db.execute("ROLLBACK")
    except Exception:
        pass


@dataclass
class ReviewerPool:
    """In-memory reviewer subprocess registry with DB persistence hooks."""

    session_token: str
    config: SpawnConfig
    _counter: int = 0
    _processes: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    _draining: set[str] = field(default_factory=set)
    _spawn_lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    _last_spawn_time: float = 0.0

    @property
    def active_count(self) -> int:
        """Count running, non-draining reviewer subprocesses."""
        return sum(
            1
            for reviewer_id, proc in self._processes.items()
            if proc.returncode is None and reviewer_id not in self._draining
        )

    def is_draining(self, reviewer_id: str) -> bool:
        """Return True when reviewer is in drain mode."""
        return reviewer_id in self._draining

    def _resolve_prompt_template_path(self) -> Path:
        path = Path(self.config.prompt_template_path)
        if path.is_absolute():
            return path
        candidates = [
            Path.cwd() / path,
            Path(self.config.workspace_path) / path,
            Path(self.config.workspace_path) / "tools" / "gsd-review-broker" / path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        return candidates[0]

    async def spawn_reviewer(
        self,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock,
        *,
        ignore_cooldown: bool = False,
    ) -> dict:
        """Spawn and persist a reviewer subprocess."""
        now = time.monotonic()
        elapsed = now - self._last_spawn_time
        if not ignore_cooldown and elapsed < self.config.spawn_cooldown_seconds:
            retry_after = round(self.config.spawn_cooldown_seconds - elapsed, 3)
            logger.info("pool.spawn_reviewer -> blocked by cooldown (retry_after=%ss)", retry_after)
            return {
                "error": "Spawn cooldown active",
                "retry_after_seconds": retry_after,
            }
        if self.active_count >= self.config.max_pool_size:
            logger.info("pool.spawn_reviewer -> blocked by cap (active=%s max=%s)", self.active_count, self.config.max_pool_size)
            return {
                "error": "Reviewer pool cap reached",
                "max_pool_size": self.config.max_pool_size,
            }

        self._counter += 1
        display_name = f"codex-r{self._counter}"
        reviewer_id = f"{display_name}-{self.session_token}"
        argv = build_codex_argv(self.config)
        prompt_path = self._resolve_prompt_template_path()
        prompt = load_prompt_template(prompt_path, reviewer_id)
        logger.info(
            "pool.spawn_reviewer -> attempting reviewer_id=%s distro=%s model=%s",
            reviewer_id,
            self.config.wsl_distro,
            self.config.model,
        )

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            if process.stdin is not None:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
            self._processes[reviewer_id] = process
            self._last_spawn_time = time.monotonic()

            async with write_lock:
                try:
                    await db.execute("BEGIN IMMEDIATE")
                    await db.execute(
                        """INSERT INTO reviewers (
                               id, display_name, session_token, status, pid,
                               spawned_at, last_active_at
                           ) VALUES (?, ?, ?, 'active', ?, datetime('now'), datetime('now'))""",
                        (reviewer_id, display_name, self.session_token, process.pid),
                    )
                    await record_event(
                        db,
                        None,
                        "reviewer_spawned",
                        actor="pool-manager",
                        new_status="active",
                        metadata={
                            "reviewer_id": reviewer_id,
                            "display_name": display_name,
                            "pid": process.pid,
                        },
                    )
                    await db.execute("COMMIT")
                except Exception:
                    await _rollback_quietly(db)
                    raise
        except Exception as exc:
            if process is not None and process.returncode is None:
                process.terminate()
                with contextlib.suppress(Exception):
                    await asyncio.wait_for(process.wait(), timeout=2.0)
            self._processes.pop(reviewer_id, None)
            logger.warning("pool.spawn_reviewer -> failed reviewer_id=%s err=%s", reviewer_id, exc)
            return {"error": f"Failed to spawn reviewer: {exc}"}

        logger.info("pool.spawn_reviewer -> success reviewer_id=%s pid=%s", reviewer_id, process.pid if process is not None else None)
        return {
            "reviewer_id": reviewer_id,
            "display_name": display_name,
            "pid": process.pid if process is not None else None,
            "status": "active",
        }

    async def drain_reviewer(
        self,
        reviewer_id: str,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock,
        reason: str = "manual",
    ) -> dict:
        """Mark reviewer as draining and terminate if no active claims remain."""
        self._draining.add(reviewer_id)
        remaining_claims = 0
        async with write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """UPDATE reviewers
                       SET status = 'draining', last_active_at = datetime('now')
                       WHERE id = ?""",
                    (reviewer_id,),
                )
                await record_event(
                    db,
                    None,
                    "reviewer_drain_start",
                    actor="pool-manager",
                    old_status="active",
                    new_status="draining",
                    metadata={"reviewer_id": reviewer_id, "reason": reason},
                )
                cursor = await db.execute(
                    "SELECT COUNT(*) AS n FROM reviews WHERE status = 'claimed' AND claimed_by = ?",
                    (reviewer_id,),
                )
                row = await cursor.fetchone()
                remaining_claims = int(row["n"]) if row is not None else 0
                await db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(db)
                return {"error": f"Failed to drain reviewer: {exc}"}

        terminated = False
        if remaining_claims == 0:
            await self._terminate_reviewer(reviewer_id, db, write_lock)
            terminated = True
        return {
            "reviewer_id": reviewer_id,
            "status": "draining",
            "remaining_claims": remaining_claims,
            "terminated": terminated,
        }

    async def _terminate_reviewer(
        self,
        reviewer_id: str,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock,
    ) -> None:
        """Terminate reviewer subprocess and persist lifecycle state."""
        proc = self._processes.get(reviewer_id)
        exit_code: int | None = None
        if proc is not None:
            if proc.returncode is None:
                proc.terminate()
                try:
                    await asyncio.wait_for(proc.wait(), timeout=10.0)
                except asyncio.TimeoutError:
                    proc.kill()
                    await proc.wait()
            exit_code = proc.returncode

        self._processes.pop(reviewer_id, None)
        self._draining.discard(reviewer_id)

        async with write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT reviews_completed FROM reviewers WHERE id = ?",
                    (reviewer_id,),
                )
                row = await cursor.fetchone()
                reviews_completed = int(row["reviews_completed"]) if row is not None else 0
                await db.execute(
                    """UPDATE reviewers
                       SET status = 'terminated', terminated_at = datetime('now')
                       WHERE id = ?""",
                    (reviewer_id,),
                )
                await record_event(
                    db,
                    None,
                    "reviewer_terminated",
                    actor="pool-manager",
                    old_status="draining",
                    new_status="terminated",
                    metadata={
                        "reviewer_id": reviewer_id,
                        "exit_code": exit_code,
                        "reviews_completed": reviews_completed,
                    },
                )
                await db.execute("COMMIT")
            except Exception:
                await _rollback_quietly(db)

    async def shutdown_all(
        self,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock,
    ) -> None:
        """Terminate all tracked reviewers."""
        for reviewer_id in list(self._processes.keys()):
            await self._terminate_reviewer(reviewer_id, db, write_lock)

    async def update_reviewer_stats(
        self,
        reviewer_id: str,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock,
        verdict: str,
        review_duration_seconds: float,
    ) -> None:
        """Increment reviewer performance counters."""
        async with write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                await db.execute(
                    """UPDATE reviewers
                       SET reviews_completed = reviews_completed + 1,
                           total_review_seconds = total_review_seconds + ?,
                           approvals = approvals + CASE WHEN ? = 'approved' THEN 1 ELSE 0 END,
                           rejections = rejections + CASE WHEN ? = 'changes_requested' THEN 1 ELSE 0 END,
                           last_active_at = datetime('now')
                       WHERE id = ?""",
                    (review_duration_seconds, verdict, verdict, reviewer_id),
                )
                await db.execute("COMMIT")
            except Exception:
                await _rollback_quietly(db)
