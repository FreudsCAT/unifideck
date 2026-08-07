"""Regression: an incapable Proton skips ONE step, it does not get replaced.

Field report: games launched fine on the default Proton but not on any
specific one selected via Steam → Properties → Compatibility → "Force the use
of a specific Steam Play compatibility tool" (the only way to pick a Proton —
Unifideck has no picker of its own, see InstalledButtons.tsx).

Cause: ``prefix_setup._preempt_incapable_proton`` saw that the chosen Proton
ships no ``protonfixes/`` (true of every official Valve Proton — umu execs the
winetricks verb with ``cwd=$PROTONPATH/protonfixes``) and ran the ENTIRE prefix
setup under managed GE-Proton instead, pinning GE onto the prefix marker and
``proton_settings.json``. The launch plan had already been frozen with the
user's Proton, so the game then ran under Proton 9/10/11 inside a prefix GE had
created, compat-installed and version-stamped. A Wine prefix is single-Proton
state; that mismatch is the failure.

The capability of ONE optional step was never evidence about which Proton the
user wants. The gate now lives in ``apply_winetricks`` and skips just that
step. These tests pin that: the incapable Proton must NOT be swapped out, the
marker it writes must NOT be terminal (so redistributables install for real
once the prefix is back on a capable Proton), and nothing may report a hang.
"""
from __future__ import annotations

import stat
import types
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.launcher.proton.compat import winetricks as wt


def _proton_dir(root: Path, *, protonfixes: bool) -> Path:
    root.mkdir(parents=True, exist_ok=True)
    script = root / "proton"
    script.write_text("#!/bin/sh\n")
    script.chmod(script.stat().st_mode | stat.S_IXUSR)
    if protonfixes:
        (root / "protonfixes").mkdir()
        (root / "protonfixes" / "winetricks").write_text("#!/bin/sh\n")
    return root


def _plan(prefix_root: Path, proton_root: Path, tool: str):
    return types.SimpleNamespace(
        prefix_path=prefix_root,
        env={"PROTONPATH": str(proton_root)},
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        state=types.SimpleNamespace(proton_tool_id=tool),
        context=types.SimpleNamespace(game_id="g1", game_key="epic:g1"),
    )


@pytest.fixture
def _wired(monkeypatch, tmp_path):
    """Stub the packages lookup and the umu spawn; capture toasts."""
    monkeypatch.setattr(
        wt, "get_required_winetricks", AsyncMock(return_value=["vcrun2022"]),
    )
    run = AsyncMock(return_value=0)
    monkeypatch.setattr(wt, "run_umu_with_retry", run)
    toast = MagicMock()
    monkeypatch.setattr(wt, "launcher_toast", toast)
    return run, toast


async def test_incapable_proton_skips_the_step(_wired, tmp_path):
    run, toast = _wired
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    proton = _proton_dir(tmp_path / "Proton 9.0 (Beta)", protonfixes=False)

    timed_out = await wt.apply_winetricks(_plan(prefix, proton, "proton_9"))

    run.assert_not_awaited()
    # False, not the timeout signal: nothing hung, so the caller must not
    # escalate to the GE retry ladder (which is what swapped the Proton).
    assert timed_out is False
    assert any(
        c.args[0] == "toasts.launcher.redistributablesSkippedProton"
        for c in toast.call_args_list
    )


async def test_skip_marker_is_not_terminal(_wired, tmp_path):
    """Redistributables must still install once a capable Proton is used."""
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    proton = _proton_dir(tmp_path / "Proton 11.0", protonfixes=False)

    await wt.apply_winetricks(_plan(prefix, proton, "proton_11"))

    marker = prefix / wt._MARKER_NAME
    assert marker.is_file()
    assert wt._already_done(marker) is False


async def test_capable_proton_still_installs(_wired, tmp_path):
    run, _toast = _wired
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    proton = _proton_dir(tmp_path / "GE-Proton11-3", protonfixes=True)

    await wt.apply_winetricks(_plan(prefix, proton, "GE-Proton11-3"))

    run.assert_awaited_once()
    argv = run.await_args.args[0]
    assert "winetricks" in argv
    assert "vcrun2022" in argv


async def test_missing_protonpath_fails_open_and_installs(_wired, tmp_path):
    """No path to judge → attempt the step; never reject on a blind guess."""
    run, _toast = _wired
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    plan = _plan(prefix, tmp_path / "unused", "whatever")
    plan.env = {}

    await wt.apply_winetricks(plan)

    run.assert_awaited_once()
