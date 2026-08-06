"""Stale-compatdata classification and the guards that authorise deletion.

This runs unattended at boot, so the identification has to be positive proof:
a ``.unifideck*`` marker written *inside* the directory. The appid
classification only ever vetoes.

Two failure modes are pinned here because both were measured on the dev Deck,
where the user's own prefixes (*The Last of Us* I and II, 1.01 GB) sat in the
same appid range as Unifideck's:

* an empty ``shortcuts.vdf`` turns every directory into ``orphan`` — which must
  still delete nothing;
* a marker-less directory is Steam's own creation and is never ours to remove.
"""
from __future__ import annotations

import pytest

from unifideck.services.shortcut.compatdata_scan import (
    CLASS_ORPHAN,
    CLASS_UNIFIDECK,
    CLASS_USER,
    has_unifideck_marker,
    index_shortcuts,
    is_prefix_in_use,
    scan,
)


@pytest.fixture
def steam_root(tmp_path):
    (tmp_path / "steamapps" / "compatdata").mkdir(parents=True)
    return tmp_path


def make_dir(steam_root, app_id: int, size: int = 32, marker: str | None = None):
    """A compatdata dir; *marker* names a Unifideck marker file to plant."""
    d = steam_root / "steamapps" / "compatdata" / str(app_id)
    d.mkdir(parents=True)
    (d / "system.reg").write_bytes(b"x" * size)
    if marker:
        (d / marker).write_text("")
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


def test_only_a_marker_authorises_deletion(steam_root):
    """The marker decides; the classification only vetoes."""
    make_dir(steam_root, 2435017353, size=10, marker=".unifideck_proton_version")
    make_dir(steam_root, 2222222222, size=20)   # orphan, no marker
    make_dir(steam_root, 2936399003, size=40)   # the user's own

    result = scan(steam_root, shortcuts(UNIFIDECK_ENTRY, USER_ENTRY))
    by_id = {e["app_id"]: e for e in result["entries"]}

    assert by_id[2435017353]["classification"] == CLASS_UNIFIDECK
    assert by_id[2222222222]["classification"] == CLASS_ORPHAN
    assert by_id[2936399003]["classification"] == CLASS_USER

    # Only the marked one is reclaimable.
    assert by_id[2435017353]["deletable"] is True
    assert by_id[2435017353]["marker"] == ".unifideck_proton_version"
    assert by_id[2222222222]["deletable"] is False
    assert by_id[2936399003]["deletable"] is False
    assert result["deletable_count"] == 1
    assert result["deletable_bytes"] == 10


def test_unifideck_shortcut_without_a_marker_is_not_deletable(steam_root):
    """Steam made this prefix; our launcher pointed WINEPREFIX elsewhere."""
    make_dir(steam_root, 2435017353, size=10)
    result = scan(steam_root, shortcuts(UNIFIDECK_ENTRY))

    assert result["entries"][0]["classification"] == CLASS_UNIFIDECK
    assert result["entries"][0]["marker"] is None
    assert result["entries"][0]["deletable"] is False
    assert result["deletable_count"] == 0


def test_marker_wins_after_uninstall_when_the_shortcut_is_gone(steam_root):
    """The case appid attribution cannot cover: games.map dropped the row."""
    make_dir(steam_root, 2222222222, size=15, marker=".unifideck_legacy_migrated")
    result = scan(steam_root, {})

    entry = result["entries"][0]
    assert entry["classification"] == CLASS_ORPHAN
    assert entry["deletable"] is True
    assert result["deletable_bytes"] == 15


def test_a_user_prefix_is_safe_even_if_shortcuts_vdf_reads_empty(steam_root):
    """The 1.01 GB regression: empty vdf must not authorise anything."""
    make_dir(steam_root, 2407186659, size=40)   # user's, no marker
    make_dir(steam_root, 2936399003, size=40)   # user's, no marker

    result = scan(steam_root, {})

    assert [e["classification"] for e in result["entries"]] == [
        CLASS_ORPHAN, CLASS_ORPHAN,
    ]
    assert result["deletable_count"] == 0
    assert result["deletable_bytes"] == 0


@pytest.mark.parametrize("marker", [
    ".unifideck_proton_version",
    ".unifideck-gog-setup-done",
    ".unifideck_vcreg_GE-Proton11-3.v2.done",
    ".unifideck_prereqs_Sugar_8adc4614ad1c.done",
    "unifideck_winetricks_complete.marker",
])
def test_every_real_marker_shape_is_recognised(tmp_path, marker):
    """Pinned against the marker names observed in live prefixes."""
    (tmp_path / marker).write_text("")
    assert has_unifideck_marker(tmp_path) == marker


def test_unrelated_dotfiles_are_not_markers(tmp_path):
    for name in (".steam", "system.reg", "pfx.lock", "unifidb.json"):
        (tmp_path / name).write_text("")
    assert has_unifideck_marker(tmp_path) is None


def test_a_locked_prefix_is_reported_in_use(steam_root):
    """Proton holds pfx.lock while a prefix is live, so we must not delete it."""
    import fcntl

    d = make_dir(steam_root, 2435017353, size=10,
                 marker=".unifideck_proton_version")
    lock = d / "pfx.lock"
    lock.write_text("")

    with lock.open("rb") as held:
        fcntl.flock(held.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        assert is_prefix_in_use(d) is True
        result = scan(steam_root, shortcuts(UNIFIDECK_ENTRY))

    entry = result["entries"][0]
    assert entry["in_use"] is True
    assert entry["deletable"] is False
    assert result["deletable_count"] == 0

    # Released: the same prefix becomes reclaimable.
    assert is_prefix_in_use(d) is False
    assert scan(steam_root, shortcuts(UNIFIDECK_ENTRY))["deletable_count"] == 1


def test_absent_lock_file_is_not_treated_as_in_use(tmp_path):
    assert is_prefix_in_use(tmp_path) is False


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
