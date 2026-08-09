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
    """Point HOME at a tmp dir so nothing here can touch real user data."""
    monkeypatch.setenv("HOME", str(tmp_path))
    return tmp_path


def _ctx(game_key="gog:123"):
    return SimpleNamespace(game_id="123", game_key=game_key)


def _state(prefix_path):
    """The launch state ``proton_prepare`` populates."""
    return SimpleNamespace(prefix_path=prefix_path)


def test_pin_writes_marker_and_saves_setting(_isolated_prefixes, monkeypatch):
    saved = MagicMock(return_value={"success": True})
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting", saved,
    )
    prefix = _isolated_prefixes / ".local/share/unifideck/prefixes/123"

    setup_mod._pin_final_tool(_ctx(), _state(prefix), "GE-Proton11-1")

    # 1. the prefix marker is (re-)stamped to the pinned tool
    assert (prefix / _MARKER_NAME).read_text(encoding="utf-8") == "GE-Proton11-1"
    # 2. the per-game pin is persisted (tier-1 of the selector)
    saved.assert_called_once_with("gog:123", "GE-Proton11-1")


def test_pin_stamps_the_resolved_prefix_not_the_default_layout(
    _isolated_prefixes, monkeypatch,
):
    """A Ubisoft prefix under a user-picked base gets the marker, not a stray dir.

    Regression: the root used to be rebuilt as
    ``~/.local/share/unifideck/prefixes/<game_id>``, which is right for every
    store except Ubisoft — whose path comes from ``ubisoft_id_map.json``. That
    stamped ``prefixes/80``, a directory no launch opens, while the real prefix
    kept its stale marker and got wiped again on the next launch.
    """
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting", MagicMock(),
    )
    real = _isolated_prefixes / "Games/prefixes/ubisoft/80"
    real.mkdir(parents=True)

    setup_mod._pin_final_tool(
        _ctx("ubisoft:80"), _state(real), "GE-Proton11-3",
    )

    assert (real / _MARKER_NAME).read_text(encoding="utf-8") == "GE-Proton11-3"
    # and nothing was created under the generic layout
    assert not (_isolated_prefixes / ".local/share/unifideck/prefixes/80").exists()


def test_pin_normalizes_a_pfx_suffixed_prefix(_isolated_prefixes, monkeypatch):
    """``<root>/pfx`` stamps ``<root>`` — the marker lives at the prefix root."""
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting", MagicMock(),
    )
    root = _isolated_prefixes / "Games/prefixes/ubisoft/80"
    (root / "pfx").mkdir(parents=True)

    setup_mod._pin_final_tool(_ctx("ubisoft:80"), _state(root / "pfx"), "GE-Proton11-3")

    assert (root / _MARKER_NAME).read_text(encoding="utf-8") == "GE-Proton11-3"


def test_pin_saves_setting_when_prefix_unresolved(_isolated_prefixes, monkeypatch):
    """No resolved prefix → skip the marker, still write the per-game pin."""
    saved = MagicMock(return_value={"success": True})
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting", saved,
    )

    setup_mod._pin_final_tool(_ctx(), _state(None), "GE-Proton11-1")

    saved.assert_called_once_with("gog:123", "GE-Proton11-1")


def test_pin_survives_save_failure(_isolated_prefixes, monkeypatch):
    # A failed save must never raise — the prefix is already built; worst case
    # is a redo next launch, not a broken install/launch.
    monkeypatch.setattr(
        "unifideck.compatibility.proton_helpers.save_proton_setting",
        MagicMock(side_effect=RuntimeError("boom")),
    )
    prefix = _isolated_prefixes / ".local/share/unifideck/prefixes/123"

    # Must not raise.
    setup_mod._pin_final_tool(_ctx(), _state(prefix), "GE-Proton11-1")

    # The marker still got written before the save attempt.
    assert (prefix / _MARKER_NAME).read_text(encoding="utf-8") == "GE-Proton11-1"
