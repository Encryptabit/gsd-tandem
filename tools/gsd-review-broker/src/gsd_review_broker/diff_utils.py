"""Diff validation and analysis utilities for the GSD Review Broker."""

from __future__ import annotations

import asyncio
import json

from unidiff import PatchSet


async def validate_diff(diff_text: str, cwd: str | None = None) -> tuple[bool, str]:
    """Validate a unified diff against the working tree using git apply --check.

    Returns (True, "") if the diff applies cleanly, or (False, error_message) otherwise.
    """
    proc = await asyncio.create_subprocess_exec(
        "git", "apply", "--check",
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
    )
    _, stderr = await proc.communicate(input=diff_text.encode("utf-8"))
    if proc.returncode == 0:
        return (True, "")
    return (False, stderr.decode("utf-8", errors="replace").strip())


def extract_affected_files(diff_text: str) -> str:
    """Parse a unified diff and return JSON describing affected files.

    Each entry contains: path, operation (create/delete/modify), added, removed.
    Returns "[]" on parse failure.
    """
    try:
        patch = PatchSet(diff_text)
    except Exception:
        return "[]"

    files: list[dict[str, str | int]] = []
    for patched_file in patch:
        if patched_file.is_added_file:
            operation = "create"
        elif patched_file.is_removed_file:
            operation = "delete"
        else:
            operation = "modify"

        files.append({
            "path": patched_file.path,
            "operation": operation,
            "added": patched_file.added,
            "removed": patched_file.removed,
        })

    return json.dumps(files)
