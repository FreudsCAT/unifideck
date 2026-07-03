"""Tests for the "100% clean" pieces of ``perform_full_cleanup``.

Covers the gaps surfaced by live validation (a destructive "Delete all
Unifideck data" left ~22 GB of Proton prefixes, all residual state, the
Ubisoft installer cache, and a working GOG login on disk):

* :func:`safe_delete.is_safe_to_delete` structural guard (replaces the old
  loose substring allowlist that skipped custom install locations).
* ``_wipe_data_dir`` two-tier behaviour — non-destructive keeps the data
  needed to keep installed games playable (``prefixes``/``saves``/
  ``save_backups``); destructive removes everything.
* ``_delete_external_prefixes`` reaches SD/custom Ubisoft prefixes recorded
  in ``ubisoft_id_map.json`` (outside the data dir).
* ``_wipe_config_auth`` deletes the real GOG creds while preserving the
  user's ``config.json`` and Heroic's ``heroic_gogdl``.
* ``_delete_install_dir`` deletes a recorded work_dir at any location but
  refuses ``$HOME`` / shallow paths.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from unifideck.core import safe_delete
from unifideck.rpc.mixins.sync import SyncRPCMixin


def _mixin(**attrs: Any) -> SyncRPCMixin:
    m = SyncRPCMixin()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


def _fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


# --------------------------------------------------------------------------
# safe_delete guard
# --------------------------------------------------------------------------
def test_is_safe_to_delete_rejects_dangerous_paths(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    assert not safe_delete.is_safe_to_delete("")
    assert not safe_delete.is_safe_to_delete("/")
    assert not safe_delete.is_safe_to_delete(str(home))
    assert not safe_delete.is_safe_to_delete(str(home.parent))  # ancestor
    # Custom/SD install locations must be deletable (the old bug).
    assert safe_delete.is_safe_to_delete(str(home / "Games"))
    assert safe_delete.is_safe_to_delete(
        "/run/media/deck/SD/MyLibrary/SomeGame",
    )


def test_safe_rmtree_refuses_home_but_deletes_deep(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    (home / "keep.txt").write_text("x")
    assert safe_delete.safe_rmtree(home) is False  # guard refused
    assert (home / "keep.txt").exists()

    deep = home / "a/b/c/game"
    deep.mkdir(parents=True)
    assert safe_delete.safe_rmtree(deep) is True
    assert not deep.exists()


# --------------------------------------------------------------------------
# _wipe_data_dir — two-tier
# --------------------------------------------------------------------------
def _populate_data_dir(home: Path) -> Path:
    data = home / ".local/share/unifideck"
    (data / "prefixes/gameA").mkdir(parents=True)
    (data / "saves/x").mkdir(parents=True)
    (data / "save_backups/x").mkdir(parents=True)
    (data / "ubisoft_installer_cache").mkdir()
    (data / "edge-auth").mkdir()
    for f in ("library_cache.json", "shortcuts_registry.json",
              "download_history.json", "playtime.db", "games.map",
              "ubisoft_id_map.json"):
        (data / f).write_text("x")
    return data


@pytest.mark.asyncio
async def test_wipe_data_dir_keeps_games_when_non_destructive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    data = _populate_data_dir(home)
    m = _mixin()

    await m._wipe_data_dir(delete_files=False)

    # Playable-game data kept so games stay re-syncable.
    assert (data / "prefixes").is_dir()
    assert (data / "saves").is_dir()
    assert (data / "save_backups").is_dir()
    # Everything else (state + caches) gone.
    assert not (data / "library_cache.json").exists()
    assert not (data / "shortcuts_registry.json").exists()
    assert not (data / "ubisoft_installer_cache").exists()
    assert not (data / "edge-auth").exists()


@pytest.mark.asyncio
async def test_wipe_data_dir_removes_everything_when_destructive(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    data = _populate_data_dir(home)
    m = _mixin()

    await m._wipe_data_dir(delete_files=True)

    assert not (data / "prefixes").exists()
    assert not (data / "saves").exists()
    assert not (data / "save_backups").exists()
    assert not (data / "library_cache.json").exists()
    # The data dir itself survives (contents only) for the plugin to reuse.
    assert data.is_dir()


# --------------------------------------------------------------------------
# _delete_external_prefixes — SD/custom Ubisoft prefixes
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_external_prefixes_reaches_out_of_tree(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    data = home / ".local/share/unifideck"
    data.mkdir(parents=True)
    # External (custom storage) prefix + an internal one (handled elsewhere).
    external = home / "Games/prefixes/ubisoft/46"
    external.mkdir(parents=True)
    internal = data / "prefixes/ubisoft/109"
    internal.mkdir(parents=True)
    (data / "ubisoft_id_map.json").write_text(json.dumps({
        "46": {"prefix_path": str(external)},
        "109": {"prefix_path": str(internal)},
        "4": {"name": "no-prefix"},
    }))
    m = _mixin()

    count = await m._delete_external_prefixes()

    assert count == 1
    assert not external.exists()      # external one removed
    assert internal.exists()          # internal left to the data-dir wipe


# --------------------------------------------------------------------------
# _wipe_config_auth — GOG creds vs preserved files
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_wipe_config_auth_deletes_gog_creds_keeps_user_config(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    cfg = home / ".config/unifideck"
    cfg.mkdir(parents=True)
    for f in ("gog_credentials.json", "gogdl_auth.json", "gog_token.json",
              "config.json"):
        (cfg / f).write_text("x")
    (cfg / "gogdl").mkdir()
    (cfg / "heroic_gogdl").mkdir()
    m = _mixin()

    await m._wipe_config_auth()

    assert not (cfg / "gog_credentials.json").exists()
    assert not (cfg / "gogdl_auth.json").exists()
    assert not (cfg / "gogdl").exists()
    # User prefs + Heroic's own gogdl are preserved.
    assert (cfg / "config.json").exists()
    assert (cfg / "heroic_gogdl").exists()


# --------------------------------------------------------------------------
# _delete_install_dir — robust guard
# --------------------------------------------------------------------------
@pytest.mark.asyncio
async def test_delete_install_dir_handles_custom_location(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    custom = tmp_path / "mnt/ssd/MyLibrary/SomeGame"
    custom.mkdir(parents=True)
    (custom / "game.exe").write_text("x")
    m = _mixin()

    assert await m._delete_install_dir(str(custom)) is True
    assert not custom.exists()


@pytest.mark.asyncio
async def test_delete_install_dir_refuses_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    home = _fake_home(monkeypatch, tmp_path)
    m = _mixin()
    assert await m._delete_install_dir(str(home)) is False
    assert home.exists()
