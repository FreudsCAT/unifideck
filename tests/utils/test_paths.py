"""Tests for utils/paths.py (OP-33a)."""
from __future__ import annotations

import os
from pathlib import Path

from unifideck.utils.paths import (
    DEFAULT_GAMES_MAP,
    DEFAULT_INSTALL_DIRS,
    DEFAULT_SD_ROOT,
    GAMES_MAP_PATH,
    dedupe_paths,
    ensure_games_map_dir,
    expand,
    get_all_game_directories,
    get_games_map_path,
)


def test_expand_tilde():
    result = expand("~/test")
    assert result == str(Path.home() / "test")
    assert "~" not in result


def test_expand_absolute_unchanged():
    assert expand("/absolute/path") == "/absolute/path"


def test_expand_env_var(monkeypatch):
    monkeypatch.setenv("UNIFIDECK_TEST_VAR", "/custom/path")
    assert expand("$UNIFIDECK_TEST_VAR/sub") == "/custom/path/sub"


def test_dedupe_preserves_order():
    assert dedupe_paths(["/a", "/b", "/a", "/c", "/b"]) == ["/a", "/b", "/c"]


def test_dedupe_empty():
    assert dedupe_paths([]) == []


def test_games_map_path_default():
    path = get_games_map_path()
    assert "games.map" in path
    assert os.path.isabs(path)


def test_games_map_path_from_config():
    from tests.helpers import MockConfig
    cfg = MockConfig({"paths": {"games_map": "/custom/games.map"}})
    path = get_games_map_path(cfg)
    assert path == "/custom/games.map"


def test_ensure_games_map_dir_creates(tmp_path):
    from tests.helpers import MockConfig
    gm = str(tmp_path / "sub" / "games.map")
    cfg = MockConfig({"paths": {"games_map": gm}})
    result = ensure_games_map_dir(cfg)
    assert result == str(tmp_path / "sub")
    assert (tmp_path / "sub").is_dir()


def test_default_install_dirs_has_all_stores():
    for store in ("epic", "gog", "amazon", "microsoft", "ubisoft"):
        assert store in DEFAULT_INSTALL_DIRS


def test_legacy_aliases():
    assert "games.map" in GAMES_MAP_PATH
    assert "epic" in DEFAULT_INSTALL_DIRS


def test_get_all_game_directories_returns_list():
    dirs = get_all_game_directories()
    assert isinstance(dirs, list)
