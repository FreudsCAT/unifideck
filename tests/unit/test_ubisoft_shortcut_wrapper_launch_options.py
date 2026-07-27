"""Shortcut LaunchOptions lookups must tolerate a wrapper prefix.

Reproduces a "Install / Play does nothing" report: a real tester's
shortcuts.vdf had ``LaunchOptions = "~/proton-launch %command%
ubisoft:<uuid>"`` (a wrapper written by the decky-proton-launch plugin,
prepended per Steam's own ``%command%`` convention). Two spots assumed
the store id was always the FIRST whitespace token of ``LaunchOptions``
and returned "no match" the moment a wrapper pushed it further along:

* ``AuthShortcutsRPCMixin._resolve_shortcut_entry`` — used by
  ``get_compat_tool_for_game``, the RPC the Ubisoft install/launch flow
  calls to resolve the shortcut's appid before ``RunGame``. Returning
  appid=0 makes the frontend bail with "Context unavailable" — RunGame
  never fires, no UPC window ever appears, the install silently stalls.
* ``_AuthShortcut.extract_store_id`` — used to recognise the persistent
  "Ubisoft Connect" auth shortcut during validation/lookup.

Both now delegate to the shared, wrapper-tolerant
``services.shortcut.launch_options.get_full_id`` (regex, word-boundary
anchored, already used elsewhere for exactly this reason) instead of
``split(maxsplit=1)[0]``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import vdf

from unifideck.rpc.mixins.auth_shortcuts import AuthShortcutsRPCMixin
from unifideck.stores.ubisoft.auth.shortcut import _AuthShortcut

_WRAPPED_LAUNCH_OPTIONS = (
    "~/proton-launch %command% ubisoft:32f4b40b-b657-420d-a046-a0a18331c7f9"
)
_STORE_GAME_ID = "ubisoft:32f4b40b-b657-420d-a046-a0a18331c7f9"


def _write_shortcuts_vdf(steam_root: Path, user: str, entries: dict) -> None:
    cfg_dir = steam_root / "userdata" / user / "config"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "shortcuts.vdf").write_bytes(
        vdf.binary_dumps({"shortcuts": entries}),  # type: ignore[no-untyped-call]
    )


def test_resolve_shortcut_entry_finds_appid_behind_a_wrapper_prefix(tmp_path):
    """A %command%-wrapped LaunchOptions must still resolve the real appid.

    Before the fix this returned (0, "") — exactly what bundle logs showed
    (``compat tool for ubisoft:<id>: appid=0``), which the frontend treats
    as "no shortcut" and never calls RunGame.
    """
    steam_root = tmp_path / "steam"
    _write_shortcuts_vdf(
        steam_root,
        "12345",
        {
            "0": {
                "appid": -1243858682,
                "AppName": "Assassin's Creed Shadows",
                "LaunchOptions": _WRAPPED_LAUNCH_OPTIONS,
            },
        },
    )

    with (
        patch(
            "unifideck.steam.library.find_steam_path",
            return_value=str(steam_root),
        ),
        patch(
            "unifideck.steam.steam_user.get_active_steam_user",
            return_value="12345",
        ),
    ):
        appid, launch_options = AuthShortcutsRPCMixin._resolve_shortcut_entry(
            _STORE_GAME_ID,
        )

    assert appid != 0, "must resolve a real appid even with a wrapper prefix"
    assert appid == -1243858682 + 2**32
    assert launch_options == _WRAPPED_LAUNCH_OPTIONS


def test_resolve_shortcut_entry_still_returns_zero_for_no_match(tmp_path):
    """Sanity: an unrelated shortcut must not false-positive."""
    steam_root = tmp_path / "steam"
    _write_shortcuts_vdf(
        steam_root,
        "12345",
        {
            "0": {
                "appid": -111,
                "AppName": "Beastmaster",
                "LaunchOptions": "ubisoft:2704",
            },
        },
    )

    with (
        patch(
            "unifideck.steam.library.find_steam_path",
            return_value=str(steam_root),
        ),
        patch(
            "unifideck.steam.steam_user.get_active_steam_user",
            return_value="12345",
        ),
    ):
        appid, launch_options = AuthShortcutsRPCMixin._resolve_shortcut_entry(
            _STORE_GAME_ID,
        )

    assert (appid, launch_options) == (0, "")


def test_extract_store_id_finds_id_behind_a_wrapper_prefix():
    """_AuthShortcut.extract_store_id must also tolerate a wrapper prefix."""
    assert _AuthShortcut.extract_store_id(_WRAPPED_LAUNCH_OPTIONS) == _STORE_GAME_ID


def test_extract_store_id_empty_for_no_launch_options():
    assert _AuthShortcut.extract_store_id("") == ""
