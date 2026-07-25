"""Unit tests for the merge-on-write lost-update guard (UD-043).

Unifideck holds ``shortcuts.vdf`` in memory for the service's
lifetime and writes the whole dict back on every ``_save_all``. A
concurrent writer — NonSteamLaunchers' scanner systemd service,
Steam's own shutdown flush, a manual "Add non-Steam game" — can
append entries to the on-disk file *after* our snapshot. Without a
merge, the next ``_save_all`` overwrites them: the user's manually
added shortcuts and NSL's shortcuts vanish (UD-043 / UD-011 / UD-070).

UD-006's Exe-gate stopped reconcile from *deleting* foreign shortcuts;
this guard stops the stale-snapshot *overwrite*. The two rules share
one ownership predicate (launcher ``Exe`` basename), so an entry we
deliberately dropped stays dropped while foreign entries survive.

Covers the pure helper ``merge_foreign_shortcuts`` and the real
``ShortcutService._save_all`` end-to-end, plus a regression pinning
that the icon-update path re-reads disk (so it can't clobber either).
"""
from __future__ import annotations

import asyncio

import vdf

from unifideck.event_bus.event_bus import EventBus
from unifideck.services.shortcut.games_map import UNIFIDECK_TAG
from unifideck.services.shortcut.persistence import (
    merge_foreign_shortcuts,
    read_vdf,
)
from unifideck.services.shortcut.service import ShortcutService

_LAUNCHER = "/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher"


def _ours(appid: int, launch: str) -> dict:
    """A genuine Unifideck shortcut: launcher ``Exe`` + store:id token."""
    return {
        "appid": appid,
        "Exe": f'"{_LAUNCHER}"',
        "LaunchOptions": launch,
        "tags": {"0": UNIFIDECK_TAG, "1": launch.split(":", 1)[0]},
    }


def _nsl(appid: int, launch: str = "epic:99") -> dict:
    """A NonSteamLaunchers-created shortcut (foreign Exe)."""
    return {
        "appid": appid,
        "Exe": '"/home/deck/.local/share/NonSteamLaunchers/nsl.sh"',
        "LaunchOptions": launch,
        "tags": {},
    }


def _manual(appid: int) -> dict:
    """A manually-added non-Steam shortcut (foreign Exe, no options)."""
    return {
        "appid": appid,
        "Exe": '"/usr/bin/some-game"',
        "LaunchOptions": "",
        "tags": {},
    }


# ── Pure helper: merge_foreign_shortcuts ───────────────────────────

def test_foreign_added_on_disk_is_restored():
    """NSL + manual shortcuts that appeared on disk post-snapshot survive."""
    mem = {"shortcuts": {"0": _ours(100, "epic:1")}}
    disk = {"shortcuts": {
        "0": _ours(100, "epic:1"),
        "1": _nsl(200),
        "2": _manual(300),
    }}
    merged = merge_foreign_shortcuts(mem, disk, _LAUNCHER)
    assert merged == 2
    appids = sorted(e["appid"] for e in mem["shortcuts"].values())
    assert appids == [100, 200, 300]


def test_our_own_deletion_is_not_resurrected():
    """A Unifideck phantom we dropped from memory stays gone.

    Memory is authoritative for launcher-``Exe`` entries, so a
    reconcile stale-sweep is honoured — the merge only restores
    *foreign* entries, never our own deletions.
    """
    mem = {"shortcuts": {}}
    disk = {"shortcuts": {"0": _ours(500, "gog:5")}}
    merged = merge_foreign_shortcuts(mem, disk, _LAUNCHER)
    assert merged == 0
    assert mem["shortcuts"] == {}


def test_foreign_already_in_memory_not_duplicated():
    """A foreign entry present in both mem and disk isn't re-added."""
    mem = {"shortcuts": {"0": _nsl(200)}}
    disk = {"shortcuts": {"0": _nsl(200)}}
    merged = merge_foreign_shortcuts(mem, disk, _LAUNCHER)
    assert merged == 0
    assert len(mem["shortcuts"]) == 1


def test_merged_keys_do_not_collide():
    """Restored foreign entries get fresh non-colliding ordinal keys."""
    mem = {"shortcuts": {"0": _ours(100, "epic:1"), "1": _ours(101, "epic:2")}}
    disk = {"shortcuts": {"0": _nsl(200), "1": _manual(300)}}
    merge_foreign_shortcuts(mem, disk, _LAUNCHER)
    # No key overwritten: all four appids present, keys unique.
    assert len(mem["shortcuts"]) == 4
    assert len(set(mem["shortcuts"].keys())) == 4


def test_empty_disk_is_noop():
    """A missing / empty on-disk file merges nothing."""
    mem = {"shortcuts": {"0": _ours(100, "epic:1")}}
    assert merge_foreign_shortcuts(mem, {"shortcuts": {}}, _LAUNCHER) == 0
    assert merge_foreign_shortcuts(mem, {}, _LAUNCHER) == 0


def test_missing_wrapper_on_mem_is_established():
    """A mem dict without the ``shortcuts`` wrapper still merges safely."""
    mem: dict = {}
    disk = {"shortcuts": {"0": _nsl(200)}}
    merged = merge_foreign_shortcuts(mem, disk, _LAUNCHER)
    assert merged == 1
    assert mem["shortcuts"]["0"]["appid"] == 200 or any(
        e["appid"] == 200 for e in mem["shortcuts"].values()
    )


def test_auth_shortcut_treated_as_foreign_and_preserved():
    """A protected auth shortcut written by the auth flow survives a
    sync-path save that loaded before it was written.

    Auth shortcuts point at the launcher too, but the auth flow owns
    their lifecycle out-of-band; here we only assert the merge never
    *drops* one that disk has and memory lacks — the reconcile
    protected-set handles the delete side."""
    # Auth shortcut with a NON-launcher Exe (the forwarder), foreign to
    # the game-sync snapshot.
    auth = {
        "appid": 777,
        "Exe": '"/home/deck/.../upc-auth-forwarder"',
        "LaunchOptions": "ubisoft:upc-auth",
        "tags": {"0": "auth-ubisoft"},
    }
    mem = {"shortcuts": {"0": _ours(100, "epic:1")}}
    disk = {"shortcuts": {"0": _ours(100, "epic:1"), "1": auth}}
    merged = merge_foreign_shortcuts(mem, disk, _LAUNCHER)
    assert merged == 1
    assert any(e["appid"] == 777 for e in mem["shortcuts"].values())


# ── Integration: ShortcutService._save_all end-to-end ──────────────

def _make_service(tmp_path) -> ShortcutService:
    sc_path = str(tmp_path / "shortcuts.vdf")
    gm_path = str(tmp_path / "games.map")
    return ShortcutService(
        EventBus(), sc_path, gm_path, launcher_path=_LAUNCHER,
    )


def _write_vdf_file(path: str, entries: dict) -> None:
    with open(path, "wb") as f:
        f.write(vdf.binary_dumps({"shortcuts": entries}))


def test_save_all_preserves_concurrent_foreign_write(tmp_path):
    """The real save path re-reads disk and keeps an NSL shortcut that
    landed after our in-memory snapshot was loaded — the UD-043 fix."""
    svc = _make_service(tmp_path)

    # 1. Initial on-disk state: just our game. Load it into memory.
    _write_vdf_file(svc._shortcuts_path, {"0": _ours(100, "epic:1")})
    asyncio.run(svc._load_shortcuts())

    # 2. Concurrent writer (NSL scanner) appends a shortcut to disk
    #    AFTER our snapshot — the classic lost-update window.
    _write_vdf_file(svc._shortcuts_path, {
        "0": _ours(100, "epic:1"),
        "1": _nsl(200),
    })

    # 3. We mutate our in-memory copy (add another of our games) and save.
    svc._shortcuts["shortcuts"]["1"] = _ours(101, "epic:2")
    asyncio.run(svc._save_all())

    # 4. Disk must now hold OUR two games AND NSL's shortcut.
    on_disk = asyncio.run(read_vdf(svc._shortcuts_path))
    appids = sorted(e["appid"] for e in on_disk["shortcuts"].values())
    assert appids == [100, 101, 200], appids


def test_save_all_still_persists_our_deletions(tmp_path):
    """Merge must not resurrect a Unifideck shortcut we removed."""
    svc = _make_service(tmp_path)
    _write_vdf_file(svc._shortcuts_path, {
        "0": _ours(100, "epic:1"),
        "1": _ours(101, "epic:2"),
    })
    asyncio.run(svc._load_shortcuts())

    # Reconcile-style delete of one of our games in memory.
    del svc._shortcuts["shortcuts"]["1"]
    asyncio.run(svc._save_all())

    on_disk = asyncio.run(read_vdf(svc._shortcuts_path))
    appids = sorted(e["appid"] for e in on_disk["shortcuts"].values())
    assert appids == [100], appids


def test_save_all_no_conflict_is_clean(tmp_path):
    """When disk == snapshot, save writes memory verbatim (no dupes)."""
    svc = _make_service(tmp_path)
    _write_vdf_file(svc._shortcuts_path, {"0": _ours(100, "epic:1")})
    asyncio.run(svc._load_shortcuts())
    asyncio.run(svc._save_all())
    on_disk = asyncio.run(read_vdf(svc._shortcuts_path))
    assert len(on_disk["shortcuts"]) == 1


# ── Regression: the icon-update path re-reads disk (can't clobber) ──

def test_icon_update_reads_fresh_and_keeps_foreign(tmp_path):
    """``_update_icons_from_grid`` operates on a fresh ``read_vdf``, so a
    foreign shortcut present on disk survives an icon refresh even if it
    was never in the service's in-memory snapshot."""
    from unifideck.services.shortcut import events

    sc_path = str(tmp_path / "shortcuts.vdf")
    grid = tmp_path / "grid"
    grid.mkdir()
    # A Unifideck game whose icon file exists, plus a foreign NSL entry.
    _write_vdf_file(sc_path, {
        "0": _ours(100, "epic:1"),
        "1": _nsl(200),
    })
    # Drop an icon file named for our appid so the updater actually
    # mutates + writes (Steam's grid naming: unsigned appid + high bit).
    unsigned = (100 & 0xFFFFFFFF) | 0x80000000
    (grid / f"{unsigned}_icon.png").write_bytes(b"x")

    svc = type("S", (), {"_shortcuts_path": sc_path})()
    updated = asyncio.run(events._update_icons_from_grid(svc))
    assert updated == 1  # the write path fired, not a trivial no-op

    on_disk = asyncio.run(read_vdf(sc_path))
    appids = sorted(e["appid"] for e in on_disk["shortcuts"].values())
    # The foreign NSL shortcut (200) must survive the icon rewrite.
    assert 200 in appids, appids
    assert 100 in appids, appids


# ── set_shortcuts_path (per-user re-bind) resets the cached snapshot ──

def test_set_shortcuts_path_reloads_new_users_file(tmp_path):
    """Re-binding to another user's file must NOT write the previous user's
    cached entries into it — the setter resets ``_shortcuts_loaded`` so the
    next access reads the correct file (the active-user misfire fix)."""
    svc = _make_service(tmp_path)

    # User A's file has one of our games; load it into the cache.
    user_a = str(tmp_path / "userA_shortcuts.vdf")
    _write_vdf_file(user_a, {"0": _ours(100, "epic:1")})
    svc.set_shortcuts_path(user_a)
    asyncio.run(svc._load_shortcuts())
    assert svc._shortcuts_loaded is True

    # Switch to user B's file (a DIFFERENT game). The setter must clear the
    # cache so B's file is read fresh, not overwritten with A's snapshot.
    user_b = str(tmp_path / "userB_shortcuts.vdf")
    _write_vdf_file(user_b, {"0": _ours(200, "gog:9")})
    svc.set_shortcuts_path(user_b)
    assert svc._shortcuts_loaded is False
    assert svc._shortcuts == {}

    asyncio.run(svc._save_all())  # loads B fresh, merges (no-op), writes back
    on_disk = asyncio.run(read_vdf(user_b))
    appids = sorted(e["appid"] for e in on_disk["shortcuts"].values())
    assert appids == [200], appids  # A's game 100 must NOT leak into B's file


def test_set_shortcuts_path_noop_when_unchanged(tmp_path):
    """Re-binding to the same path keeps the loaded cache intact."""
    svc = _make_service(tmp_path)
    _write_vdf_file(svc._shortcuts_path, {"0": _ours(100, "epic:1")})
    asyncio.run(svc._load_shortcuts())
    svc.set_shortcuts_path(svc._shortcuts_path)
    assert svc._shortcuts_loaded is True
