"""Process observation for the Battle.net two-phase launch.

py_modules/unifideck/launcher/proton/handlers/battlenet_watch.py

Everything here runs on the **Linux side**, reading ``/proc``. That is a
deliberate anti-cheat hygiene rule, not incidental: Warden scans the game
process's memory, its loaded code, the Windows process list and its handle
table. Reading ``/proc/<pid>/cmdline`` and ``/environ`` touches none of
those — it never enters the prefix, never opens a handle to the game, and
never appears in the Windows process list.

Two measured facts shape the probes:

* **There is no ``Battle.net Helper.exe`` process.** That string is a
  command-line *argument*; every CEF child is named ``Battle.net.exe`` and
  distinguished by ``--type=``. An earlier design keyed readiness on a
  process that does not exist.
* **The client's ``WINEPREFIX`` is ``<prefix>/pfx/``**, because umu
  normalises it and creates ``pfx -> .`` as a self-symlink. Prefix matching
  must therefore normalise, or a client running for a sibling Blizzard game
  is mistaken for this one's.
"""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# The client's own processes and Wine's infrastructure. None of these is
# ever "the game started". Measured during a real 12.43 GB install.
EXCLUDED_IMAGES: frozenset[str] = frozenset({
    "battle.net.exe",
    "battle.net launcher.exe",
    "agent.exe",
    "agenthelper.exe",
    "blizzarderror.exe",
    "blizzardbrowser.exe",
    "blizzard uninstaller.exe",
    # Wine / Proton infrastructure
    "explorer.exe",
    "services.exe",
    "winedevice.exe",
    "plugplay.exe",
    "rpcss.exe",
    "svchost.exe",
    "tabtip.exe",
    "conhost.exe",
    "wineboot.exe",
    "start.exe",
    "winemenubuilder.exe",
    "umu.exe",
    "xalia.exe",
    "steam.exe",
})

# The main client process carries this flag; its CEF children carry --type=.
_FROM_LAUNCHER = "--from-launcher"
_RENDERER = "--type=renderer"


def _normalise_prefix(prefix: str | Path) -> str:
    """Canonical form for comparing WINEPREFIX values.

    umu rewrites the value to ``<prefix>/pfx/`` and ``pfx`` is a symlink to
    the prefix itself, so both spellings must compare equal.
    """
    try:
        return str(Path(prefix).resolve()).rstrip("/")
    except OSError:
        return str(prefix).rstrip("/")


def _proc_field(pid: str, field: str) -> str:
    try:
        with Path(f"/proc/{pid}/{field}").open("rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def _image_name(cmdline: str) -> str:
    """Windows image name from a Wine process command line, lowercased."""
    first = cmdline.split("\x00", 1)[0]
    if not first:
        return ""
    return first.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def _pids() -> list[str]:
    try:
        return [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []


def scan(prefix: str | Path) -> list[tuple[str, str]]:
    """``(pid, image_name)`` for every Wine process in this prefix.

    Scoped by ``WINEPREFIX`` so a client running for another Blizzard game
    is never mistaken for this one's.
    """
    target = _normalise_prefix(prefix)
    found: list[tuple[str, str]] = []
    for pid in _pids():
        environ = _proc_field(pid, "environ")
        if "WINEPREFIX=" not in environ:
            continue
        value = ""
        for entry in environ.split("\x00"):
            if entry.startswith("WINEPREFIX="):
                value = entry.partition("=")[2]
                break
        if _normalise_prefix(value) != target:
            continue
        image = _image_name(_proc_field(pid, "cmdline"))
        if image:
            found.append((pid, image))
    return found


def client_ready(prefix: str | Path) -> bool:
    """True once the client can accept an ``--exec`` command.

    Keyed on a CEF renderer being up, which is the Linux-observable
    equivalent of "the main window exists". A window probe is not usable:
    xdotool cannot see into Gaming Mode's separate gamescope session.
    """
    target = _normalise_prefix(prefix)
    for pid in _pids():
        cmdline = _proc_field(pid, "cmdline")
        if _RENDERER not in cmdline and _FROM_LAUNCHER not in cmdline:
            continue
        if _image_name(cmdline) != "battle.net.exe":
            continue
        environ = _proc_field(pid, "environ")
        if f"WINEPREFIX={target}" in environ or target in environ:
            return _RENDERER in cmdline
    return False


def game_pids(prefix: str | Path) -> set[str]:
    """PIDs of non-excluded Wine processes — candidate game processes."""
    return {pid for pid, image in scan(prefix) if image not in EXCLUDED_IMAGES}


async def wait_for_client_ready(
    prefix: str | Path, deadline_seconds: float, poll: float = 2.0,
) -> bool:
    """Poll until the client's renderer appears, or give up."""
    timeout = deadline_seconds
    waited = 0.0
    while waited < timeout:
        if client_ready(prefix):
            logger.info("[battlenet] client ready after %.0fs", waited)
            return True
        await asyncio.sleep(poll)
        waited += poll
    logger.error("[battlenet] client not ready after %.0fs", timeout)
    return False


async def wait_for_game(
    prefix: str | Path,
    before: set[str],
    deadline_seconds: float,
    poll: float = 3.0,
) -> str | None:
    """Wait for a game process that was not running before. None on timeout.

    This is the silent-failure detector. An obsolete family code makes the
    client accept ``--exec="launch X"`` and do nothing at all — no error, no
    dialog, no exit code — so "the command returned" proves nothing and only
    a new process does.
    """
    timeout = deadline_seconds
    waited = 0.0
    while waited < timeout:
        appeared = game_pids(prefix) - before
        if appeared:
            pid = sorted(appeared)[0]
            logger.info("[battlenet] game process %s appeared after %.0fs", pid, waited)
            return pid
        await asyncio.sleep(poll)
        waited += poll
    return None


def _game_still_running(prefix: str | Path, pid: str) -> bool:
    """Whether ``pid`` is still a live game process in this prefix."""
    return Path(f"/proc/{pid}").exists() and pid in game_pids(prefix)


async def wait_for_exit(prefix: str | Path, pid: str, poll: float = 10.0) -> None:
    """Block until the game process goes away.

    Polls rather than waits on an event: the game is not our child (the
    client spawned it inside the prefix), so there is no handle to await
    and nothing in-process will ever signal us. ``asyncio.Event`` would
    have nobody to set it.
    """
    while _game_still_running(prefix, pid):  # noqa: ASYNC110 — external OS state
        await asyncio.sleep(poll)


async def wait_while_client_running(prefix: str | Path, poll: float = 10.0) -> None:
    """Block while the client is up, so Steam keeps the shortcut alive.

    Same reasoning as :func:`wait_for_exit`: the client is a detached
    process we do not own.
    """
    while client_ready(prefix):  # noqa: ASYNC110 — external state, no event source
        await asyncio.sleep(poll)
