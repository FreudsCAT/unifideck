"""Tests for ``prefix_setup._pin_final_tool`` — the recovery pin.

When ``setup_prefix`` recovers a hanging Proton by falling back to the managed
GE-Proton, it pins GE for that game so the NEXT launch resolves GE directly
(tier 1 of ``select_proton_version``) instead of re-picking the user's hanging
global-default, seeing a "Proton family change" against the GE-built prefix, and
wiping + rebuilding it at Play time (the observed Rise-of-the-Tomb-Raider redo).

The pin has two halves, both asserted here:
  1. re-stamp the prefix's ``.unifideck_proton_version`` marker (so
     ``ensure_prefix_initialized`` sees no family change next launch), and
  2. write the per-game entry into ``proton_settings.json`` via
     ``save_proton_setting`` (the tier-1 lookup the selector honours).

``save_proton_setting`` lives in the aiohttp-heavy ``compatibility`` package and
is imported lazily inside the function (the launcher must stay stdlib-safe under
system Python) — so it's patched by module path, not by attribute on
``prefix_setup``.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from unifideck.launcher.proton import prefix_setup as setup_mod
from unifideck.launcher.proton.compat.prefix_init import _MARKER_NAME


@pytest.fixture
def _isolated_prefixes(tmp_path, monkeypatch):
    """Redirect the prefixes root at ~/.local/share into a tmp dir.

    ``_pin_final_tool`` computes the prefix root from ``Path("~/.local/share/
    unifideck/prefixes").expanduser()``; point HOME at tmp so the marker write
    never touches real user data.
    """
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _ctx():
    return SimpleNamespace(game_id="123", game_key="gog:123")


def test_pin_writes_marker_and_saves_setting(_isolated_prefixes, monkeypatch):
    saved = MagicMock(return_value={"success": True})
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting", saved,
    )

    setup_mod._pin_final_tool(_ctx(), "GE-Proton11-1")

    # 1. the prefix marker is (re-)stamped to the pinned tool
    marker = _isolated_prefixes / ".local/share/unifideck/prefixes/123" / _MARKER_NAME
    assert marker.read_text(encoding="utf-8") == "GE-Proton11-1"
    # 2. the per-game pin is persisted (tier-1 of the selector)
    saved.assert_called_once_with("gog:123", "GE-Proton11-1")


def test_pin_survives_save_failure(_isolated_prefixes, monkeypatch):
    # A failed save must never raise — the prefix is already built; worst case
    # is a redo next launch, not a broken install/launch.
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting",
        MagicMock(side_effect=RuntimeError("boom")),
    )

    # Must not raise.
    setup_mod._pin_final_tool(_ctx(), "GE-Proton11-1")

    # The marker still got written before the save attempt.
    marker = _isolated_prefixes / ".local/share/unifideck/prefixes/123" / _MARKER_NAME
    assert marker.read_text(encoding="utf-8") == "GE-Proton11-1"
