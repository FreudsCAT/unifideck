"""Unit tests for the games.map repopulation pass (``map_repair``).

The bug: reconcile only (re)writes a ``games.map`` row when the synced
``Game`` carries BOTH ``installed`` and ``exe_path``. Epic and Amazon
resolve ``exe_path`` at install time, never during a library sync, so an
installed game whose row is absent stays absent — nothing in the codebase
creates one. That happens whenever the games outlive the plugin's data
dir: "delete plugin data", a reinstall onto a machine that already has
the games, a partial restore, a move to another Deck.

It is silent. The library looks healthy and the games still launch,
because the dispatcher re-resolves the executable by search. What it
cannot invent is the row's ``app_id``, so ``ctx.steam_app_id`` stays
``None``, ``select_proton_version`` skips its Steam force-compat tier,
and the user's Properties > Compatibility choice is ignored across the
whole library with no error and no log line.

These tests drive the real ``ShortcutService.reconcile`` against a
tmp_path data dir, because two halves of the fix can each fail on their
own: writing the row, and *persisting* it — reconcile skips
``_save_all`` unless something changed, and a stable library reports
nothing but ``kept``.
"""
from __future__ import annotations

import asyncio

import pytest
import vdf

from unifideck.core.types import Game
from unifideck.event_bus.event_bus import EventBus
from unifideck.services.shortcut.games_map import (
    UNIFIDECK_TAG,
    generate_app_id,
    parse_games_map,
)
from unifideck.services.shortcut.map_repair import repair_missing_rows
from unifideck.services.shortcut.service import ShortcutService

_LAUNCHER = "/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher"


def _shortcut(appid: int, launch: str) -> dict:
    """A genuine Unifideck shortcut: launcher ``Exe`` + store:id token."""
    return {
        "appid": appid,
        "AppName": launch,
        "Exe": f'"{_LAUNCHER}"',
        "LaunchOptions": launch,
        "tags": {"0": UNIFIDECK_TAG, "1": launch.split(":", 1)[0]},
    }


def _install_dir(tmp_path, name: str, exe: str | None = "Game.exe"):
    """An install dir holding one plausible game executable."""
    install = tmp_path / "games" / name
    install.mkdir(parents=True)
    if exe is not None:
        target = install / exe
        target.write_bytes(b"MZ" + b"\0" * 4096)
    return install


def _service(tmp_path, shortcuts: dict | None = None) -> ShortcutService:
    """A service pointed at a tmp data dir, optionally pre-seeded vdf."""
    data = tmp_path / "data"
    data.mkdir(exist_ok=True)
    sc_path = data / "shortcuts.vdf"
    with sc_path.open("wb") as f:
        f.write(vdf.binary_dumps({"shortcuts": shortcuts or {}}))
    return ShortcutService(
        EventBus(), str(sc_path), str(data / "games.map"),
        launcher_path=_LAUNCHER,
    )


def _rows_on_disk(svc: ShortcutService) -> dict:
    """Re-parse games.map from disk — never trust the in-memory dict."""
    from pathlib import Path

    path = Path(svc._games_map_path)
    if not path.is_file():
        return {}
    return parse_games_map(path.read_text(encoding="utf-8"))


def _epic_game(install, *, installed: bool = True, exe_path: str | None = None):
    """An Epic library game as sync produces it: no ``exe_path``."""
    return Game(
        app_id=0, store="epic", store_game_id="Potoo", title="Overcooked 2",
        installed=installed, install_path=str(install), exe_path=exe_path,
    )


# ── The repair itself ──────────────────────────────────────────────

def test_installed_game_with_no_row_is_repaired(tmp_path):
    """The headline case, end to end and through the on-disk file.

    The shortcut exists (so reconcile reports only ``kept``, which is
    also what gates persistence) but games.map is empty. Afterwards the
    row must be on disk carrying the shortcut's real appid.
    """
    install = _install_dir(tmp_path, "Overcooked2")
    appid = generate_app_id(_LAUNCHER, "epic:Potoo")
    svc = _service(tmp_path, {"0": _shortcut(appid, "epic:Potoo")})

    counts = asyncio.run(svc.reconcile([_epic_game(install)]))

    assert counts["repaired"] == 1
    assert counts["added"] == 0, "the shortcut already existed"
    row = _rows_on_disk(svc)["epic:Potoo"]
    assert row.app_id == appid
    assert row.exe == str(install / "Game.exe")
    assert row.work_dir == str(install)


def test_repaired_row_survives_a_sync_that_changed_nothing_else(tmp_path):
    """Persistence is the half that silently fails.

    ``reconcile`` only calls ``_save_all`` when added/removed/reclaimed
    are non-zero. A library that is otherwise stable reports pure
    ``kept``, so without counting repairs the rebuilt row would live
    and die in memory. Pinned by reading the file with a *fresh*
    service rather than the one that wrote it.
    """
    install = _install_dir(tmp_path, "Overcooked2")
    appid = generate_app_id(_LAUNCHER, "epic:Potoo")
    shortcuts = {"0": _shortcut(appid, "epic:Potoo")}
    svc = _service(tmp_path, shortcuts)
    counts = asyncio.run(svc.reconcile([_epic_game(install)]))
    assert (counts["added"], counts["removed"], counts["reclaimed"]) == (0, 0, 0)

    reopened = ShortcutService(
        EventBus(), svc._shortcuts_path, svc._games_map_path,
        launcher_path=_LAUNCHER,
    )
    entry = asyncio.run(reopened.get_entry_for_game_key("epic", "Potoo"))
    assert entry is not None, "the repaired row never reached disk"
    assert entry.app_id == appid


def test_repair_prefers_the_shortcut_appid_over_a_regenerated_one(tmp_path):
    """The row must carry the id Steam actually keyed the tile on.

    ``mark_installed`` reads the appid off the existing shortcut for
    this reason: a regenerated one diverges the moment the two sources
    disagree, and then every appid-keyed lookup — artwork, playtime,
    the ``CompatToolMapping`` entry this repair exists to reach —
    points at a shortcut that does not exist.

    Driven through ``repair_missing_rows`` rather than ``reconcile``
    on purpose. Reconcile's stale sweep deletes a shortcut whose appid
    is not the one the current library computes, so a divergence
    staged end-to-end never survives long enough to be observed —
    which is also why the preference is a safety net rather than a
    path the happy case takes.
    """
    install = _install_dir(tmp_path, "Overcooked2")
    steam_appid = -110954320
    assert steam_appid != generate_app_id(_LAUNCHER, "epic:Potoo")
    svc = _service(tmp_path, {"0": _shortcut(steam_appid, "epic:Potoo")})
    asyncio.run(svc._load_shortcuts())
    asyncio.run(svc._load_games_map())

    repaired = asyncio.run(repair_missing_rows(svc, [_epic_game(install)]))

    assert repaired == 1
    assert svc._games_map["epic:Potoo"].app_id == steam_appid


def test_repair_writes_a_row_even_with_no_executable_found(tmp_path):
    """An exe-less install still earns a row — the app_id is the payload.

    Also the Ubisoft case: those titles launch through the
    ``uplay://`` deeplink and legitimately have no resolvable exe, and
    the dispatcher reads the row's mere existence as the "installed"
    signal that routes Play to the game instead of reopening UPC.
    """
    install = _install_dir(tmp_path, "Ubi", exe=None)
    appid = generate_app_id(_LAUNCHER, "ubisoft:720")
    svc = _service(tmp_path, {"0": _shortcut(appid, "ubisoft:720")})
    game = Game(
        app_id=0, store="ubisoft", store_game_id="720", title="AC",
        installed=True, install_path=str(install),
    )

    assert asyncio.run(svc.reconcile([game]))["repaired"] == 1
    row = _rows_on_disk(svc)["ubisoft:720"]
    assert row.exe == ""
    assert row.app_id == appid
    assert row.work_dir == str(install)


def test_repair_prefers_a_native_start_sh_wrapper(tmp_path):
    """GOG's Linux builds launch through ``start.sh``, not a ``.exe``.

    ``exe_finder`` only ever returns ``.exe`` files, so without the
    explicit check the repaired row would point at some bundled Windows
    helper instead of the real entry point — the same rule
    ``dispatcher._resolve_exe_from_install`` follows.
    """
    install = _install_dir(tmp_path, "AbsoluteDrift")
    (install / "start.sh").write_text("#!/bin/sh\n")
    appid = generate_app_id(_LAUNCHER, "gog:1297999995")
    svc = _service(tmp_path, {"0": _shortcut(appid, "gog:1297999995")})
    game = Game(
        app_id=0, store="gog", store_game_id="1297999995", title="Drift",
        installed=True, install_path=str(install),
    )

    asyncio.run(svc.reconcile([game]))

    assert _rows_on_disk(svc)["gog:1297999995"].exe == str(install / "start.sh")


# ── What the repair must leave alone ───────────────────────────────

def test_existing_row_is_left_verbatim(tmp_path):
    """A healthy row is never rewritten by the repair pass.

    Load-bearing for "Change executable": that feature rewrites only
    the exe column and keeps ``work_dir`` deliberately decoupled, so a
    repair that re-derived either would silently undo the user's pick
    on the next sync.
    """
    install = _install_dir(tmp_path, "Overcooked2")
    appid = generate_app_id(_LAUNCHER, "epic:Potoo")
    svc = _service(tmp_path, {"0": _shortcut(appid, "epic:Potoo")})
    # First sync repairs the row; then the user picks their own exe.
    asyncio.run(svc.reconcile([_epic_game(install)]))
    assert asyncio.run(svc.set_executable("epic", "Potoo", "/custom/tool.exe"))

    counts = asyncio.run(svc.reconcile([_epic_game(install)]))

    assert counts["repaired"] == 0
    assert _rows_on_disk(svc)["epic:Potoo"].exe == "/custom/tool.exe"


def test_uninstalled_game_gets_no_row(tmp_path):
    """Owned-but-not-installed titles must stay out of games.map.

    The file is the launcher's installed-games manifest; a row for an
    uninstalled game makes ``prefix_bridge`` treat a nonexistent prefix
    as live and makes the Ubisoft dispatcher take the deeplink path for
    a game with nothing to launch.
    """
    install = _install_dir(tmp_path, "Overcooked2")
    appid = generate_app_id(_LAUNCHER, "epic:Potoo")
    svc = _service(tmp_path, {"0": _shortcut(appid, "epic:Potoo")})

    counts = asyncio.run(svc.reconcile([_epic_game(install, installed=False)]))

    assert counts["repaired"] == 0
    assert "epic:Potoo" not in _rows_on_disk(svc)


def test_vanished_install_dir_is_skipped(tmp_path):
    """A stale manifest entry must not be resurrected as a row.

    Ubisoft's install state comes from manifests that can outlive the
    files. Writing a row for one would mark a dead game installed —
    worse than the missing row, since the dispatcher would then route
    Play to it instead of to the installer.
    """
    appid = generate_app_id(_LAUNCHER, "ubisoft:720")
    svc = _service(tmp_path, {"0": _shortcut(appid, "ubisoft:720")})
    game = Game(
        app_id=0, store="ubisoft", store_game_id="720", title="AC",
        installed=True, install_path=str(tmp_path / "gone"),
    )

    counts = asyncio.run(svc.reconcile([game]))

    assert counts["repaired"] == 0
    assert "ubisoft:720" not in _rows_on_disk(svc)


def test_game_without_install_path_is_skipped(tmp_path):
    """No install dir means nothing to resolve — and nothing to write."""
    appid = generate_app_id(_LAUNCHER, "epic:Potoo")
    svc = _service(tmp_path, {"0": _shortcut(appid, "epic:Potoo")})
    game = Game(
        app_id=0, store="epic", store_game_id="Potoo", title="Overcooked 2",
        installed=True, install_path=None,
    )

    counts = asyncio.run(svc.reconcile([game]))

    assert counts["repaired"] == 0
    assert "epic:Potoo" not in _rows_on_disk(svc)


@pytest.mark.parametrize("store", ["gog", "ubisoft"])
def test_stores_that_supply_exe_path_need_no_repair(tmp_path, store):
    """GOG and Ubisoft scan the disk, so their rows are written normally.

    Pins that the repair pass is a fallback, not a second writer racing
    the phase-2 path: when ``exe_path`` arrives with the game, phase 2
    writes the row and the repair finds nothing to do.
    """
    install = _install_dir(tmp_path, "Scanned")
    exe = str(install / "Game.exe")
    appid = generate_app_id(_LAUNCHER, f"{store}:42")
    svc = _service(tmp_path, {"0": _shortcut(appid, f"{store}:42")})
    game = Game(
        app_id=0, store=store, store_game_id="42", title="Scanned",
        installed=True, install_path=str(install), exe_path=exe,
    )

    counts = asyncio.run(svc.reconcile([game]))

    assert counts["repaired"] == 0
    assert _rows_on_disk(svc)[f"{store}:42"].exe == exe
