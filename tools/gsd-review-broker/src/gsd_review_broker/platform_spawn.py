"""Platform-aware subprocess argv building for reviewer workers."""

from __future__ import annotations

import os
import re
import shlex
from pathlib import Path

from gsd_review_broker.config_schema import SpawnConfig

CLAIM_GENERATION_NOTE = (
    "IMPORTANT: When submitting a verdict, always pass the claim_generation value "
    "you received from claim_review. This prevents stale verdict submissions after reclaim."
)


def detect_platform() -> str:
    """Return normalized platform label used for spawn strategy."""
    return "windows" if os.name == "nt" else "native"


def build_codex_argv(config: SpawnConfig) -> list[str]:
    """Build shell-free argv for reviewer subprocess invocation."""
    codex_args = [
        "codex",
        "exec",
        "--sandbox",
        "read-only",
        "--ephemeral",
        "--model",
        config.model,
        "-c",
        f"model_reasoning_effort={config.reasoning_effort}",
        "-C",
        config.workspace_path,
        "-",
    ]
    if detect_platform() == "windows":
        codex_cmd = " ".join(shlex.quote(arg) for arg in codex_args)
        # Match the manual reviewer launcher behavior: initialize nvm when present,
        # then exec codex in the same shell so Node-backed installs resolve.
        bash_cmd = (
            "if [ -s ~/.nvm/nvm.sh ]; then . ~/.nvm/nvm.sh; fi; "
            f"exec {codex_cmd}"
        )
        return ["wsl", "-d", config.wsl_distro, "--", "bash", "-lc", bash_cmd]
    return codex_args


def load_prompt_template(template_path: str | Path, reviewer_id: str) -> str:
    """Load reviewer prompt template and substitute all known placeholders."""
    raw = Path(template_path).read_text(encoding="utf-8")
    rendered = raw.replace("{reviewer_id}", reviewer_id)
    rendered = rendered.replace("{claim_generation_note}", CLAIM_GENERATION_NOTE)
    unresolved = re.search(r"\{[a-z_]+\}", rendered)
    if unresolved is not None:
        raise ValueError(f"Unresolved template placeholder: {unresolved.group(0)}")
    return rendered
