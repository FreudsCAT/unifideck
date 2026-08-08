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

Checking up front costs one ``stat`` and saves two full timeouts per install.

The gate has since MOVED (it now lives in ``compat.winetricks``, evaluated on
``plan.env["PROTONPATH"]``) and been narrowed: it used to route the whole
prefix setup to managed GE-Proton, which is what broke launching under a
user-selected Proton — a prefix is single-Proton state, so a GE-built prefix
plus a Proton-9 launch is the corruption. It now skips the winetricks step
and leaves the Proton alone. The capability predicate itself is unchanged,
which is why every case below still holds; see
``test_winetricks_skips_incapable_proton.py`` for the new behaviour.
"""
from __future__ import annotations

import stat

from unifideck.launcher.proton.compat.winetricks import (
    _proton_can_run_winetricks_verb as _can_run_winetricks_verb,
)


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
