"""Tests for launcher.proton.infrastructure.container_escape.

Field bug: setting Steam's own Properties > Compatibility "Force the use of
a specific Steam Play compatibility tool" on a Unifideck shortcut makes Steam
wrap ``bin/unifideck-launcher`` in ITS OWN pressure-vessel container. Proton's
``python3`` cannot resolve ``libz.so.1`` in there, so umu exits 127 —
*whichever* Proton the user picks, which is why trying different builds to
work around it never helped.

Reproduced deterministically on-device by entering the container exactly the
way Steam does and running the identical umu command (fails, container python
``May 5 2026``), then escaping it (succeeds, host python ``Jun 21 2025``,
rc=0). ``UMU_NO_RUNTIME=1`` was also verified NOT to help.

These tests pin the escape's decision logic and argv shape.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.launcher.proton.infrastructure import container_escape as ce

_ARGV = ["/usr/bin/python3.13", "/plugin/bin/umu/umu/umu-run", "/games/G.exe"]


@pytest.fixture
def _containerised(monkeypatch: pytest.MonkeyPatch):
    """Pretend we're inside pressure-vessel with the escape client present."""
    monkeypatch.setattr(ce, "in_pressure_vessel", lambda: True)
    monkeypatch.setattr(
        ce.shutil, "which", lambda _n: "/usr/bin/steam-runtime-launch-client",
    )


def test_noop_when_not_containerised(monkeypatch):
    """The normal (unwrapped) launch must be left completely alone."""
    monkeypatch.setattr(ce, "in_pressure_vessel", lambda: False)
    assert ce.escape_argv(_ARGV, {"GAMEID": "umu-0"}, None) == _ARGV


def test_noop_when_escape_client_missing(monkeypatch):
    """No client → run as before rather than breaking outright."""
    monkeypatch.setattr(ce, "in_pressure_vessel", lambda: True)
    monkeypatch.setattr(ce.shutil, "which", lambda _n: None)
    assert ce.escape_argv(_ARGV, {"GAMEID": "umu-0"}, None) == _ARGV


def test_wraps_with_alongside_steam(_containerised):
    out = ce.escape_argv(_ARGV, {"GAMEID": "umu-0"}, None)
    assert out[0] == "/usr/bin/steam-runtime-launch-client"
    assert out[1] == "--alongside-steam"
    assert "--" in out and "env" in out
    # the original command survives intact, at the end
    assert out[-len(_ARGV):] == _ARGV


def test_passes_cwd_as_directory(_containerised):
    out = ce.escape_argv(_ARGV, {}, Path("/games/The Gap"))
    assert "--directory=/games/The Gap" in out


def test_inherited_container_vars_dropped_ours_forwarded(
    _containerised, monkeypatch,
):
    """Only what this launch deliberately set crosses the boundary.

    ``PATH`` is the load-bearing case: it is inherited from the container
    and must NOT be forwarded, or the escaped process would resolve
    binaries against container paths instead of the clean host ones.
    """
    monkeypatch.setattr(
        ce.os, "environ", {"PATH": "/container/bin", "HOME": "/home/deck"},
    )
    env = {
        "PATH": "/container/bin",      # inherited, unchanged → dropped
        "HOME": "/home/deck",          # inherited, unchanged → dropped
        "GAMEID": "umu-0",             # ours → forwarded
        "PROTONPATH": "/ge",           # ours → forwarded
        "WINEDLLOVERRIDES": "x=n,b",   # ours → forwarded
    }
    out = ce.escape_argv(_ARGV, env, None)
    pairs = out[out.index("env") + 1: -len(_ARGV)]
    assert "PATH=/container/bin" not in pairs
    assert "HOME=/home/deck" not in pairs
    assert "GAMEID=umu-0" in pairs
    assert "PROTONPATH=/ge" in pairs
    assert "WINEDLLOVERRIDES=x=n,b" in pairs


def test_always_forward_survives_identical_container_value(
    _containerised, monkeypatch,
):
    """Critical umu vars are forwarded even if the container already had
    the same value — the escaped process starts from Steam's env, not ours."""
    monkeypatch.setattr(ce.os, "environ", {"WINEPREFIX": "/pfx"})
    out = ce.escape_argv(_ARGV, {"WINEPREFIX": "/pfx"}, None)
    assert "WINEPREFIX=/pfx" in out


def test_detection_reads_lowercase_container_var(monkeypatch):
    """pressure-vessel sets lowercase ``container`` (OCI convention)."""
    monkeypatch.setattr(ce.os, "environ", {"container": "pressure-vessel"})
    monkeypatch.setattr(ce.Path, "is_dir", lambda _self: False)
    assert ce.in_pressure_vessel() is True

    monkeypatch.setattr(ce.os, "environ", {})
    assert ce.in_pressure_vessel() is False
