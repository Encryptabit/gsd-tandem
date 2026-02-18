"""Tests for platform-specific reviewer spawn command construction."""

from __future__ import annotations

from pathlib import Path

import pytest

from gsd_review_broker import config_schema, platform_spawn
from gsd_review_broker.config_schema import SpawnConfig
from gsd_review_broker.platform_spawn import build_codex_argv, detect_platform, load_prompt_template


def _config(tmp_path: Path, **overrides) -> SpawnConfig:
    base = {
        "workspace_path": str(tmp_path),
        "model": "o4-mini",
        "reasoning_effort": "high",
        "wsl_distro": "Ubuntu",
    }
    base.update(overrides)
    return SpawnConfig(**base)


def test_detect_platform_returns_string() -> None:
    platform = detect_platform()
    assert platform in {"windows", "native"}


def test_build_codex_argv_native(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(platform_spawn.os, "name", "posix", raising=False)
    monkeypatch.setattr(config_schema.os, "name", "posix", raising=False)
    config = _config(tmp_path)
    argv = build_codex_argv(config)
    assert argv[0] == "codex"
    assert "--sandbox" in argv
    assert "read-only" in argv
    assert "--ephemeral" in argv


def test_build_codex_argv_windows(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(platform_spawn.os, "name", "nt", raising=False)
    monkeypatch.setattr(config_schema.os, "name", "nt", raising=False)
    config = _config(tmp_path)
    argv = build_codex_argv(config)
    assert argv[:6] == ["wsl", "-d", "Ubuntu", "--", "bash", "-lc"]
    assert "nvm.sh" in argv[-1]
    assert "exec codex exec" in argv[-1]


def test_argv_is_list_of_strings(tmp_path: Path) -> None:
    argv = build_codex_argv(_config(tmp_path))
    assert all(isinstance(value, str) for value in argv)


def test_argv_contains_no_shell_metacharacters(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(platform_spawn.os, "name", "posix", raising=False)
    monkeypatch.setattr(config_schema.os, "name", "posix", raising=False)
    special_path = tmp_path / "workspace with spaces ; &&"
    special_path.mkdir()
    argv = build_codex_argv(_config(tmp_path, workspace_path=str(special_path)))
    assert str(special_path) in argv
    assert argv.count(str(special_path)) == 1


def test_load_prompt_template(tmp_path: Path) -> None:
    template = tmp_path / "reviewer_prompt.md"
    template.write_text(
        'You are "{reviewer_id}"\n{claim_generation_note}\n',
        encoding="utf-8",
    )
    loaded = load_prompt_template(template, "codex-r1-abc")
    assert "{reviewer_id}" not in loaded
    assert "{claim_generation_note}" not in loaded
    assert "codex-r1-abc" in loaded


def test_load_prompt_template_no_unresolved_placeholders(tmp_path: Path) -> None:
    template = tmp_path / "reviewer_prompt.md"
    template.write_text(
        "{reviewer_id}\n{claim_generation_note}\n{unknown_var}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="Unresolved template placeholder"):
        load_prompt_template(template, "codex-r1-abc")


def test_load_prompt_template_all_known_placeholders_resolved(tmp_path: Path) -> None:
    template = tmp_path / "reviewer_prompt.md"
    template.write_text("{reviewer_id}\n{claim_generation_note}\n", encoding="utf-8")
    loaded = load_prompt_template(template, "codex-r2-xyz")
    assert "{" not in loaded
    assert "}" not in loaded


def test_argv_dash_stdin_flag(tmp_path: Path) -> None:
    argv = build_codex_argv(_config(tmp_path))
    assert argv[-1] == "-"
