"""A GE recovery must not quietly overwrite the user's Proton choice.

``_pin_final_tool`` writes two things after the GE hang-recovery ladder wins:
the prefix marker (per-prefix fact — "GE built this", and what stops the
wipe-and-rebuild loop) and a per-game entry in ``proton_settings.json``.

The second one is wrong whenever the user has a live Steam Force-Compat entry
for the game. ``selector.select_proton_version`` reads ``config.vdf`` as tier 1
and ``proton_settings.json`` as tier 2, so with an entry present the pin can
never take effect — its only observable consequence is that when the user later
clears Force Compatibility they silently land on GE with nothing explaining
why. Pure side effect, no upside.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from unifideck.launcher.proton import prefix_setup as ps


@pytest.fixture
def _isolated_prefixes(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path / ".local" / "share" / "unifideck" / "prefixes"


@pytest.fixture
def _save_spy(monkeypatch):
    spy = MagicMock()
    import unifideck.compatibility.proton_helpers as helpers
    monkeypatch.setattr(helpers, "save_proton_setting", spy)
    return spy


def _ctx(app_id: str | None):
    return SimpleNamespace(
        store="epic", game_id="g1", game_key="epic:g1", steam_app_id=app_id,
    )


def test_pin_is_skipped_when_force_compat_is_live(
    _isolated_prefixes, _save_spy, monkeypatch,
):
    from unifideck.launcher.proton.infrastructure import selector
    monkeypatch.setattr(
        selector, "get_steam_compat_tool_override", lambda _a: "proton_9",
    )

    ps._pin_final_tool(_ctx("4179000979"), "GE-Proton11-3")

    _save_spy.assert_not_called()
    # The marker still records what actually built the prefix.
    marker = _isolated_prefixes / "g1" / ".unifideck_proton_version"
    assert marker.read_text() == "GE-Proton11-3"


def test_pin_is_written_when_there_is_no_force_compat(
    _isolated_prefixes, _save_spy, monkeypatch,
):
    from unifideck.launcher.proton.infrastructure import selector
    monkeypatch.setattr(
        selector, "get_steam_compat_tool_override", lambda _a: None,
    )

    ps._pin_final_tool(_ctx("4179000979"), "GE-Proton11-3")

    _save_spy.assert_called_once_with("epic:g1", "GE-Proton11-3")


def test_pin_is_written_when_the_shortcut_has_no_appid(
    _isolated_prefixes, _save_spy,
):
    """No AppID → no Force-Compat entry can exist; behave as before."""
    ps._pin_final_tool(_ctx(None), "GE-Proton11-3")

    _save_spy.assert_called_once_with("epic:g1", "GE-Proton11-3")


def test_lookup_failure_falls_back_to_pinning(
    _isolated_prefixes, _save_spy, monkeypatch,
):
    """Fail closed: a broken lookup must not silently drop the pin."""
    from unifideck.launcher.proton.infrastructure import selector

    def _boom(_a):
        raise OSError("config.vdf unreadable")

    monkeypatch.setattr(selector, "get_steam_compat_tool_override", _boom)

    ps._pin_final_tool(_ctx("4179000979"), "GE-Proton11-3")

    _save_spy.assert_called_once_with("epic:g1", "GE-Proton11-3")
