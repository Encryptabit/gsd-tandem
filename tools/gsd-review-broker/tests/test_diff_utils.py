"""Tests for diff utility functions."""

from __future__ import annotations

import json
import subprocess

import pytest

from gsd_review_broker.diff_utils import extract_affected_files, validate_diff

# -- Realistic unified diff test data --

MODIFY_DIFF = """\
--- a/hello.py
+++ b/hello.py
@@ -1,2 +1,2 @@
 def greet():
-    return "hello"
+    return "hello, world"
"""

CREATE_DIFF = """\
--- /dev/null
+++ b/newfile.py
@@ -0,0 +1,2 @@
+def new_func():
+    pass
"""

DELETE_DIFF = """\
--- a/oldfile.py
+++ /dev/null
@@ -1,2 +0,0 @@
-def old_func():
-    pass
"""

MULTI_FILE_DIFF = """\
--- a/alpha.py
+++ b/alpha.py
@@ -1 +1,2 @@
 x = 1
+y = 2
--- a/beta.py
+++ b/beta.py
@@ -1,2 +1 @@
 a = 10
-b = 20
"""


class TestExtractAffectedFiles:
    """Tests for extract_affected_files."""

    def test_single_file_modify(self) -> None:
        result = json.loads(extract_affected_files(MODIFY_DIFF))
        assert len(result) == 1
        entry = result[0]
        assert entry["path"] == "hello.py"
        assert entry["operation"] == "modify"
        assert entry["added"] > 0
        assert entry["removed"] > 0

    def test_file_creation(self) -> None:
        result = json.loads(extract_affected_files(CREATE_DIFF))
        assert len(result) == 1
        entry = result[0]
        assert entry["path"] == "newfile.py"
        assert entry["operation"] == "create"
        assert entry["added"] == 2
        assert entry["removed"] == 0

    def test_file_deletion(self) -> None:
        result = json.loads(extract_affected_files(DELETE_DIFF))
        assert len(result) == 1
        entry = result[0]
        assert entry["path"] == "oldfile.py"
        assert entry["operation"] == "delete"
        assert entry["added"] == 0
        assert entry["removed"] == 2

    def test_multi_file_diff(self) -> None:
        result = json.loads(extract_affected_files(MULTI_FILE_DIFF))
        assert len(result) == 2
        paths = {e["path"] for e in result}
        assert paths == {"alpha.py", "beta.py"}
        for entry in result:
            assert entry["operation"] == "modify"

    def test_empty_string_returns_empty_list(self) -> None:
        result = extract_affected_files("")
        assert result == "[]"

    def test_malformed_input_returns_empty_list(self) -> None:
        result = extract_affected_files("this is not a diff at all\nrandom garbage")
        assert result == "[]"


class TestValidateDiff:
    """Tests for validate_diff using real temporary git repos."""

    @pytest.fixture
    def git_repo(self, tmp_path):
        """Create a temporary git repository with a committed file."""
        repo = tmp_path / "repo"
        repo.mkdir()
        subprocess.run(["git", "init"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "config", "user.name", "Test User"],
            cwd=str(repo), check=True, capture_output=True,
        )
        subprocess.run(
            ["git", "config", "user.email", "test@example.com"],
            cwd=str(repo), check=True, capture_output=True,
        )
        # Create and commit a file
        hello = repo / "hello.py"
        hello.write_text('def greet():\n    return "hello"\n')
        subprocess.run(["git", "add", "hello.py"], cwd=str(repo), check=True, capture_output=True)
        subprocess.run(
            ["git", "commit", "-m", "initial"],
            cwd=str(repo), check=True, capture_output=True,
        )
        return repo

    async def test_valid_diff_applies(self, git_repo) -> None:
        diff = (
            '--- a/hello.py\n'
            '+++ b/hello.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' def greet():\n'
            '-    return "hello"\n'
            '+    return "hello, world"\n'
        )
        ok, err = await validate_diff(diff, cwd=str(git_repo))
        assert ok is True
        assert err == ""

    async def test_diff_references_nonexistent_file(self, git_repo) -> None:
        diff = (
            '--- a/nonexistent.py\n'
            '+++ b/nonexistent.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' x = 1\n'
            '-y = 2\n'
            '+y = 3\n'
        )
        ok, err = await validate_diff(diff, cwd=str(git_repo))
        assert ok is False
        assert err  # error message is non-empty

    async def test_diff_with_wrong_context(self, git_repo) -> None:
        diff = (
            '--- a/hello.py\n'
            '+++ b/hello.py\n'
            '@@ -1,2 +1,2 @@\n'
            ' WRONG CONTEXT LINE\n'
            '-    return "hello"\n'
            '+    return "hello, world"\n'
        )
        ok, err = await validate_diff(diff, cwd=str(git_repo))
        assert ok is False
        assert err  # error message is non-empty
