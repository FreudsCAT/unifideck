"""Regression: a Ubisoft install must not sit on "INSTALLING UBISOFT CONNECT".

Field report (Rayman Origins). The install reached::

    [UbisoftInstaller] awaiting UPC launch via frontend RunGame
    [DownloadWorker] requested UPC launch for ubisoft:80

and the UI showed "INSTALLING UBISOFT CONNECT / Follow the Ubisoft Connect
window to finish installing…" indefinitely. Nothing was logged after that
point, and no upc.exe was ever running.

TWO defects combined.

1. The launcher died before opening UPC. ``RunGame`` invoked
   ``unifideck-launcher ubisoft:80``, whose ``_build_context`` looks the game
   up in games.map. There was no row (normal mid-install), so it fell through
   to ``wrapper_prefix_is_populated`` — which is exactly the escape hatch
   for this case — but that hand-built ``<prefix_root>/drive_c/…`` while a
   Proton prefix keeps its C: drive at ``<prefix_root>/pfx/drive_c``. It
   returned False for a fully-populated prefix and the launcher raised
   ``GameNotFoundError`` (exit 2) instantly. The game's prefix was the custom
   ``~/Games/prefixes/ubisoft/80`` recorded in ``ubisoft_id_map.json``, so
   the recorded-path branch was in play, not the default.

2. Nothing noticed. The poll loop's abandonment watchdog was gated on
   ``window_ever_seen``, so when UPC never appears at all that flag stays
   False and the loop runs its full two-hour timeout with no diagnosis.

The watchdog has since been generalized into the shared wrapper-store install
watcher (``stores/shared/wrapper_install``) and is keyed on the client
*process*, not a window: the window probe could not see into Gaming Mode's
separate gamescope session, so it never fired there at all.
"""
from __future__ import annotations

import json

import pytest

from unifideck.launcher import dispatcher
from unifideck.stores.shared.wrapper_install import watch as watch_mod
from unifideck.stores.ubisoft.installer import manual_ui_poll as poll_mod

_UPC_REL = "Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"


# ── defect 1: prefix detection missed the pfx/ level ────────────


@pytest.fixture
def id_map(tmp_path, monkeypatch):
    """Isolated HOME so ubisoft_id_map.json lookups are sandboxed."""
    monkeypatch.setenv("HOME", str(tmp_path))
    path = tmp_path / ".local" / "share" / "unifideck"
    path.mkdir(parents=True)

    def _write(entries):
        (path / "ubisoft_id_map.json").write_text(json.dumps(entries))

    return _write


def _populate(prefix_root, *, layout):
    """Create a prefix with upc.exe under the given C:-drive layout."""
    drive_c = prefix_root / "pfx" / "drive_c" if layout == "modern" \
        else prefix_root / "drive_c"
    upc = drive_c / _UPC_REL
    upc.parent.mkdir(parents=True)
    upc.write_text("")
    return prefix_root


def test_modern_pfx_layout_is_detected(id_map, tmp_path):
    """The reported bug: <root>/pfx/drive_c was invisible to the old check."""
    root = _populate(tmp_path / "Games" / "prefixes" / "ubisoft" / "80",
                     layout="modern")
    id_map({"80": {"name": "Rayman Origins", "prefix_path": str(root)}})

    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is True


def test_legacy_drive_c_layout_still_detected(id_map, tmp_path):
    """Very old prefixes keep drive_c at the root — must not regress."""
    root = _populate(tmp_path / "old" / "80", layout="legacy")
    id_map({"80": {"name": "X", "prefix_path": str(root)}})

    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is True


def test_default_location_prefix_is_detected(id_map, tmp_path):
    """No recorded prefix_path — fall back to the internal default root."""
    root = _populate(
        tmp_path / ".local" / "share" / "unifideck" / "prefixes" / "ubisoft" / "80",
        layout="modern",
    )
    assert root.exists()
    id_map({"80": {"name": "X"}})

    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is True


def test_prefix_without_upc_is_not_populated(id_map, tmp_path):
    """An empty prefix must still raise GameNotFoundError upstream."""
    root = tmp_path / "empty" / "80"
    (root / "pfx" / "drive_c").mkdir(parents=True)
    id_map({"80": {"name": "X", "prefix_path": str(root)}})

    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is False


def test_game_absent_from_id_map_is_not_populated(id_map):
    id_map({"99": {"name": "Other"}})
    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is False


def test_missing_or_corrupt_id_map_is_not_populated(id_map, tmp_path):
    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is False
    (tmp_path / ".local" / "share" / "unifideck"
     / "ubisoft_id_map.json").write_text("{bad json")
    assert dispatcher.wrapper_prefix_is_populated("ubisoft", "80") is False


# ── defect 2: no bail-out when UPC never starts ─────────────────
#
# The watchdog now lives in the shared wrapper-store watcher, so these assert
# against that — with Ubisoft's probe, which is what the install actually runs.


def _watch(monkeypatch, *, alive: bool) -> watch_mod._Watch:
    """A watch over Ubisoft's probe with a scripted liveness answer."""
    state = {"alive": alive}
    monkeypatch.setattr(
        watch_mod, "install_alive", lambda _store, _prefix: state["alive"],
    )
    w = watch_mod._Watch(
        poll_mod.UbisoftInstallProbe("/base", "/prefix"), "/prefix", None,
    )
    w._state = state  # type: ignore[attr-defined]
    return w


def _polls_for(seconds: float, watch: watch_mod._Watch) -> int:
    return int(seconds / watch._poll)


def test_gives_up_after_the_grace_with_no_upc_process(monkeypatch):
    watch = _watch(monkeypatch, alive=False)
    give_up = False
    for _ in range(_polls_for(watch_mod.NEVER_STARTED_GRACE_S, watch)):
        give_up = watch._abandoned()

    assert give_up is True, "must stop waiting once UPC clearly never started"


def test_does_not_give_up_before_the_grace(monkeypatch):
    """A cold UPC start under Proton is slow — never cut it short."""
    watch = _watch(monkeypatch, alive=False)
    give_up = False
    for _ in range(_polls_for(watch_mod.NEVER_STARTED_GRACE_S, watch) - 1):
        give_up = watch._abandoned()

    assert give_up is False


def test_a_live_upc_process_resets_the_counter(monkeypatch):
    """UPC merely slow to draw its window must not trip the bail-out."""
    watch = _watch(monkeypatch, alive=False)
    for _ in range(_polls_for(watch_mod.NEVER_STARTED_GRACE_S, watch) - 1):
        watch._abandoned()

    watch._state["alive"] = True
    assert watch._abandoned() is False
    assert watch._absent_for == 0.0


def test_grace_is_well_inside_the_overall_timeout():
    """The point is failing fast — minutes, not the two-hour backstop."""
    assert watch_mod.NEVER_STARTED_GRACE_S <= 300, "minutes, not hours"
    assert watch_mod.NEVER_STARTED_GRACE_S < watch_mod.INSTALL_TIMEOUT_S
