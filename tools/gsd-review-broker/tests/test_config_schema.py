"""Tests for reviewer pool spawn configuration schema."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from pydantic import ValidationError

from gsd_review_broker.config_schema import SpawnConfig, load_spawn_config


def test_default_config(tmp_path: Path) -> None:
    config = SpawnConfig(workspace_path=str(tmp_path))
    assert config.model == "o4-mini"
    assert config.reasoning_effort == "high"
    assert config.wsl_distro == "Ubuntu"
    assert config.max_pool_size == 3
    assert config.claim_timeout_seconds == 1200.0


def test_invalid_model_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), model="not-a-model")


def test_invalid_reasoning_effort_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), reasoning_effort="ultra")


def test_pool_size_bounds(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), max_pool_size=0)
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), max_pool_size=11)


def test_timeout_minimums(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), idle_timeout_seconds=30.0)
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), max_ttl_seconds=100.0)
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), claim_timeout_seconds=10.0)
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), spawn_cooldown_seconds=0.5)


def test_load_spawn_config_from_file(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(
        json.dumps(
            {
                "reviewer_pool": {
                    "workspace_path": str(tmp_path),
                    "model": "o4-mini",
                    "max_pool_size": 2,
                }
            }
        ),
        encoding="utf-8",
    )
    loaded = load_spawn_config(config_path)
    assert loaded is not None
    assert loaded.max_pool_size == 2
    assert loaded.workspace_path == str(tmp_path)


def test_load_spawn_config_missing_file(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_spawn_config(tmp_path / "missing.json")


def test_load_spawn_config_missing_key(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"mode": "yolo"}), encoding="utf-8")
    assert load_spawn_config(config_path) is None


def test_load_spawn_config_explicit_null_disables_pool(tmp_path: Path) -> None:
    config_path = tmp_path / "config.json"
    config_path.write_text(json.dumps({"reviewer_pool": None}), encoding="utf-8")
    assert load_spawn_config(config_path) is None


def test_shell_metacharacter_in_model_rejected(tmp_path: Path) -> None:
    with pytest.raises(ValidationError):
        SpawnConfig(workspace_path=str(tmp_path), model="; rm -rf /")


def test_workspace_path_nonexistent_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    from gsd_review_broker import config_schema

    monkeypatch.setattr(config_schema.os, "name", "posix", raising=False)
    with pytest.raises(ValidationError, match="does not exist"):
        SpawnConfig(workspace_path="/tmp/nonexistent_workspace_path_xyz123")


def test_workspace_path_exists_accepted(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from gsd_review_broker import config_schema

    monkeypatch.setattr(config_schema.os, "name", "posix", raising=False)
    config = SpawnConfig(workspace_path=str(tmp_path))
    assert config.workspace_path == str(tmp_path)


def test_workspace_path_skips_check_on_windows(monkeypatch: pytest.MonkeyPatch) -> None:
    from gsd_review_broker import config_schema

    monkeypatch.setattr(config_schema.os, "name", "nt", raising=False)
    config = SpawnConfig(workspace_path="/mnt/c/nonexistent")
    assert config.workspace_path == "/mnt/c/nonexistent"
