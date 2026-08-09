"""Regression: ``UNIFIDECK_BATTLENET_ACTION=install`` must reach the handler.

``_detect_special_action`` gated the ``install`` action on a literal
``candidate_store == "ubisoft"``. Battle.net therefore fell through to the
normal-launch path, looked for a games.map row that by definition does not
exist yet (the game is not installed — that is the point of the action), and
raised ``GameNotFoundError``. ``battlenet_install_launch`` was unreachable
dead code, so no Battle.net game could ever be installed.

``LauncherService._handle_auth_path`` picks the handler off ``ctx.auth_store``,
so the context must carry the *real* wrapper store rather than a hardcoded
``"ubisoft"`` — otherwise a Battle.net install would open Ubisoft Connect.
"""
from __future__ import annotations

import pytest

from unifideck.launcher import dispatcher as d


class _FakeShortcutSvc:
    """No games.map row: the install action fires before this is consulted."""

    async def get_entry_for_game_key(self, _store: str, _game_id: str) -> None:
        return None


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    for store in ("EPIC", "GOG", "AMAZON", "MICROSOFT", "UBISOFT", "BATTLENET"):
        monkeypatch.delenv(f"UNIFIDECK_{store}_ACTION", raising=False)
    monkeypatch.setattr(d, "_resolve_exe_from_install", lambda *a, **k: None)
    monkeypatch.setattr(d, "_install_path_from_cache", lambda *a, **k: "")
    monkeypatch.setattr(d, "_resolve_plugin_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_battlenet_install_action_routes_to_the_install_context(monkeypatch):
    monkeypatch.setenv("UNIFIDECK_BATTLENET_ACTION", "install")

    ctx = await d._build_context(["launcher", "battlenet:s1"], _FakeShortcutSvc())

    assert ctx.is_launch_action is False
    assert ctx.action == "install"
    # The handler is selected off auth_store; "ubisoft" here would open the
    # wrong vendor client entirely.
    assert ctx.auth_store == "battlenet"
    assert ctx.game_id == "s1", "the real uid must survive — it resolves the family"


@pytest.mark.asyncio
async def test_battlenet_auth_action_still_routes_to_auth(monkeypatch):
    monkeypatch.setenv("UNIFIDECK_BATTLENET_ACTION", "auth")

    ctx = await d._build_context(
        ["launcher", "battlenet:bnet-auth"], _FakeShortcutSvc(),
    )

    assert ctx.action == "auth"
    assert ctx.auth_store == "battlenet"


@pytest.mark.asyncio
async def test_install_is_refused_for_a_non_wrapper_store(monkeypatch):
    """Only wrapper stores have a vendor client to open.

    Epic installs through legendary, so an ``install`` token there is
    meaningless and must not short-circuit the launch path.
    """
    monkeypatch.setenv("UNIFIDECK_EPIC_ACTION", "install")

    with pytest.raises(d.GameNotFoundError):
        await d._build_context(["launcher", "epic:abc123"], _FakeShortcutSvc())


def test_the_wrapper_predicate_is_the_gate() -> None:
    """Pin the mechanism, so a future store is added in one place."""
    assert d.is_wrapper_store("battlenet") is True
    assert d.is_wrapper_store("ubisoft") is True
    assert d.is_wrapper_store("epic") is False
