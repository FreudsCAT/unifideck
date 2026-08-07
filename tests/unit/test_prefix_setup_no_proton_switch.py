"""Regression: only a HANG may switch the Proton the prefix is built with.

``setup_prefix`` used to run a static pre-check — "does this Proton ship
``protonfixes/``?" — and, when it didn't, build the entire prefix under
managed GE-Proton instead. Every official Valve Proton fails that check (umu
execs the winetricks verb with ``cwd=$PROTONPATH/protonfixes``), so every
Proton a user forced in Steam → Properties → Compatibility got its prefix
created, compat-installed and version-stamped by GE while the launch plan —
frozen earlier — still pointed at their pick. A Wine prefix is single-Proton
state, and that split is what stopped the games from starting.

The capability of one optional step was never evidence about which Proton the
user wants. It now skips that step (see
test_winetricks_skips_incapable_proton.py) and leaves the Proton alone; the
GE ladder is reserved for a genuine runtime hang, which these tests keep.

Also pinned here: ``setup_prefix`` publishes the winning ``(path, tool)`` onto
the caller's state as an explicit output, instead of ``_run_one`` leaving
whichever attempt ran last behind as a side effect.
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.launcher import proton as proton_pkg
from unifideck.launcher.proton import prefix_setup as setup_mod
from unifideck.launcher.proton.compat import prefix_init as prefix_init_mod


def _ctx():
    return SimpleNamespace(
        store="epic", game_id="g1", game_key="epic:g1", steam_app_id=None,
    )


@pytest.fixture
def wired(monkeypatch):
    monkeypatch.setattr(
        proton_pkg, "find_python_3_10_plus", lambda: "/usr/bin/python3",
    )
    monkeypatch.setattr(
        proton_pkg, "proton_prepare",
        lambda ctx, state, **kw: SimpleNamespace(
            tool_id=kw["proton_tool_id"], env={},
        ),
    )
    monkeypatch.setattr(
        prefix_init_mod, "ensure_prefix_initialized", AsyncMock(),
    )
    pin = MagicMock()
    monkeypatch.setattr(setup_mod, "_pin_final_tool", pin)
    return SimpleNamespace(pin=pin)


def _official_proton(tmp_path, name):
    """An official Valve Proton: no ``protonfixes/`` payload."""
    root = tmp_path / name
    root.mkdir(parents=True, exist_ok=True)
    (root / "proton").write_text("#!/bin/sh\n")
    return root


def _patch_compat(monkeypatch, timed_out_sequence):
    from unifideck.launcher.proton import compat as compat_pkg

    seq = iter(timed_out_sequence)
    calls = []

    async def _compat(plan):
        calls.append(plan.tool_id)
        return next(seq)

    monkeypatch.setattr(compat_pkg, "apply_prefix_compat", _compat)
    return calls


async def test_official_proton_is_not_swapped_for_ge(tmp_path, wired, monkeypatch):
    calls = _patch_compat(monkeypatch, [False])
    user_path = _official_proton(tmp_path, "Proton 9.0 (Beta)")
    monkeypatch.setattr(
        proton_pkg, "select_proton_version",
        lambda steam_app_id, store_game_id: (user_path, "proton_9"),
    )
    ge = MagicMock()
    monkeypatch.setattr(proton_pkg, "select_managed_ge_proton", ge)

    state = SimpleNamespace()
    tool, recovered = await setup_mod.setup_prefix(_ctx(), state, proton=None)

    assert calls == ["proton_9"]          # setup ran under the USER's Proton
    ge.assert_not_called()                 # no GE anywhere near it
    wired.pin.assert_not_called()          # nothing overwrote their choice
    assert (tool, recovered) == ("proton_9", False)
    assert state.proton_tool_id == "proton_9"
    assert state.proton_path == user_path


async def test_a_real_hang_still_recovers_to_ge(tmp_path, wired, monkeypatch):
    """The ladder that genuinely earns a Proton switch must survive."""
    calls = _patch_compat(monkeypatch, [True, False])
    user_path = _official_proton(tmp_path, "Proton 9.0 (Beta)")
    ge_path = _official_proton(tmp_path, "GE-Proton11-3")
    monkeypatch.setattr(
        proton_pkg, "select_proton_version",
        lambda steam_app_id, store_game_id: (user_path, "proton_9"),
    )
    monkeypatch.setattr(
        proton_pkg, "select_managed_ge_proton",
        MagicMock(return_value=(ge_path, "GE-Proton11-3")),
    )

    state = SimpleNamespace()
    tool, recovered = await setup_mod.setup_prefix(_ctx(), state)

    assert calls == ["proton_9", "GE-Proton11-3"]
    assert (tool, recovered) == ("GE-Proton11-3", True)
    wired.pin.assert_called_once()
    # The published winner is the one that BUILT the prefix, so the launch
    # plan can be realigned onto it.
    assert state.proton_tool_id == "GE-Proton11-3"
    assert state.proton_path == ge_path


async def test_caller_supplied_proton_skips_the_second_resolution(
    tmp_path, wired, monkeypatch,
):
    """The orchestrator hands over Phase 1's tool; don't re-read config.vdf."""
    _patch_compat(monkeypatch, [False])
    select = MagicMock()
    monkeypatch.setattr(proton_pkg, "select_proton_version", select)
    monkeypatch.setattr(proton_pkg, "select_managed_ge_proton", MagicMock())
    given = _official_proton(tmp_path, "Proton 10.0")

    tool, _ = await setup_mod.setup_prefix(
        _ctx(), SimpleNamespace(), proton=(given, "proton_10"),
    )

    select.assert_not_called()
    assert tool == "proton_10"
