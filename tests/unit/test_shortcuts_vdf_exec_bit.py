"""Unit tests for the shortcuts.vdf executable-bit guard (UD-112 / NSL wipe).

NonSteamLaunchers' persistent ``nslgamescanner.service`` treats the
*executable bit* of ``shortcuts.vdf`` as its "already-initialised"
sentinel: on each scan, if the file is **not** executable it overwrites
the whole file with an empty ``{"shortcuts": {}}`` — wiping every
shortcut, Unifideck's included ("0 games after sync"). NSL always leaves
the file at ``0o755`` after its own writes.

Our ``write_vdf`` writes via tmp-file + ``os.replace``, which creates the
destination inode at the umask default (typically ``0o644`` — NOT
executable), silently disarming NSL's sentinel. The fix re-asserts
``0o755`` after every write, on the single lowest-level byte-writer, so
the two tools coexist.

These tests pin (1) that ``write_vdf`` leaves the file executable, and
(2) that a file written by ``write_vdf`` survives NSL's real init gate
(reproduced from ``NSLGameScanner.py``), so the library is not wiped.
"""
from __future__ import annotations

import asyncio
import os
import stat

import vdf

from unifideck.services.shortcut.persistence import read_vdf, write_vdf

_LAUNCHER = "/home/deck/homebrew/plugins/Unifideck/bin/unifideck-launcher"


def _ours(appid: int, launch: str) -> dict:
    """A genuine Unifideck shortcut (launcher ``Exe``)."""
    return {"appid": appid, "Exe": f'"{_LAUNCHER}"', "LaunchOptions": launch}


def _library(n: int) -> dict:
    """A shortcuts.vdf dict with *n* Unifideck-owned entries."""
    return {"shortcuts": {str(i): _ours(1000 + i, f"epic:{i}") for i in range(n)}}


def _nsl_scanner_startup_gate(shortcuts_file: str) -> dict:
    """Reproduce NSL's init gate verbatim (NSLGameScanner.py:262-279).

    Returns the shortcuts dict NSL would hold after startup and writes
    the file exactly as NSL does. If the file is not executable, NSL
    wipes it to empty; otherwise it loads and leaves it intact.
    """
    def write_shortcuts_to_file(path: str, shortcuts: dict) -> None:
        with open(path, "wb") as f:
            f.write(vdf.binary_dumps(shortcuts))  # type: ignore[no-untyped-call]
        os.chmod(path, 0o755)  # noqa: S103 — mirrors NSL's own code under test

    if not os.access(shortcuts_file, os.X_OK):
        empty = {"shortcuts": {}}
        write_shortcuts_to_file(shortcuts_file, empty)
        return empty
    with open(shortcuts_file, "rb") as f:
        return vdf.binary_loads(f.read())  # type: ignore[no-any-return, no-untyped-call]


# ── The guard itself ───────────────────────────────────────────────

def test_write_vdf_leaves_file_executable(tmp_path):
    """Every write must leave the user-exec bit set (NSL sentinel)."""
    path = str(tmp_path / "shortcuts.vdf")
    asyncio.run(write_vdf(path, _library(3)))

    assert os.access(path, os.X_OK), "shortcuts.vdf must be executable after write"
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o755


def test_write_vdf_reasserts_exec_bit_when_stripped(tmp_path):
    """A pre-existing non-executable file is repaired on the next write."""
    path = str(tmp_path / "shortcuts.vdf")
    asyncio.run(write_vdf(path, _library(1)))
    os.chmod(path, 0o644)  # simulate a prior non-exec write / umask
    assert not os.access(path, os.X_OK)

    asyncio.run(write_vdf(path, _library(2)))
    assert os.access(path, os.X_OK)


def test_library_survives_nsl_gate_after_write_vdf(tmp_path):
    """End-to-end: a write_vdf file is NOT wiped by NSL's scanner gate."""
    path = str(tmp_path / "shortcuts.vdf")
    asyncio.run(write_vdf(path, _library(5)))

    # NSL's scanner fires: because the file is executable it loads, not wipes.
    after = _nsl_scanner_startup_gate(path)
    assert len(after["shortcuts"]) == 5, "NSL wiped an executable file"

    # And the bytes are still readable as our library.
    reloaded = asyncio.run(read_vdf(path))
    assert len(reloaded["shortcuts"]) == 5


def test_nsl_gate_wipes_a_non_executable_file(tmp_path):
    """Control: proves the gate DOES wipe when the exec bit is missing.

    Guards against the reproduction going stale — if this stops wiping,
    the exec-bit fix is no longer load-bearing and the guard above is
    meaningless.
    """
    path = str(tmp_path / "shortcuts.vdf")
    asyncio.run(write_vdf(path, _library(5)))
    os.chmod(path, 0o644)  # undo the fix, as a plain tmp+replace would leave it

    after = _nsl_scanner_startup_gate(path)
    assert after == {"shortcuts": {}}, "NSL gate should wipe a non-exec file"
