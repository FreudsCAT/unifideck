"""support_bundle/procscan.py — Read-only process inspection.

Shared by the session probe (is gamescope running, i.e. Gaming Mode)
and the conflict probe (is a stranded wineserver or upc.exe holding an
install hostage).

**Strictly observational.** This module names processes; it never
signals, kills, or otherwise touches them. A diagnostics capture that
mutated the state it was describing would be worse than no capture.

Reads ``/proc`` directly rather than shelling out to ``pgrep`` so it
works identically on every distro and costs one directory walk.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

_PROC = Path("/proc")

# Only these processes are ever reported. An allowlist rather than a
# full process table: the bundle is headed for a public bug-report
# channel, and an unrelated user process has no business in it.
TOOLCHAIN_NAMES: tuple[str, ...] = (
    "steam", "steamwebhelper", "gamescope", "wineserver", "wine",
    "wineserver64", "umu-run", "upc.exe", "UplayWebCore.exe",
    "legendary", "gogdl", "nile", "comet", "proton", "srt-bwrap",
)

# Full command lines are recorded only for our own toolchain, where the
# arguments are the diagnostic (which Proton, which prefix, which
# game). Everything else gets a name and nothing more.
CMDLINE_ALLOWED: frozenset[str] = frozenset({
    "wineserver", "wine", "wineserver64", "umu-run", "upc.exe",
    "legendary", "gogdl", "nile", "comet", "proton", "srt-bwrap",
})
# NB: matching on ``comm`` survives the legendary/gogdl switch to zipapps.
# The kernel sets ``comm`` from the file passed to ``execve``, not from the
# shebang interpreter it goes on to load, so a zipapp still reports
# ``legendary``/``gogdl`` (verified on-device) even though its argv reads
# ``/usr/bin/env python3 bin/legendary``. Nothing to special-case here.


@dataclass(frozen=True)
class ProcInfo:
    """One observed process."""

    pid: int
    name: str
    uid: int | None
    started_at: float | None
    cmdline: str = ""


def _read(path: Path) -> str:
    """Read a /proc file, returning "" on any failure."""
    try:
        return path.read_text(encoding="utf-8", errors="replace")
    except (OSError, ValueError):
        return ""


def _proc_start_time(entry: Path) -> float | None:
    """Wall-clock start time of a process, in epoch seconds.

    Derived from the directory mtime, which the kernel sets when the
    process is created. Cheaper and far simpler than parsing field 22
    of ``/proc/<pid>/stat`` against ``btime`` and ``CLK_TCK``, and
    accurate to the second, which is all the shortcuts.vdf race check
    needs.
    """
    try:
        return entry.stat().st_mtime
    except OSError:
        return None


def _proc_uid(entry: Path) -> int | None:
    """Owning uid of a process, or None when unreadable."""
    try:
        return entry.stat().st_uid
    except OSError:
        return None


def iter_processes() -> list[ProcInfo]:
    """Snapshot every allowlisted process currently running.

    A process that exits mid-walk simply disappears from the result —
    every read is individually guarded, because ``/proc`` is racy by
    nature and a vanished pid is not an error worth reporting.
    """
    found: list[ProcInfo] = []
    if not _PROC.is_dir():
        return found
    try:
        entries = list(_PROC.iterdir())
    except OSError as err:
        logger.debug("[support_bundle] /proc walk failed: %s", err)
        return found
    for entry in entries:
        if not entry.name.isdigit():
            continue
        info = _inspect(entry)
        if info is not None:
            found.append(info)
    return found


def _inspect(entry: Path) -> ProcInfo | None:
    """Build a :class:`ProcInfo` if this pid is allowlisted."""
    name = _read(entry / "comm").strip()
    if not name or name not in TOOLCHAIN_NAMES:
        return None
    cmdline = ""
    if name in CMDLINE_ALLOWED:
        raw = _read(entry / "cmdline")
        cmdline = raw.replace("\x00", " ").strip()[:400]
    return ProcInfo(
        pid=int(entry.name),
        name=name,
        uid=_proc_uid(entry),
        started_at=_proc_start_time(entry),
        cmdline=cmdline,
    )


def is_running(name: str) -> bool:
    """True when at least one process named ``name`` is alive."""
    return any(p.name == name for p in iter_processes())


def newest_start(processes: list[ProcInfo], name: str) -> float | None:
    """Most recent start time among processes called ``name``.

    Steam spawns helpers, so "when did Steam start" means the newest
    matching process, which is the one whose view of shortcuts.vdf
    matters.
    """
    stamps = [p.started_at for p in processes if p.name == name and p.started_at]
    return max(stamps) if stamps else None


def env_of(pid: int, keys: tuple[str, ...]) -> dict[str, bool]:
    """Report which of ``keys`` are present in a process environment.

    Presence only, never values. Used to check whether the four
    session variables the install-time warmup depends on
    (``DISPLAY``, ``WAYLAND_DISPLAY``, ``XDG_RUNTIME_DIR``,
    ``DBUS_SESSION_BUS_ADDRESS``) can still be borrowed from the live
    Steam process. Reading another process's environ requires matching
    credentials, so an empty result means "could not read", which the
    caller distinguishes.
    """
    raw = _read(_PROC / str(pid) / "environ")
    if not raw:
        return {}
    names = {item.split("=", 1)[0] for item in raw.split("\x00") if "=" in item}
    return {key: key in names for key in keys}


def own_ids() -> dict[str, int]:
    """Return this process's uid/euid/gid, proving who we run as."""
    return {
        "uid": os.getuid(),
        "euid": os.geteuid(),
        "gid": os.getgid(),
    }
