"""Finding a wrapper store's vendor clients across every prefix.

py_modules/unifideck/launcher/proton/handlers/wrapper_clients.py

The ``/proc`` primitives here were Battle.net's, scoped to one prefix at a
time: "is the client up in *this* prefix". That is the wrong shape for the
question that actually causes session loss, which is the **inverse** — "is a
client up in some *other* prefix right now".

It matters because these clients are not per-prefix as far as the vendor is
concerned. Every prefix is a clone, so every client presents the same
client-instance id and the same token; two running at once both refresh that
token and one of them loses. On Battle.net the user hits it by opening the
Sign-In tile and then launching a game, and the symptom is
``BLZBNTBGS80000023`` on whichever client was second to look.

Everything runs on the **Linux side**, reading ``/proc``. That is an
anti-cheat hygiene rule, not incidental: Warden scans the game process's
memory, its loaded code, the Windows process list and its handle table.
Reading ``/proc/<pid>/cmdline`` and ``/environ`` touches none of those — it
never enters the prefix, never opens a handle to the game, and never appears
in the Windows process list.

Stdlib-only; runs under the SYSTEM python (3.10-3.14).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

_WINEPREFIX = "WINEPREFIX="

# The vendor client's own Windows images, per store. Battle.net's CEF children
# are *all* named ``battle.net.exe`` and distinguished only by ``--type=``;
# there is no ``Battle.net Helper.exe`` process, despite that string appearing
# in command lines.
CLIENT_IMAGES: dict[str, frozenset[str]] = {
    "battlenet": frozenset({"battle.net.exe", "battle.net launcher.exe"}),
    "ubisoft": frozenset({"upc.exe", "ubisoftconnect.exe"}),
}


def normalise_prefix(prefix: str | Path) -> str:
    """Canonical form for comparing WINEPREFIX values.

    umu rewrites the value to ``<prefix>/pfx/`` and ``pfx`` is a symlink to
    the prefix itself, so both spellings must compare equal.
    """
    try:
        return str(Path(prefix).resolve()).rstrip("/")
    except OSError:
        return str(prefix).rstrip("/")


def proc_field(pid: str, field: str) -> str:
    """One ``/proc/<pid>`` field as text, or empty when unreadable."""
    try:
        with Path(f"/proc/{pid}/{field}").open("rb") as handle:
            return handle.read().decode("utf-8", "replace")
    except (OSError, ValueError):
        return ""


def image_name(cmdline: str) -> str:
    """Windows image name from a Wine process command line, lowercased."""
    first = cmdline.split("\x00", 1)[0]
    if not first:
        return ""
    return first.replace("\\", "/").rsplit("/", 1)[-1].strip().lower()


def pids() -> list[str]:
    """Every pid on the system."""
    try:
        return [p for p in os.listdir("/proc") if p.isdigit()]
    except OSError:
        return []


def wineprefix_of(pid: str) -> str | None:
    """Normalised ``WINEPREFIX`` of ``pid``, or None if it has none.

    Read as an exact ``WINEPREFIX=`` entry, never as a substring of the whole
    environ blob: ``STEAM_COMPAT_DATA_PATH`` and ``PROTONPATH`` carry the same
    path and would match a naive ``in environ`` test.
    """
    environ = proc_field(pid, "environ")
    if _WINEPREFIX not in environ:
        return None
    for entry in environ.split("\x00"):
        if entry.startswith(_WINEPREFIX):
            return normalise_prefix(entry.partition("=")[2])
    return None


def scan_prefix(prefix: str | Path) -> list[tuple[str, str, str]]:
    """``(pid, image, cmdline)`` for every **Windows** process in ``prefix``.

    Scoped by ``WINEPREFIX`` so a client running for another of the store's
    games is never mistaken for this one's.

    Restricted to ``.exe`` images on purpose. ``WINEPREFIX`` is inherited by
    the whole Linux-side umu chain — ``srt-bwrap``, ``pv-adverb``,
    ``umu-run``, the Proton ``python3`` — so those wrappers used to read as
    game processes. Measured on-device: a phase C ``srt-bwrap`` was reported
    as "game process appeared after 0s", defeating the silent-failure
    detector and leaving the watcher following a pid that is not the game.
    """
    target = normalise_prefix(prefix)
    found: list[tuple[str, str, str]] = []
    for pid in pids():
        if wineprefix_of(pid) != target:
            continue
        cmdline = proc_field(pid, "cmdline")
        image = image_name(cmdline)
        if image.endswith(".exe"):
            found.append((pid, image, cmdline))
    return found


def client_running_in(store: str, prefix: str | Path) -> bool:
    """True while any of ``store``'s client processes is alive in ``prefix``.

    Deliberately a superset of Battle.net's own ``client_running``, which
    counts only ``battle.net.exe``: this one also counts the launcher
    executable. Callers use it to decide when a client has *fully* exited and
    its rotated token is safe to read, and there over-reporting liveness only
    costs a short wait, while under-reporting it reads a torn vault.
    """
    images = CLIENT_IMAGES.get(store)
    if not images:
        return False
    return any(image in images for _, image, _ in scan_prefix(prefix))


def live_client_prefixes(
    store: str, *, exclude: tuple[str | Path, ...] = (),
) -> list[Path]:
    """Prefixes currently running ``store``'s vendor client.

    One pass over ``/proc``, grouping by ``WINEPREFIX`` — the inverse of
    :func:`scan_prefix`, and the only way to answer "is a client already up
    somewhere else" without knowing every prefix path in advance (games
    installed to removable media live outside our directory).

    Returned paths are the normalised ``WINEPREFIX`` values, which for umu is
    ``<prefix>/pfx`` — a self-symlink to the prefix, so they resolve to the
    same directory the caller passed in.
    """
    images = CLIENT_IMAGES.get(store)
    if not images:
        return []
    skip = {normalise_prefix(p) for p in exclude}
    found: dict[str, Path] = {}
    for pid in pids():
        prefix = wineprefix_of(pid)
        if prefix is None or prefix in skip or prefix in found:
            continue
        if image_name(proc_field(pid, "cmdline")) in images:
            found[prefix] = Path(prefix)
    return list(found.values())
