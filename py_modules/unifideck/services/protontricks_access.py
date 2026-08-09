"""Grant Flatpak Protontricks read access to Unifideck's prefixes.

py_modules/unifideck/services/protontricks_access.py

``core.compat_bridge`` symlinks ``steamapps/compatdata/<appid>`` at
``~/.local/share/unifideck/prefixes/<game_id>`` so external Wine tooling can
find our prefixes. That is sufficient for a native/pip Protontricks, but the
**Flatpak** build — how it is installed on virtually every Deck — runs in a
sandbox whose ``filesystems=`` list covers ``~/.steam`` and
``~/.local/share/Steam`` and nothing else. Inside that sandbox the bridge
symlink dangles, ``prefix_path.is_dir()`` is False, and Protontricks skips
the shortcut with *"does not have a prefix"* — exactly as if the bridge did
not exist. Verified on-device: the identical symlink is invisible in-sandbox
and visible after the override below.

So the bridge needs one companion action::

    flatpak override --user \
        --filesystem=<prefixes dir> com.github.Matoking.protontricks

The grant is deliberately **narrow** (the prefixes directory only — not the
whole data dir, which holds auth tokens and caches) and idempotent.

Root note: ``plugin_loader`` runs as root, but ``--user`` overrides are
per-user state under the *desktop* user's ``~/.local/share/flatpak``. Running
it as root would silently configure root's Flatpak instead. Every command
here is therefore demoted to the uid that owns the prefixes directory, via
:func:`unifideck.utils.mounts.run_demoted` (a real subprocess — never
``os.setuid`` in this process; see that module's docstring).

Best-effort throughout: Protontricks is optional tooling and no failure here
may affect syncing, installing, or launching.
"""
from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterator
from pathlib import Path

from unifideck.core.compat_bridge import PREFIX_ROOT
from unifideck.utils.mounts import run_demoted

logger = logging.getLogger(__name__)

#: The only Protontricks distribution that needs a permission grant. Native
#: and pip installs read the bridge symlink with no sandbox in the way.
FLATPAK_APP_ID = "com.github.Matoking.protontricks"

#: Access-mode suffixes Flatpak appends to a ``filesystems=`` entry.
_GRANT_MODES = (":ro", ":rw", ":create")

_TIMEOUT = 15.0


def _owner_ids(path: Path) -> tuple[int, int] | None:
    """``(uid, gid)`` owning *path*, walking up to the first existing parent.

    ``None`` when nothing in the chain can be stat'd.
    """
    for candidate in (path, *path.parents):
        try:
            st = candidate.stat()
        except OSError:
            continue
        return st.st_uid, st.st_gid
    return None


def _run_as_owner(
    argv: list[str], prefixes: Path,
) -> subprocess.CompletedProcess[str] | None:
    """Run *argv* as the owner of the prefixes dir. ``None`` on failure."""
    ids = _owner_ids(prefixes)
    if ids is None:
        logger.debug("[protontricks] cannot stat %s to find owner", prefixes)
        return None
    uid, gid = ids
    return run_demoted(argv, uid, gid, timeout=_TIMEOUT)


def flatpak_present(prefixes: Path) -> bool:
    """True iff the Protontricks Flatpak is installed for this user."""
    proc = _run_as_owner(["flatpak", "info", FLATPAK_APP_ID], prefixes)
    return bool(proc and proc.returncode == 0)


def _show_overrides(prefixes: Path) -> str | None:
    """Raw ``flatpak override --user --show`` output, or ``None`` on failure.

    One subprocess shared by every "is it already granted?" question.
    """
    proc = _run_as_owner(
        ["flatpak", "override", "--user", "--show", FLATPAK_APP_ID], prefixes,
    )
    if not proc or proc.returncode != 0:
        return None
    return proc.stdout


def _path_is_granted(show_output: str, target: Path) -> bool:
    """True iff *show_output* grants *target* or any ancestor of it."""
    try:
        resolved = target.resolve()
    except (OSError, RuntimeError):
        return False
    for raw in _granted_paths(show_output):
        try:
            candidate = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if candidate == resolved or candidate in resolved.parents:
            return True
    return False


def has_access(prefixes: Path) -> bool:
    """True iff a user override already exposes *prefixes* to the sandbox.

    Matches an exact grant of the prefixes dir *or* any ancestor of it (a
    user who granted their whole home is already covered — do not re-add).
    """
    shown = _show_overrides(prefixes)
    return _path_is_granted(shown, prefixes) if shown is not None else False


def _filesystem_entries(show_output: str) -> Iterator[str]:
    """Yield the non-empty ``filesystems=`` entries of *show_output*.

    There may be more than one ``filesystems=`` line; every one contributes.
    """
    for line in show_output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "filesystems":
            continue
        for raw in value.split(";"):
            if raw.strip():
                yield raw.strip()


def _strip_mode_suffix(item: str) -> str:
    """Drop a trailing ``:ro``/``:rw``/``:create`` access mode from *item*."""
    for mode in _GRANT_MODES:
        if item.endswith(mode):
            return item[: -len(mode)]
    return item


def _granted_paths(show_output: str) -> list[str]:
    """Extract filesystem grants from ``flatpak override --show`` output.

    The output is INI-ish::

        [Context]
        filesystems=/home/deck/Documents;~/.steam;

    Entries may carry a ``:ro``/``:rw``/``:create`` mode suffix, and
    placeholder tokens (``home``, ``host``, ``xdg-*``) which are not paths.
    """
    out: list[str] = []
    for raw in _filesystem_entries(show_output):
        item = _strip_mode_suffix(raw)
        if item.startswith(("/", "~")):
            out.append(item)
    return out


def ensure_access(prefixes: Path | str | None = None) -> str:
    """Idempotently grant the Flatpak read access to the prefixes dir.

    Returns one of:

    ``"granted"``  the override was just added;
    ``"already"``  access was already present — the steady state;
    ``"absent"``   Protontricks Flatpak (or ``flatpak`` itself) not installed;
    ``"skipped"``  the prefixes directory does not exist yet;
    ``"failed"``   the ``flatpak override`` command errored.

    Never raises.
    """
    root = Path(prefixes).expanduser() if prefixes else PREFIX_ROOT
    if not root.is_dir():
        return "skipped"
    if not flatpak_present(root):
        return "absent"
    if has_access(root):
        return "already"

    proc = _run_as_owner(
        [
            "flatpak", "override", "--user",
            f"--filesystem={root}",
            FLATPAK_APP_ID,
        ],
        root,
    )
    if not proc or proc.returncode != 0:
        detail = (proc.stderr or "").strip() if proc else "no subprocess"
        logger.warning("[protontricks] override failed: %s", detail)
        return "failed"
    logger.info("[protontricks] granted Flatpak access to %s", root)
    return "granted"


#: Protontricks' own env var for extra ``compatibilitytools.d`` roots. This is
#: the ONLY way to widen its Proton search inside the Flatpak: it also scans
#: ``/usr/share/steam/compatibilitytools.d``, but Flatpak silently ignores
#: filesystem grants under ``/usr``, so that path stays invisible in-sandbox
#: no matter what we override (verified on-device).
EXTRA_TOOLS_ENV = "STEAM_EXTRA_COMPAT_TOOLS_PATHS"


def _env_entries(show_output: str, name: str) -> list[str]:
    """Path entries of the ``name`` env override, in declaration order.

    Section-blind on purpose, matching :func:`_filesystem_entries`: the key is
    unique across ``flatpak override --show``'s INI sections, so there is no
    need to track which one we are inside.
    """
    for line in show_output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip() != name:
            continue
        return [part for part in value.split(os.pathsep) if part.strip()]
    return []


def tool_path_status(root: Path, tools_dir: Path) -> str:
    """Report whether *tools_dir* is both readable and searched in-sandbox.

    ``"already"`` both the filesystem grant and the env entry are present;
    ``"partial"`` one of the two is missing;
    ``"absent"``  neither is present;
    ``"unknown"`` the override state could not be read.
    """
    shown = _show_overrides(root)
    if shown is None:
        return "unknown"
    granted = _path_is_granted(shown, tools_dir)
    searched = str(tools_dir) in _env_entries(shown, EXTRA_TOOLS_ENV)
    if granted and searched:
        return "already"
    return "partial" if granted or searched else "absent"


def ensure_tool_path_access(tools_dir: Path | str | None = None) -> str:
    """Make :mod:`unifideck.core.compat_tool_bridge`'s links usable in-sandbox.

    Two overrides are needed, and both are idempotent:

    * ``--filesystem=<tools_dir>:ro`` so the sandbox can read the links;
    * ``--env=STEAM_EXTRA_COMPAT_TOOLS_PATHS=…<tools_dir>`` so Protontricks
      actually *searches* them — a readable directory it never looks in is
      worth nothing.

    Any pre-existing env value is preserved and appended to, never replaced:
    the user (or another tool) may already point Protontricks somewhere.

    Same return vocabulary as :func:`ensure_access`.
    """
    from unifideck.core.compat_tool_bridge import bridge_root

    tools = Path(tools_dir).expanduser() if tools_dir else bridge_root()
    if not tools.is_dir():
        return "skipped"
    if not flatpak_present(tools):
        return "absent"
    shown = _show_overrides(tools)
    if shown is None:
        return "failed"
    if _path_is_granted(shown, tools) and str(tools) in _env_entries(
        shown, EXTRA_TOOLS_ENV,
    ):
        return "already"

    existing = [e for e in _env_entries(shown, EXTRA_TOOLS_ENV) if e != str(tools)]
    joined = os.pathsep.join([*existing, str(tools)])
    proc = _run_as_owner(
        [
            "flatpak", "override", "--user",
            f"--filesystem={tools}:ro",
            f"--env={EXTRA_TOOLS_ENV}={joined}",
            FLATPAK_APP_ID,
        ],
        tools,
    )
    if not proc or proc.returncode != 0:
        detail = (proc.stderr or "").strip() if proc else "no subprocess"
        logger.warning("[protontricks] tool-path override failed: %s", detail)
        return "failed"
    logger.info(
        "[protontricks] granted Flatpak access to %s and added it to %s",
        tools, EXTRA_TOOLS_ENV,
    )
    return "granted"
