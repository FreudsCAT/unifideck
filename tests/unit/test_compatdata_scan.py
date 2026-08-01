"""Stale-compatdata classification — especially the "never offer a user's
own prefix" guarantee.

On the Deck this was developed against, two of the non-Steam ``compatdata``
directories (1.0 GB) belonged to the user's own shortcuts, sitting in the
same appid range as Unifideck's. Misclassifying one would delete a live
prefix, so the ``user`` bucket is pinned here from several angles.
"""
from __future__ import annotations

import pytest

from unifideck.services.shortcut.compatdata_scan import (
    CLASS_ORPHAN,
    CLASS_UNIFIDECK,
    CLASS_USER,
    index_shortcuts,
    scan,
)


@pytest.fixture
def steam_root(tmp_path):
    (tmp_path / "steamapps" / "compatdata").mkdir(parents=True)
    return tmp_path


def make_dir(steam_root, app_id: int, size: int = 32):
    d = steam_root / "steamapps" / "compatdata" / str(app_id)
    d.mkdir(parents=True)
    (d / "system.reg").write_bytes(b"x" * size)
    return d


def shortcuts(*entries):
    """Build the ``{"0": {...}}`` mapping a parsed shortcuts.vdf carries."""
    return {str(i): e for i, e in enumerate(entries)}


UNIFIDECK_ENTRY = {"AppName": "Ghostrunner", "appid": -1859949943,
                   "tags": {"0": "Unifideck"}}
USER_ENTRY = {"AppName": "The Last of Us Part I", "appid": -1358568293,
              "exe": "/home/deck/Games/tlou/tlou.exe", "tags": {}}


def test_index_detects_unifideck_by_tag_and_by_launcher_exe():
    idx = index_shortcuts(shortcuts(
        UNIFIDECK_ENTRY,
        {"AppName": "Tagless", "appid": 5,
         "exe": "/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher x"},
        USER_ENTRY,
    ))
    assert idx[2435017353] == ("Ghostrunner", True)
    assert idx[5] == ("Tagless", True)
    assert idx[2936399003] == ("The Last of Us Part I", False)


def test_user_owned_prefix_is_reported_but_never_deletable(steam_root):
    make_dir(steam_root, 2936399003)
    result = scan(steam_root, shortcuts(USER_ENTRY))

    entry = result["entries"][0]
    assert entry["classification"] == CLASS_USER
    assert entry["deletable"] is False
    assert result["deletable_count"] == 0
    assert result["deletable_bytes"] == 0


def test_unifideck_and_orphan_dirs_are_deletable(steam_root):
    make_dir(steam_root, 2435017353, size=10)   # tagged Unifideck
    make_dir(steam_root, 2222222222, size=20)   # no shortcut at all
    make_dir(steam_root, 2936399003, size=40)   # the user's own

    result = scan(steam_root, shortcuts(UNIFIDECK_ENTRY, USER_ENTRY))
    by_id = {e["app_id"]: e for e in result["entries"]}

    assert by_id[2435017353]["classification"] == CLASS_UNIFIDECK
    assert by_id[2222222222]["classification"] == CLASS_ORPHAN
    assert by_id[2936399003]["classification"] == CLASS_USER
    assert result["deletable_count"] == 2
    assert result["deletable_bytes"] == 30  # user's 40 bytes excluded


def test_real_steam_appids_are_never_scanned(steam_root):
    make_dir(steam_root, 234140)  # a genuine Steam game
    assert scan(steam_root, {})["entries"] == []


def test_bridge_symlinks_are_skipped(steam_root, tmp_path):
    """A live game's bridge must never be offered up as reclaimable."""
    prefix = tmp_path / "prefixes" / "Sugar"
    prefix.mkdir(parents=True)
    link = steam_root / "steamapps" / "compatdata" / "3807899590"
    link.symlink_to(prefix, target_is_directory=True)

    assert scan(steam_root, {})["entries"] == []


def test_missing_root_returns_empty_not_an_error(tmp_path):
    assert scan(tmp_path / "nope", {})["entries"] == []
    assert scan(None, {})["entries"] == []
