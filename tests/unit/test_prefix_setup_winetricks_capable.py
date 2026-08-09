"""Regression: don't burn two timeouts on a Proton that can't run winetricks.

Field report — every fresh install spent ~4 minutes in "Setting up game…"
and then "always fell back to Proton GE". On-device logs showed the same
shape three times in a row::

    selected via global-default tool: proton_experimental
    umu ('createprefix',) exceeded 120s — killing process group
    regedit timed out (proton=proton_experimental hung)
    compat still timing out … — retrying setup with managed GE-Proton
    Proton family change proton_experimental -> GE-Proton11-3; resetting prefix

The cause is not a hang. umu's winetricks verb execs
``<PROTONPATH>/protonfixes/winetricks``, and only GE-Proton / UMU-Proton
bundle that file — umu's own ``--help`` says "requires UMU-Proton or
GE-Proton". Under an official Valve Proton it raises FileNotFoundError from
inside umu, leaving the wine child holding the prefix until the compat-step
killpg fires. Two steps, two full timeouts, then the ladder switched to GE
and RESET the prefix — throwing away everything it had just built.

Because the end state was always GE, checking up front costs one ``stat``
and saves minutes plus a wasted prefix build on every fresh install.
"""
from __future__ import annotations

import stat
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from unifideck.launcher import proton as proton_pkg
from unifideck.launcher.proton import prefix_setup as setup_mod
from unifideck.launcher.proton.compat import prefix_init as prefix_init_mod
from unifideck.launcher.proton.prefix_setup import _can_run_winetricks_verb


def _proton_dir(root, *, protonfixes: bool):
    """Build a Proton tool dir, optionally with GE's protonfixes payload."""
    root.mkdir(parents=True, exist_ok=True)
    script = root / "proton"
    script.write_text("#!/bin/sh\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    if protonfixes:
        (root / "protonfixes").mkdir()
        (root / "protonfixes" / "winetricks").write_text("#!/bin/sh\n")
    return root


def test_ge_proton_with_protonfixes_is_capable(tmp_path):
    root = _proton_dir(tmp_path / "GE-Proton11-3", protonfixes=True)
    assert _can_run_winetricks_verb(root) is True


def test_official_proton_without_protonfixes_is_not_capable(tmp_path):
    """The reported case: Proton Experimental ships no protonfixes dir."""
    root = _proton_dir(tmp_path / "Proton - Experimental", protonfixes=False)
    assert _can_run_winetricks_verb(root) is False


def test_the_proton_script_path_is_accepted_too(tmp_path):
    """Callers hold the ``proton`` script path, not the tool dir."""
    root = _proton_dir(tmp_path / "GE-Proton11-3", protonfixes=True)
    assert _can_run_winetricks_verb(root / "proton") is True

    bare = _proton_dir(tmp_path / "Proton 10.0", protonfixes=False)
    assert _can_run_winetricks_verb(bare / "proton") is False


def test_no_path_fails_open(tmp_path):
    """Never let this gate reject a Proton it cannot actually judge."""
    assert _can_run_winetricks_verb(None) is True
    assert _can_run_winetricks_verb("") is True


def test_a_file_named_protonfixes_does_not_count(tmp_path):
    """It has to be the directory umu execs into, not any same-named file."""
    root = _proton_dir(tmp_path / "Weird-Proton", protonfixes=False)
    (root / "protonfixes").write_text("not a directory")
    assert _can_run_winetricks_verb(root) is False


# ── borrowing GE must not change which Proton the GAME runs under ──


async def _setup_with_incapable_default(tmp_path, monkeypatch, *, pending):
    """Drive ``setup_prefix`` with an official (winetricks-incapable) default."""
    default = _proton_dir(tmp_path / "Proton - Experimental", protonfixes=False)
    ge = _proton_dir(tmp_path / "GE-Proton11-3", protonfixes=True)

    monkeypatch.setattr(proton_pkg, "find_python_3_10_plus", lambda: "/usr/bin/python3")
    monkeypatch.setattr(
        proton_pkg, "proton_prepare",
        lambda ctx, state, **kw: SimpleNamespace(
            tool_id=kw["proton_tool_id"], env={}, context=ctx,
            prefix_path=tmp_path / "prefix",
        ),
    )
    monkeypatch.setattr(
        proton_pkg, "select_proton_version",
        lambda steam_app_id, store_game_id: (str(default), "proton_experimental"),
    )
    monkeypatch.setattr(
        proton_pkg, "select_managed_ge_proton",
        MagicMock(return_value=(str(ge), "GE-Proton11-3")),
    )
    monkeypatch.setattr(prefix_init_mod, "ensure_prefix_initialized", AsyncMock())
    monkeypatch.setattr(setup_mod, "_pin_final_tool", MagicMock())
    monkeypatch.setattr(setup_mod, "_compat_pending", lambda *a, **k: pending)

    seen = {}

    async def _compat(plan, *, vcreg_plan=None):
        seen["winetricks_under"] = plan.tool_id
        seen["vcreg_under"] = vcreg_plan.tool_id if vcreg_plan else None
        return False

    from unifideck.launcher.proton import compat as compat_pkg
    monkeypatch.setattr(compat_pkg, "apply_prefix_compat", _compat)

    ctx = SimpleNamespace(
        store="gog", game_id="123", game_key="gog:123", steam_app_id=None,
    )
    result = await setup_mod.setup_prefix(ctx, SimpleNamespace())
    return result, seen


async def test_borrowed_ge_does_not_become_the_launch_proton(tmp_path, monkeypatch):
    """GE is borrowed for one umu verb; the game still runs under the user's pick.

    Reporting GE here is what made the launcher log ``proton=GE-Proton11-3``
    for launches umu actually ran under Proton-Experimental — and that
    disagreement re-stamped the prefix on every single launch.
    """
    (tool, recovered), seen = await _setup_with_incapable_default(
        tmp_path, monkeypatch, pending=True,
    )

    assert seen["winetricks_under"] == "GE-Proton11-3"
    # The VC++ registry import goes last and under the LAUNCH Proton, so that
    # Proton's own prefix upgrade happens before the keys are written.
    assert seen["vcreg_under"] == "proton_experimental"
    assert (tool, recovered) == ("proton_experimental", False)


async def test_warmed_prefix_does_not_borrow_ge_at_all(tmp_path, monkeypatch):
    """Nothing pending → don't touch the prefix and don't reroute the Proton."""
    (tool, recovered), seen = await _setup_with_incapable_default(
        tmp_path, monkeypatch, pending=False,
    )

    assert seen == {}
    assert (tool, recovered) == ("proton_experimental", False)
