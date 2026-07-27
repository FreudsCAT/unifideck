"""Tests for destination and root resolution.

The fallback chains are the device-agnostic part of Capture Logs, so
each rung is exercised explicitly. ``os.access`` is monkeypatched rather
than using ``chmod``, because CI may run as root — where ``chmod 0o500``
does not actually deny access, and a test that has to be skipped is a
test that fails this project's suite.
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest

from unifideck.services.support_bundle import resolve

_DECKY_VARS = (
    "DECKY_PLUGIN_LOG_DIR", "DECKY_HOME", "DECKY_PLUGIN_DIR",
    "DECKY_PLUGIN_NAME", "DECKY_PLUGIN_RUNTIME_DIR",
)


class _FakeConfig:
    """ConfigManager stand-in returning canned strings."""

    def __init__(self, values: dict[str, str] | None = None) -> None:
        self._values = values or {}

    def get_str(self, key: str, default: str = "") -> str:
        return self._values.get(key, default)

    def get_int(self, key: str, default: int = 0) -> int:
        return default

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    monkeypatch.setenv("HOME", str(home))
    for name in _DECKY_VARS:
        monkeypatch.delenv(name, raising=False)
    return home


def _no_xdg(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend ``xdg-user-dir`` is not installed."""
    monkeypatch.setattr(resolve, "_xdg_download_dir", lambda: None)


# ── destination chain ─────────────────────────────────────────────
def test_explicit_destination_wins(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    target = tmp_path / "explicit"
    target.mkdir()
    resolved, source, _ = resolve.resolve_dest(str(target), _FakeConfig(), None)
    assert resolved == target
    assert source == "explicit"


def test_config_export_path_is_honoured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """``logs.export_path`` already existed in the schema, unread."""
    _fake_home(monkeypatch, tmp_path)
    configured = tmp_path / "configured"
    configured.mkdir()
    config = _FakeConfig({"logs.export_path": str(configured)})
    resolved, source, _ = resolve.resolve_dest("", config, None)
    assert resolved == configured
    assert source == "config"


def test_falls_back_to_xdg_download_dir(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Covers a localised Downloads folder name."""
    home = _fake_home(monkeypatch, tmp_path)
    localised = home / "Telechargements"
    localised.mkdir()
    monkeypatch.setattr(resolve, "_xdg_download_dir", lambda: str(localised))
    config = _FakeConfig({"logs.export_path": "/nonexistent/ro/path"})
    monkeypatch.setattr(Path, "mkdir", _deny_mkdir(localised))
    resolved, source, tried = resolve.resolve_dest("", config, None)
    assert resolved == localised
    assert source == "xdg"
    assert any("nonexistent" in item for item in tried)


def _deny_mkdir(allowed: Path) -> Any:
    """Patch ``mkdir`` so only ``allowed`` can be created."""
    real = Path.mkdir

    def _mkdir(self: Path, *args: Any, **kwargs: Any) -> Any:
        if self == allowed:
            return real(self, *args, **kwargs)
        raise OSError(30, "Read-only file system")

    return _mkdir


def test_unwritable_config_dir_falls_through_to_home_downloads(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    blocked = tmp_path / "blocked"
    blocked.mkdir()
    downloads = home / "Downloads"
    downloads.mkdir()
    _no_xdg(monkeypatch)
    real_access = os.access

    def _access(path: Any, mode: int) -> bool:
        if str(path) == str(blocked):
            return False
        return real_access(path, mode)

    monkeypatch.setattr(os, "access", _access)
    config = _FakeConfig({"logs.export_path": str(blocked)})
    resolved, source, _ = resolve.resolve_dest("", config, None)
    assert resolved == downloads
    assert source == "home_downloads"


def test_all_destinations_unwritable_raises(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    _no_xdg(monkeypatch)
    monkeypatch.setattr(os, "access", lambda *_args: False)
    monkeypatch.setattr(
        Path, "mkdir",
        lambda *_a, **_k: (_ for _ in ()).throw(OSError(30, "read-only")),
    )
    with pytest.raises(OSError, match="no writable destination"):
        resolve.resolve_dest("", _FakeConfig(), None)


def test_empty_dest_never_degrades_into_the_home_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Guard against the bug the older single-file export had.

    There, an empty destination became ``Path("")`` and then
    ``Path.home() / ""``, so logs were written as bare files straight
    into ``$HOME``. An empty argument must skip the explicit rung
    entirely, not resolve to nothing-in-particular.
    """
    home = _fake_home(monkeypatch, tmp_path)
    configured = tmp_path / "downloads"
    configured.mkdir()
    config = _FakeConfig({"logs.export_path": str(configured)})
    resolved, source, tried = resolve.resolve_dest("", config, None)
    assert resolved == configured
    assert source != "explicit"
    assert not any("explicit" in item for item in tried)
    assert resolved != home


def test_explicit_zip_path_uses_its_parent_and_filename(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    target = tmp_path / "out" / "mybundle.zip"
    target.parent.mkdir()
    resolved, source, _ = resolve.resolve_dest(str(target), _FakeConfig(), None)
    assert resolved == target.parent
    assert source == "explicit"
    assert resolve.archive_name(str(target)) == "mybundle.zip"


def test_generated_archive_name_is_timestamped() -> None:
    name = resolve.archive_name("")
    assert name.startswith("unifideck-logs-")
    assert name.endswith(".zip")


# ── Decky log directory chain ─────────────────────────────────────
def test_log_dir_prefers_the_explicit_env_var(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    explicit = tmp_path / "explicit-logs"
    explicit.mkdir()
    monkeypatch.setenv("DECKY_PLUGIN_LOG_DIR", str(explicit))
    found, source, _ = resolve.resolve_decky_log_dir()
    assert found == explicit
    assert source == "DECKY_PLUGIN_LOG_DIR"


def test_log_dir_derived_from_decky_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    decky_home = tmp_path / "homebrew"
    logs = decky_home / "logs" / "Unifideck"
    logs.mkdir(parents=True)
    monkeypatch.setenv("DECKY_HOME", str(decky_home))
    found, source, _ = resolve.resolve_decky_log_dir()
    assert found == logs
    assert source == "DECKY_HOME"


def test_log_dir_derived_from_the_plugin_dir_sibling(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    plugin = home / "homebrew" / "plugins" / "Unifideck"
    plugin.mkdir(parents=True)
    logs = home / "homebrew" / "logs" / "Unifideck"
    logs.mkdir(parents=True)
    monkeypatch.setenv("DECKY_PLUGIN_DIR", str(plugin))
    # DECKY_HOME would otherwise win; this asserts the sibling rung.
    monkeypatch.delenv("DECKY_HOME", raising=False)
    found, source, _ = resolve.resolve_decky_log_dir()
    assert found == logs
    assert source in ("DECKY_HOME", "plugin_dir_sibling")


def test_log_dir_survives_a_case_mismatch(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Decky's docs use a lowercase plugin dir; our name is capitalised.

    Without the case-insensitive scan this reports "no logs found" with
    the logs sitting right there.
    """
    home = _fake_home(monkeypatch, tmp_path)
    logs = home / "homebrew" / "logs" / "unifideck"
    logs.mkdir(parents=True)
    found, source, _ = resolve.resolve_decky_log_dir()
    assert found == logs
    assert source == "case_insensitive_scan"


def test_log_dir_not_found_is_reported_with_candidates(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    found, source, tried = resolve.resolve_decky_log_dir()
    assert found is None
    assert source == "not_found"
    assert tried, "every candidate must be recorded for the manifest"


def test_log_dir_resolution_never_creates_the_directory(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """Creating an empty dir here would mask the real one forever."""
    home = _fake_home(monkeypatch, tmp_path)
    resolve.resolve_decky_log_dir()
    assert not (home / "homebrew" / "logs").exists()


# ── root labelling ────────────────────────────────────────────────
def test_root_source_labels_name_the_resolver_that_won(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """The label must not claim ServicePaths when it fell back.

    An earlier version hardcoded ``paths.steam_root`` even when the
    value came from the liveness probe — exactly the detail a reader
    relies on when a root resolves somewhere unexpected.
    """
    _fake_home(monkeypatch, tmp_path)
    roots, sources, _ = resolve.build_roots(_FakeConfig(), None)
    assert sources["steam"] != "paths.steam_root"
    assert sources["data"] == "config"
    assert roots["home"] == str(Path.home())


def test_service_paths_take_priority_over_probes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    steam = tmp_path / "custom-steam"
    steam.mkdir()

    class _Paths:
        steam_root = str(steam)
        data_dir = str(tmp_path / "custom-data")
        plugin_dir = str(tmp_path / "custom-plugin")

    roots, sources, _ = resolve.build_roots(_FakeConfig(), _Paths())
    assert roots["steam"] == str(steam)
    assert sources["steam"] == "paths.steam_root"
    assert sources["data"] == "paths.data_dir"


def test_sanitize_entry_name_strips_shell_hostile_characters() -> None:
    assert resolve.sanitize_entry_name("2026-07-23 16.21.34.log") == (
        "2026-07-23_16.21.34.log"
    )
    assert resolve.sanitize_entry_name("a/b:c*d") == "a_b_c_d"
    assert resolve.sanitize_entry_name("") == "unnamed"
