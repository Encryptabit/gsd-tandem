"""Spawn/reviewer pool configuration schema."""

from __future__ import annotations

import json
import os
from pathlib import Path

from pydantic import BaseModel, Field, field_validator

ALLOWED_MODELS: set[str] = {
    "o4-mini",
    "o3",
    "codex-mini-latest",
    "gpt-5",
    "gpt-5-codex",
    "gpt-5.3-codex",
}


class SpawnConfig(BaseModel):
    """Validated reviewer pool spawn/runtime configuration."""

    model: str = Field(default="o4-mini")
    reasoning_effort: str = Field(default="high")
    workspace_path: str
    wsl_distro: str = Field(default="Ubuntu")
    max_pool_size: int = Field(default=3, ge=1, le=10)
    idle_timeout_seconds: float = Field(default=300.0, ge=60.0)
    max_ttl_seconds: float = Field(default=3600.0, ge=300.0)
    claim_timeout_seconds: float = Field(default=1200.0, ge=60.0)
    spawn_cooldown_seconds: float = Field(default=10.0, ge=1.0)
    prompt_template_path: str = Field(default="reviewer_prompt.md")
    scaling_ratio: float = Field(default=3.0, ge=1.0)
    background_check_interval_seconds: float = Field(default=30.0, ge=5.0)

    @field_validator("model")
    @classmethod
    def _validate_model(cls, value: str) -> str:
        if value not in ALLOWED_MODELS:
            allowed = ", ".join(sorted(ALLOWED_MODELS))
            raise ValueError(f"Unsupported model: {value!r}. Allowed: {allowed}")
        return value

    @field_validator("reasoning_effort")
    @classmethod
    def _validate_reasoning_effort(cls, value: str) -> str:
        allowed = {"low", "medium", "high"}
        if value not in allowed:
            raise ValueError(f"reasoning_effort must be one of {sorted(allowed)}")
        return value

    @field_validator("workspace_path")
    @classmethod
    def _validate_workspace_path(cls, value: str) -> str:
        # WSL-style paths are not resolvable from native Windows Python runtime.
        if os.name == "nt":
            return value
        if not Path(value).exists():
            raise ValueError(f"workspace_path does not exist: {value}")
        return value


def load_spawn_config(config_path: str | Path) -> SpawnConfig | None:
    """Load reviewer pool config from .planning/config.json.

    Returns:
    - None when the reviewer_pool section is missing (pool disabled).
    - SpawnConfig when reviewer_pool exists and validates.
    Raises:
    - FileNotFoundError if config file is missing.
    - pydantic ValidationError on invalid reviewer_pool values.
    - json.JSONDecodeError for malformed JSON.
    """

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(path)

    payload = json.loads(path.read_text(encoding="utf-8"))
    if "reviewer_pool" not in payload:
        return None

    section = payload.get("reviewer_pool")
    if section is None:
        return None
    if not isinstance(section, dict):
        raise ValueError("reviewer_pool must be an object when provided")
    return SpawnConfig.model_validate(section)
