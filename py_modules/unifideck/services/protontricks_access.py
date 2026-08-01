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
import subprocess
from pathlib import Path

from unifideck.core.compat_bridge import PREFIX_ROOT
from unifideck.utils.mounts import run_demoted

logger = logging.getLogger(__name__)

#: The only Protontricks distribution that needs a permission grant. Native
#: and pip installs read the bridge symlink with no sandbox in the way.
FLATPAK_APP_ID = "com.github.Matoking.protontricks"

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


def has_access(prefixes: Path) -> bool:
    """True iff a user override already exposes *prefixes* to the sandbox.

    Matches an exact grant of the prefixes dir *or* any ancestor of it (a
    user who granted their whole home is already covered — do not re-add).
    """
    proc = _run_as_owner(
        ["flatpak", "override", "--user", "--show", FLATPAK_APP_ID], prefixes,
    )
    if not proc or proc.returncode != 0:
        return False
    granted = _granted_paths(proc.stdout)
    target = prefixes.resolve()
    for raw in granted:
        try:
            candidate = Path(raw).expanduser().resolve()
        except (OSError, RuntimeError):
            continue
        if candidate == target or candidate in target.parents:
            return True
    return False


def _granted_paths(show_output: str) -> list[str]:
    """Extract filesystem grants from ``flatpak override --show`` output.

    The output is INI-ish::

        [Context]
        filesystems=/home/deck/Documents;~/.steam;

    Entries may carry a ``:ro``/``:rw``/``:create`` mode suffix, and
    placeholder tokens (``home``, ``host``, ``xdg-*``) which are not paths.
    """
    out: list[str] = []
    for line in show_output.splitlines():
        key, sep, value = line.partition("=")
        if not sep or key.strip() != "filesystems":
            continue
        for raw in value.split(";"):
            item = raw.strip()
            if not item:
                continue
            for mode in (":ro", ":rw", ":create"):
                if item.endswith(mode):
                    item = item[: -len(mode)]
                    break
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
