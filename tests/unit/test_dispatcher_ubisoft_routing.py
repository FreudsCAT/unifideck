"""Regression: an *installed* Ubisoft title must launch the game, not UPC.

Ubisoft games launch via the ``uplay://launch/{id}/0`` deeplink in
``ubisoft_launch``, which ignores ``exe_path`` — so an installed title
legitimately has an empty ``exe`` in games.map. A prior fix (commit bc27c05)
added a "no exe + populated prefix → open UPC to install" fallback *inside*
the ``if not exe:`` branch, which then fired for installed titles too: clicking
**Play** re-opened Ubisoft Connect instead of launching the game.

These tests pin the corrected routing in ``dispatcher._build_context``:

* installed (a games.map row exists, exe empty)  → play context (deeplink);
* not installed (no row) but a bootstrapped prefix → install context (UPC);
* not installed and no prefix                     → ``GameNotFoundError``.
"""
from __future__ import annotations

import types

import pytest

from unifideck.launcher import dispatcher as d


class _FakeShortcutSvc:
    def __init__(self, entry: object | None) -> None:
        self._entry = entry

    async def get_entry_for_game_key(self, _store: str, _game_id: str) -> object | None:
        return self._entry


def _argv(game_key: str = "ubisoft:100") -> list[str]:
    return ["launcher", game_key]


@pytest.fixture(autouse=True)
def _hermetic(monkeypatch, tmp_path):
    # No special-action env token → plain launch path.
    monkeypatch.delenv("UNIFIDECK_UBISOFT_ACTION", raising=False)
    # Keep exe re-resolution and library-cache lookup from touching the
    # real filesystem / a dev machine's Ubisoft data.
    monkeypatch.setattr(d, "_resolve_exe_from_install", lambda *a, **k: None)
    monkeypatch.setattr(d, "_install_path_from_cache", lambda *a, **k: "")
    monkeypatch.setattr(d, "_resolve_plugin_dir", lambda: tmp_path)


@pytest.mark.asyncio
async def test_installed_ubisoft_empty_exe_routes_to_play(monkeypatch):
    """A games.map row (installed) with empty exe → play, even with a prefix."""
    # A populated prefix exists, but that must NOT divert an installed game.
    monkeypatch.setattr(d, "wrapper_prefix_is_populated", lambda _s, _gid: True)
    svc = _FakeShortcutSvc(
        types.SimpleNamespace(exe="", work_dir="/games/bge", app_id=1),
    )

    ctx = await d._build_context(_argv(), svc)

    assert ctx.store == "ubisoft"
    assert ctx.is_launch_action is True  # play, not a UPC action
    assert ctx.action is None
    assert ctx.auth_store is None


@pytest.mark.asyncio
async def test_uninstalled_ubisoft_with_prefix_routes_to_install(monkeypatch):
    """No games.map row but a bootstrapped prefix → open UPC to install."""
    monkeypatch.setattr(d, "wrapper_prefix_is_populated", lambda _s, _gid: True)
    svc = _FakeShortcutSvc(None)

    ctx = await d._build_context(_argv(), svc)

    assert ctx.is_launch_action is False
    assert ctx.action == "install"


@pytest.mark.asyncio
async def test_uninstalled_ubisoft_no_prefix_raises(monkeypatch):
    """No row and no prefix → genuinely-missing game still errors."""
    monkeypatch.setattr(d, "wrapper_prefix_is_populated", lambda _s, _gid: False)
    svc = _FakeShortcutSvc(None)

    with pytest.raises(d.GameNotFoundError):
        await d._build_context(_argv(), svc)
