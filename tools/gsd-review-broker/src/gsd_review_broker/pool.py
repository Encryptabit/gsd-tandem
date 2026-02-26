"""Reviewer subprocess pool management."""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import os
import re
import sys
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

import aiosqlite

from gsd_review_broker.audit import record_event
from gsd_review_broker.config_schema import SpawnConfig
from gsd_review_broker.platform_spawn import build_codex_argv, load_prompt_template

logger = logging.getLogger("gsd_review_broker")
USER_CONFIG_DIRNAME = "gsd-review-broker"
PROMPT_PATH_ENV_VAR = "BROKER_PROMPT_TEMPLATE_PATH"
REVIEWER_LOG_DIR_ENV_VAR = "BROKER_REVIEWER_LOG_DIR"
REVIEWER_LOG_MAX_BYTES_ENV_VAR = "BROKER_REVIEWER_LOG_MAX_BYTES"
REVIEWER_LOG_BACKUPS_ENV_VAR = "BROKER_REVIEWER_LOG_BACKUPS"
DEFAULT_REVIEWER_LOG_MAX_BYTES = 5 * 1024 * 1024
DEFAULT_REVIEWER_LOG_BACKUPS = 5


async def _rollback_quietly(db: aiosqlite.Connection) -> None:
    with contextlib.suppress(Exception):
        await db.execute("ROLLBACK")


def _default_user_config_dir() -> Path:
    """Resolve a cross-platform user config directory for global prompt templates."""
    xdg_config_home = os.environ.get("XDG_CONFIG_HOME")
    if xdg_config_home:
        return Path(xdg_config_home).expanduser() / USER_CONFIG_DIRNAME

    if os.name == "nt":
        appdata = os.environ.get("APPDATA")
        if appdata:
            return Path(appdata).expanduser() / USER_CONFIG_DIRNAME
        return Path.home() / "AppData" / "Roaming" / USER_CONFIG_DIRNAME

    if sys.platform == "darwin":
        return Path.home() / "Library" / "Application Support" / USER_CONFIG_DIRNAME

    return Path.home() / ".config" / USER_CONFIG_DIRNAME


def _utc_timestamp() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds").replace(
        "+00:00",
        "Z",
    )


def _read_positive_int_env(name: str, default: int, minimum: int) -> int:
    raw = os.environ.get(name)
    if raw is None:
        return default
    try:
        value = int(raw)
    except ValueError:
        logger.warning("Invalid %s=%r; using default %s", name, raw, default)
        return default
    if value < minimum:
        logger.warning(
            "Invalid %s=%r; value must be >= %s (using default %s)",
            name,
            raw,
            minimum,
            default,
        )
        return default
    return value


class _JsonlRotatingWriter:
    """Append-only JSONL writer with size-based rotation."""

    def __init__(self, path: Path, *, max_bytes: int, backups: int) -> None:
        self.path = path
        self.max_bytes = max_bytes
        self.backups = backups
        self._file = None
        self._lock = asyncio.Lock()

    def _ensure_open(self) -> None:
        if self._file is not None and not self._file.closed:
            return
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._file = self.path.open("a", encoding="utf-8")

    def _rotated_path(self, index: int) -> Path:
        if index == 0:
            return self.path
        return Path(f"{self.path}.{index}")

    def _rotate(self) -> None:
        if self._file is not None and not self._file.closed:
            self._file.close()
        oldest = self._rotated_path(self.backups)
        if oldest.exists():
            oldest.unlink()
        for index in range(self.backups - 1, -1, -1):
            src = self._rotated_path(index)
            if not src.exists():
                continue
            dst = self._rotated_path(index + 1)
            src.replace(dst)
        self._file = None
        self._ensure_open()

    async def write_record(self, payload: dict[str, object]) -> None:
        line = json.dumps(payload, separators=(",", ":")) + "\n"
        encoded_len = len(line.encode("utf-8"))
        async with self._lock:
            self._ensure_open()
            assert self._file is not None
            self._file.seek(0, os.SEEK_END)
            if self._file.tell() + encoded_len > self.max_bytes:
                self._rotate()
            self._file.write(line)
            self._file.flush()

    async def close(self) -> None:
        async with self._lock:
            if self._file is not None and not self._file.closed:
                self._file.close()
            self._file = None


@dataclass
class ReviewerPool:
    """In-memory reviewer subprocess registry with DB persistence hooks."""

    session_token: str
    config: SpawnConfig
    _counter: int = 0
    _processes: dict[str, asyncio.subprocess.Process] = field(default_factory=dict)
    _draining: set[str] = field(default_factory=set)
    _log_writers: dict[str, _JsonlRotatingWriter] = field(default_factory=dict)
    _stream_tasks: dict[str, list[asyncio.Task[None]]] = field(default_factory=dict)
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
        path = Path(self.config.prompt_template_path).expanduser()
        if path.is_absolute():
            return path

        forced_path = os.environ.get(PROMPT_PATH_ENV_VAR)
        if forced_path:
            forced_candidate = Path(forced_path).expanduser()
            if forced_candidate.exists():
                return forced_candidate

        user_config_dir = _default_user_config_dir()
        candidates = [
            Path(self.config.workspace_path) / path,
            Path(self.config.workspace_path) / "tools" / "gsd-review-broker" / path,
            user_config_dir / path,
            Path.cwd() / path,
        ]
        for candidate in candidates:
            if candidate.exists():
                return candidate
        if forced_path:
            return Path(forced_path).expanduser()
        return candidates[0]

    def _resolve_reviewer_log_dir(self) -> Path:
        override = os.environ.get(REVIEWER_LOG_DIR_ENV_VAR)
        if override:
            return Path(override).expanduser()
        return _default_user_config_dir() / "reviewer-logs"

    def _reviewer_log_path(self, reviewer_id: str) -> Path:
        safe_reviewer_id = re.sub(r"[^A-Za-z0-9._-]", "_", reviewer_id)
        return self._resolve_reviewer_log_dir() / f"reviewer-{safe_reviewer_id}.jsonl"

    async def _write_reviewer_log(
        self,
        reviewer_id: str,
        *,
        event: str,
        stream: str | None = None,
        message: str | None = None,
        pid: int | None = None,
        exit_code: int | None = None,
    ) -> None:
        writer = self._log_writers.get(reviewer_id)
        if writer is None:
            return
        record: dict[str, object] = {
            "ts": _utc_timestamp(),
            "event": event,
            "reviewer_id": reviewer_id,
            "session_token": self.session_token,
        }
        if stream is not None:
            record["stream"] = stream
        if message is not None:
            record["message"] = message
        if pid is not None:
            record["pid"] = pid
        if exit_code is not None:
            record["exit_code"] = exit_code
        try:
            await writer.write_record(record)
        except Exception:
            logger.exception("Failed writing reviewer log record: reviewer_id=%s", reviewer_id)

    async def _drain_reviewer_stream(
        self,
        reviewer_id: str,
        pid: int | None,
        stream_name: str,
        stream: asyncio.StreamReader,
    ) -> None:
        try:
            while True:
                line = await stream.readline()
                if not line:
                    break
                message = line.decode("utf-8", errors="replace").rstrip("\r\n")
                await self._write_reviewer_log(
                    reviewer_id,
                    event="reviewer_output",
                    stream=stream_name,
                    message=message,
                    pid=pid,
                )
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.exception(
                "reviewer stream drain failed: reviewer_id=%s stream=%s",
                reviewer_id,
                stream_name,
            )

    async def _cleanup_reviewer_logging(
        self,
        reviewer_id: str,
        *,
        cancel_tasks: bool,
        close_writer: bool = True,
    ) -> None:
        tasks = self._stream_tasks.pop(reviewer_id, [])
        if cancel_tasks:
            for task in tasks:
                task.cancel()
        if tasks:
            with contextlib.suppress(Exception):
                await asyncio.gather(*tasks, return_exceptions=True)
        if close_writer:
            writer = self._log_writers.pop(reviewer_id, None)
            if writer is not None:
                with contextlib.suppress(Exception):
                    await writer.close()

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
            logger.info(
                "pool.spawn_reviewer -> blocked by cap (active=%s max=%s)",
                self.active_count,
                self.config.max_pool_size,
            )
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
        log_max_bytes = _read_positive_int_env(
            REVIEWER_LOG_MAX_BYTES_ENV_VAR,
            DEFAULT_REVIEWER_LOG_MAX_BYTES,
            1024,
        )
        log_backups = _read_positive_int_env(
            REVIEWER_LOG_BACKUPS_ENV_VAR,
            DEFAULT_REVIEWER_LOG_BACKUPS,
            1,
        )
        self._log_writers[reviewer_id] = _JsonlRotatingWriter(
            self._reviewer_log_path(reviewer_id),
            max_bytes=log_max_bytes,
            backups=log_backups,
        )

        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *argv,
                stdin=asyncio.subprocess.PIPE,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stream_tasks: list[asyncio.Task[None]] = []
            if process.stdout is not None:
                stream_tasks.append(
                    asyncio.create_task(
                        self._drain_reviewer_stream(
                            reviewer_id,
                            process.pid,
                            "stdout",
                            process.stdout,
                        )
                    )
                )
            if process.stderr is not None:
                stream_tasks.append(
                    asyncio.create_task(
                        self._drain_reviewer_stream(
                            reviewer_id,
                            process.pid,
                            "stderr",
                            process.stderr,
                        )
                    )
                )
            self._stream_tasks[reviewer_id] = stream_tasks
            if process.stdin is not None:
                process.stdin.write(prompt.encode("utf-8"))
                await process.stdin.drain()
                process.stdin.close()
            self._processes[reviewer_id] = process
            self._last_spawn_time = time.monotonic()
            await self._write_reviewer_log(
                reviewer_id,
                event="reviewer_spawned",
                pid=process.pid,
            )

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
            await self._write_reviewer_log(
                reviewer_id,
                event="reviewer_spawn_failed",
                pid=process.pid if process is not None else None,
                message=str(exc),
            )
            await self._cleanup_reviewer_logging(reviewer_id, cancel_tasks=True)
            self._processes.pop(reviewer_id, None)
            logger.warning("pool.spawn_reviewer -> failed reviewer_id=%s err=%s", reviewer_id, exc)
            return {"error": f"Failed to spawn reviewer: {exc}"}

        logger.info(
            "pool.spawn_reviewer -> success reviewer_id=%s pid=%s",
            reviewer_id,
            process.pid if process is not None else None,
        )
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
        """Mark reviewer as draining and terminate when no open attachments remain."""
        self._draining.add(reviewer_id)
        remaining_open_reviews = 0
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
                    """SELECT COUNT(*) AS n
                       FROM reviews
                       WHERE status != 'closed' AND claimed_by = ?""",
                    (reviewer_id,),
                )
                row = await cursor.fetchone()
                remaining_open_reviews = int(row["n"]) if row is not None else 0
                await db.execute("COMMIT")
            except Exception as exc:
                await _rollback_quietly(db)
                return {"error": f"Failed to drain reviewer: {exc}"}

        terminated = False
        if remaining_open_reviews == 0:
            await self._terminate_reviewer(reviewer_id, db, write_lock)
            terminated = True
        return {
            "reviewer_id": reviewer_id,
            "status": "draining",
            # Backward-compatible key retained for existing clients.
            "remaining_claims": remaining_open_reviews,
            "remaining_open_reviews": remaining_open_reviews,
            "terminated": terminated,
        }

    async def mark_dead_process_draining(
        self,
        reviewer_id: str,
        db: aiosqlite.Connection,
        write_lock: asyncio.Lock,
        *,
        exit_code: int | None,
        open_reviews: int,
    ) -> None:
        """Detach exited subprocess and keep reviewer draining while open reviews remain."""
        proc = self._processes.pop(reviewer_id, None)
        self._draining.add(reviewer_id)
        await self._cleanup_reviewer_logging(
            reviewer_id,
            cancel_tasks=False,
            close_writer=False,
        )
        await self._write_reviewer_log(
            reviewer_id,
            event="reviewer_process_exited",
            pid=proc.pid if proc is not None else None,
            exit_code=exit_code,
            message=f"open_reviews={open_reviews}",
        )

        async with write_lock:
            try:
                await db.execute("BEGIN IMMEDIATE")
                cursor = await db.execute(
                    "SELECT status FROM reviewers WHERE id = ?",
                    (reviewer_id,),
                )
                row = await cursor.fetchone()
                old_status = row["status"] if row is not None else None
                await db.execute(
                    """UPDATE reviewers
                       SET status = 'draining', last_active_at = datetime('now')
                       WHERE id = ? AND status != 'terminated'""",
                    (reviewer_id,),
                )
                await record_event(
                    db,
                    None,
                    "reviewer_drain_start",
                    actor="pool-manager",
                    old_status=old_status,
                    new_status="draining",
                    metadata={
                        "reviewer_id": reviewer_id,
                        "reason": "process_exited_with_open_reviews",
                        "exit_code": exit_code,
                        "open_reviews": open_reviews,
                    },
                )
                await db.execute("COMMIT")
            except Exception:
                await _rollback_quietly(db)

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
                except TimeoutError:
                    proc.kill()
                    await proc.wait()
            exit_code = proc.returncode

        self._processes.pop(reviewer_id, None)
        self._draining.discard(reviewer_id)
        await self._write_reviewer_log(
            reviewer_id,
            event="reviewer_terminated",
            pid=proc.pid if proc is not None else None,
            exit_code=exit_code,
        )
        await self._cleanup_reviewer_logging(reviewer_id, cancel_tasks=False)

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
                           rejections = rejections + CASE
                               WHEN ? = 'changes_requested' THEN 1 ELSE 0 END,
                           last_active_at = datetime('now')
                       WHERE id = ?""",
                    (review_duration_seconds, verdict, verdict, reviewer_id),
                )
                await db.execute("COMMIT")
            except Exception:
                await _rollback_quietly(db)
