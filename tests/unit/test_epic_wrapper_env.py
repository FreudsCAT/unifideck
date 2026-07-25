"""Regression: Epic's --wrapper invocation must not leak env pollution.

Bug report: an Epic launch failed with "python3: error while loading shared
libraries: libz.so.1" inside the pressure-vessel container, right after
umu-run started. legendary (bin/legendary) is a PyInstaller onefile binary
that spawns the ``--wrapper`` command (python3 + umu-run) as its own
subprocess; if it hands down its own bundled LD_LIBRARY_PATH/LD_PRELOAD
instead of the clean env it was launched with, that pollution rides
umu-run straight into the Steam Runtime container. GOG/Amazon/Ubisoft are
unaffected — they spawn umu-run directly with Unifideck's own sanitized
env, never going through a vendored CLI's own wrapper mechanism. The fix
force-clears both vars right at the legendary -> umu-run boundary.
"""
from __future__ import annotations

import types
from pathlib import Path

from unifideck.launcher.proton.compat import epic as compat_epic
from unifideck.launcher.proton.handlers.epic import _build_legendary_argv
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan


def _plan() -> ProtonLaunchPlan:
    return ProtonLaunchPlan(
        context=types.SimpleNamespace(
            game_id="abc123", store="epic", exe_path=Path("/install/abc123.exe"),
        ),
        state=types.SimpleNamespace(wrappers=[], game_args=[], umu_id=None),
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        prefix_path=Path("/tmp/prefix"),  # noqa: S108
        env={},
        on_process_start=None,
    )


def test_wrapper_force_clears_ld_env(monkeypatch):
    monkeypatch.setattr(compat_epic, "detect_offline", lambda: False)

    argv = _build_legendary_argv(_plan(), "/plugin/bin/legendary")

    wrapper_cmd = argv[argv.index("--wrapper") + 1]
    assert wrapper_cmd == (
        "env -u LD_LIBRARY_PATH -u LD_PRELOAD "
        "/usr/bin/python3 /plugin/bin/umu/umu/umu-run"
    )
