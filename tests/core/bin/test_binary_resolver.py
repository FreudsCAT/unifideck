"""Tests for core/bin/binary_resolver.py (OP-07a)."""
from __future__ import annotations

from unifideck.core.binaries.binary_resolver import BinaryResolver, _is_executable
from unifideck.core.types.domain import CLITool


def test_is_executable_on_python():
    """python3 should be found and executable."""
    import shutil
    py = shutil.which("python3")
    if py:
        assert _is_executable(py) is True


def test_is_executable_on_missing():
    assert _is_executable("/nonexistent_xyz") is False


def test_is_executable_on_directory(tmp_path):
    assert _is_executable(str(tmp_path)) is False


def test_resolve_finds_python():
    br = BinaryResolver()
    tool = CLITool(name="python3", search_paths=[])
    path = br.resolve(tool)
    assert path is not None
    assert "python" in path


def test_resolve_returns_none_for_missing():
    br = BinaryResolver()
    tool = CLITool(name="nonexistent_tool_xyz_12345", search_paths=[])
    assert br.resolve(tool) is None


def test_resolve_search_paths_priority(tmp_path):
    """Tier 1 (search_paths) should be checked before system PATH."""
    fake_bin = tmp_path / "my_tool"
    fake_bin.write_text("#!/bin/sh\necho fake")
    fake_bin.chmod(0o755)
    br = BinaryResolver()
    tool = CLITool(name="my_tool", search_paths=[str(fake_bin)])
    result = br.resolve(tool)
    assert result == str(fake_bin)


def test_check_version_returns_string():
    br = BinaryResolver()
    tool = CLITool(name="python3", version_flag="--version")
    import shutil
    py = shutil.which("python3")
    if py:
        ver = br.check_version(tool, py)
        assert ver is not None
        assert "Python" in ver or "python" in ver.lower()


def test_check_version_returns_none_on_missing():
    br = BinaryResolver()
    tool = CLITool(name="nonexistent", version_flag="--version")
    assert br.check_version(tool, "/nonexistent_bin") is None
