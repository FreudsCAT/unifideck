"""Persistent auth shortcuts for wrapper stores.

This is what makes sign-in work **in Gaming Mode**. Desktop Mode can spawn
the vendor client directly, but on the deck a process with no Steam
shortcut gets no gamescope session and its window never renders — which is
exactly how this presented when it was missing: login worked on the desktop
and silently did nothing on the deck.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from unifideck.stores.shared.auth_shortcut import (
    AuthShortcutSpec,
    build_context,
    ensure_auth_shortcut,
    find_in_vdf,
    launcher_path_for,
)

BATTLENET = AuthShortcutSpec(
    store="battlenet",
    store_game_id="battlenet:bnet-auth",
    display_name="Battle.net",
    action_env="UNIFIDECK_BATTLENET_ACTION",
)


class _ShortcutService:
    """Minimal stand-in for ShortcutService."""

    def __init__(self, shortcuts: dict[str, Any] | None = None, *, appid: int = -12345) -> None:
        self.data: dict[str, Any] = {"shortcuts": shortcuts or {}}
        self.writes = 0
        self._appid = appid

    def generate_app_id(self, launcher: str, identity: str) -> int:
        del launcher, identity
        return self._appid

    async def read_shortcuts(self) -> dict[str, Any]:
        return self.data

    async def write_shortcuts(self, data: dict[str, Any]) -> None:
        self.data = data
        self.writes += 1


def test_creates_a_shortcut_when_none_exists() -> None:
    sm = _ShortcutService()
    appid = asyncio.run(ensure_auth_shortcut(sm, BATTLENET, "/plugin"))
    assert appid == -12345 + 2**32
    assert sm.writes == 1
    entry = next(iter(sm.data["shortcuts"].values()))
    assert entry["AppName"] == "Battle.net"
    assert "battlenet:bnet-auth" in entry["LaunchOptions"]
    assert "UNIFIDECK_BATTLENET_ACTION=auth" in entry["LaunchOptions"]


def test_the_shortcut_is_hidden() -> None:
    """It is infrastructure, not a tile the user should browse."""
    sm = _ShortcutService()
    asyncio.run(ensure_auth_shortcut(sm, BATTLENET, "/plugin"))
    assert next(iter(sm.data["shortcuts"].values()))["IsHidden"] == 1


def test_an_existing_shortcut_is_reused_not_duplicated() -> None:
    """Re-running sign-in must not litter the library with copies."""
    sm = _ShortcutService()
    first = asyncio.run(ensure_auth_shortcut(sm, BATTLENET, "/plugin"))
    writes_after_first = sm.writes
    second = asyncio.run(ensure_auth_shortcut(sm, BATTLENET, "/plugin"))
    assert first == second
    assert sm.writes == writes_after_first
    assert len(sm.data["shortcuts"]) == 1


def test_it_is_added_alongside_existing_shortcuts(tmp_path: Any) -> None:
    sm = _ShortcutService({"0": {"AppName": "Something", "LaunchOptions": "epic:1"}})
    asyncio.run(ensure_auth_shortcut(sm, BATTLENET, "/plugin"))
    assert len(sm.data["shortcuts"]) == 2
    assert sm.data["shortcuts"]["0"]["AppName"] == "Something"


def test_find_in_vdf_matches_on_the_store_game_id() -> None:
    shortcuts = {
        "0": {"appid": 7, "LaunchOptions": "ubisoft:upc-auth UNIFIDECK_UBISOFT_ACTION=auth"},
        "1": {"appid": 9, "LaunchOptions": "battlenet:bnet-auth UNIFIDECK_BATTLENET_ACTION=auth"},
    }
    assert find_in_vdf(shortcuts, BATTLENET) == 9


def test_a_missing_shortcut_service_degrades_to_none() -> None:
    """The frontend then falls back to a temporary shortcut."""
    assert asyncio.run(ensure_auth_shortcut(None, BATTLENET, "/plugin")) is None


def test_a_broken_vdf_does_not_break_sign_in() -> None:
    class _Exploding(_ShortcutService):
        async def read_shortcuts(self) -> dict[str, Any]:
            raise OSError("vdf unreadable")

    assert asyncio.run(ensure_auth_shortcut(_Exploding(), BATTLENET, "/plugin")) is None


# --------------------------------------------------------------------------
# the context the frontend consumes
# --------------------------------------------------------------------------


def test_context_carries_everything_rungame_needs() -> None:
    sm = _ShortcutService()
    ctx = asyncio.run(build_context(sm, BATTLENET, "/plugin"))
    assert ctx["success"] is True
    assert ctx["appid_unsigned"] > 0
    assert ctx["launcher_path"].endswith("bin/unifideck-launcher")
    assert ctx["launch_wait_ms"] > 0


def test_launcher_path_is_returned_even_on_failure() -> None:
    """Without it the frontend cannot even fall back to a temp shortcut."""
    ctx = asyncio.run(build_context(None, BATTLENET, "/plugin"))
    assert ctx["success"] is False
    assert ctx["launcher_path"].endswith("bin/unifideck-launcher")


def test_launcher_path_resolves_without_a_plugin_dir() -> None:
    assert launcher_path_for(None).endswith("bin/unifideck-launcher")


def test_launch_options_are_byte_stable() -> None:
    """Changing this orphans every existing shortcut until rewritten."""
    assert (
        BATTLENET.launch_options("/anything")
        == "battlenet:bnet-auth UNIFIDECK_BATTLENET_ACTION=auth"
    )


def test_the_spec_is_generic_over_stores() -> None:
    """EA App should be a spec, not another module."""
    ea = AuthShortcutSpec(
        store="ea",
        store_game_id="ea:ea-auth",
        display_name="EA App",
        action_env="UNIFIDECK_EA_ACTION",
    )
    sm = _ShortcutService()
    asyncio.run(ensure_auth_shortcut(sm, ea, "/plugin"))
    entry = next(iter(sm.data["shortcuts"].values()))
    assert entry["AppName"] == "EA App"
    assert entry["LaunchOptions"] == "ea:ea-auth UNIFIDECK_EA_ACTION=auth"


# --------------------------------------------------------------------------
# the launch options must survive the parser that reads them back
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "spec",
    [
        BATTLENET,
        AuthShortcutSpec(
            store="ubisoft",
            store_game_id="ubisoft:upc-auth",
            display_name="Ubisoft Connect",
            action_env="UNIFIDECK_UBISOFT_ACTION",
        ),
    ],
    ids=["battlenet", "ubisoft"],
)
def test_store_id_is_recoverable_from_the_launch_options(spec: AuthShortcutSpec) -> None:
    """The regression that made the tile do nothing.

    ``STORE_ID_PATTERN`` did not list ``battlenet``, so ``get_full_id``
    returned None, the context resolved to ``(0, "")`` and RunGame never
    fired — a Connect button that silently did nothing.
    """
    from unifideck.services.shortcut.launch_options import get_full_id

    assert get_full_id(spec.launch_options("/l")) == spec.store_game_id
