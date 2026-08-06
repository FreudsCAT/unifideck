"""The boot-time reclaim that actually deletes Steam-made compatdata prefixes.

Deletion is irreversible and runs unattended, so every guard is pinned:

* authorisation comes from a ``.unifideck*`` marker inside the directory, never
  from the appid — after an uninstall ``games.map`` has dropped the row and the
  leftover is indistinguishable from a stranger's prefix by appid alone;
* a user-owned appid is vetoed even if a marker somehow appeared;
* a prefix whose ``pfx.lock`` is held is left alone;
* a path that is some installed game's live prefix is left alone.
"""
from __future__ import annotations

import fcntl

import pytest

from unifideck.core import compat_bridge
from unifideck.services import prefix_bridge

UNIFIDECK_ENTRY = {"AppName": "Ghostrunner", "appid": -1859949943,
                   "tags": {"0": "Unifideck"}}          # u32 2435017353
USER_ENTRY = {"AppName": "The Last of Us Part I", "appid": -1358568293,
              "exe": "/home/deck/Games/tlou/tlou.exe", "tags": {}}  # 2936399003


@pytest.fixture
def env(tmp_path, monkeypatch):
    data = tmp_path / "data"
    prefixes = data / "prefixes"
    steam_root = tmp_path / "steam"
    prefixes.mkdir(parents=True)
    (steam_root / "steamapps" / "compatdata").mkdir(parents=True)
    monkeypatch.setattr(compat_bridge, "PREFIX_ROOT", prefixes)
    monkeypatch.setattr(prefix_bridge.compat_bridge, "PREFIX_ROOT", prefixes)
    monkeypatch.setattr(prefix_bridge, "_GAMES_MAP", data / "games.map")
    monkeypatch.setattr(prefix_bridge, "_UBISOFT_ID_MAP", data / "ubisoft_id_map.json")
    return data, prefixes, steam_root


def compat_dir(steam_root, app_id, *, marker=None, size=64, lock=True):
    d = steam_root / "steamapps" / "compatdata" / str(app_id)
    d.mkdir(parents=True)
    (d / "system.reg").write_bytes(b"x" * size)
    if lock:
        (d / "pfx.lock").write_text("")
    if marker:
        (d / marker).write_text("")
    return d


def shortcuts(*entries):
    return {str(i): e for i, e in enumerate(entries)}


def test_marked_prefix_is_deleted(env):
    _data, _prefixes, steam_root = env
    d = compat_dir(steam_root, 2435017353, marker=".unifideck_proton_version")

    tally = prefix_bridge.reclaim_redundant_compatdata(
        steam_root, shortcuts(UNIFIDECK_ENTRY),
    )

    assert not d.exists()
    assert tally["deleted"] == 1
    assert tally["freed_bytes"] > 0


def test_unmarked_prefix_survives(env):
    _data, _prefixes, steam_root = env
    d = compat_dir(steam_root, 2435017353)

    tally = prefix_bridge.reclaim_redundant_compatdata(
        steam_root, shortcuts(UNIFIDECK_ENTRY),
    )

    assert d.is_dir()
    assert tally["deleted"] == 0


def test_user_owned_prefix_survives_even_with_a_marker(env):
    """The veto holds regardless of what is inside the directory."""
    _data, _prefixes, steam_root = env
    d = compat_dir(steam_root, 2936399003, marker=".unifideck_proton_version")

    tally = prefix_bridge.reclaim_redundant_compatdata(
        steam_root, shortcuts(USER_ENTRY),
    )

    assert d.is_dir()
    assert tally["deleted"] == 0


def test_empty_shortcuts_vdf_deletes_nothing_unmarked(env):
    """The 1.01 GB regression, at the deletion layer."""
    _data, _prefixes, steam_root = env
    a = compat_dir(steam_root, 2407186659)
    b = compat_dir(steam_root, 2936399003)

    tally = prefix_bridge.reclaim_redundant_compatdata(steam_root, {})

    assert a.is_dir() and b.is_dir()
    assert tally["deleted"] == 0


def test_marked_orphan_is_deleted_after_uninstall(env):
    """games.map has no row, no shortcut exists — the marker still proves it."""
    _data, _prefixes, steam_root = env
    d = compat_dir(steam_root, 2222222222, marker=".unifideck_legacy_migrated")

    tally = prefix_bridge.reclaim_redundant_compatdata(steam_root, {})

    assert not d.exists()
    assert tally["deleted"] == 1


def test_locked_prefix_is_skipped(env):
    _data, _prefixes, steam_root = env
    d = compat_dir(steam_root, 2435017353, marker=".unifideck_proton_version")

    with (d / "pfx.lock").open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        tally = prefix_bridge.reclaim_redundant_compatdata(
            steam_root, shortcuts(UNIFIDECK_ENTRY),
        )

    assert d.is_dir()
    assert tally["deleted"] == 0
    assert tally["skipped_in_use"] == 1


def test_live_game_prefix_is_never_deleted(env, monkeypatch):
    """Guard 3: a path that resolves to an installed game's prefix is off limits."""
    data, _prefixes, steam_root = env
    d = compat_dir(steam_root, 2435017353, marker=".unifideck_proton_version")
    (data / "games.map").write_text(
        "# Unifideck non-Steam shortcut manifest (games.map)\n"
        "epic:Sugar=/games/rl/rl.exe\t/games/rl\t-1859949943\n",
    )
    # Pretend the game's resolved prefix IS this compatdata directory.
    monkeypatch.setattr(prefix_bridge, "resolve_prefix", lambda store, gid: d)

    tally = prefix_bridge.reclaim_redundant_compatdata(
        steam_root, shortcuts(UNIFIDECK_ENTRY),
    )

    assert d.is_dir()
    assert tally["deleted"] == 0


def test_missing_steam_root_is_a_noop(env):
    assert prefix_bridge.reclaim_redundant_compatdata(None, {})["deleted"] == 0
