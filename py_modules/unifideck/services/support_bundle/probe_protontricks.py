"""support_bundle/probe_protontricks.py — Is Protontricks actually usable?

"Protontricks still not working, giving errors" is a recurring report that
our bundles could not answer *at all*. The only protontricks line the plugin
logged was ``[prefix_bridge] … flatpak=absent`` / ``flatpak=skipped``, and
both values are ambiguous by construction:

* ``absent``  conflates "``flatpak`` is not installed" with "the Protontricks
  Flatpak is not installed" — so a **native** Protontricks in active use
  reports ``absent``, which reads like the tooling is missing;
* ``skipped`` only means the prefixes dir did not exist *at that instant*,
  which is true on every boot before the first game is installed.

So a report arrived with a 13 MB bundle in which the word "protontricks"
appeared zero times. This probe closes that gap by recording the four things
that decide whether Protontricks can work, in the order it needs them:

1. **which distribution** is installed (native / flatpak / absent) — the
   ambiguity above, resolved;
2. **the prefix bridges** — is each ``compatdata/<appid>`` link resolvable,
   and does it satisfy Protontricks' two gates (``pfx`` is a dir,
   ``pfx.lock`` is a file)?
3. **the compat-tool bridge** — Protontricks must also find the *Proton*, and
   a distro-packaged one under ``/usr/share/steam`` is invisible inside the
   Flatpak sandbox (Flatpak ignores ``/usr`` filesystem grants). Reports both
   halves of the fix: the links, and whether the sandbox is set up to read
   *and* search them;
4. **what Protontricks itself says** — ``protontricks -l``, verbatim.

Everything is best-effort and read-only: an absent Protontricks, a missing
Steam root, or a subprocess that times out all produce a field saying so
rather than an exception. Runs during environment collection because
``checks.py`` is required to derive verdicts from already-collected data and
never touch the filesystem itself.
"""
from __future__ import annotations

import logging
import os
import shutil
from pathlib import Path
from typing import Any

from unifideck.core import compat_bridge, compat_tool_bridge
from unifideck.services import protontricks_access as access
from unifideck.utils import vdf_compat
from unifideck.utils.mounts import run_demoted

logger = logging.getLogger(__name__)

#: ``protontricks -l`` walks every Steam library and every shortcut, so it is
#: slower than a plain ``--version``. Long enough for a 700-shortcut library,
#: short enough that a hung call cannot stall a diagnostics capture.
_LIST_TIMEOUT = 45.0
_VERSION_TIMEOUT = 15.0

#: ``protontricks -l`` output is a per-game listing; on a large library it is
#: long but not huge. Capped so a pathological case cannot bloat the bundle.
_MAX_OUTPUT_CHARS = 8000


def _trim(text: str | None) -> str:
    """Collapse trailing whitespace and cap length, noting any truncation."""
    if not text:
        return ""
    clean = text.strip()
    if len(clean) <= _MAX_OUTPUT_CHARS:
        return clean
    return clean[:_MAX_OUTPUT_CHARS] + f"\n…truncated ({len(clean)} chars total)"


def _owner_dir() -> Path:
    """A path whose owner the demoted subprocesses should run as.

    ``plugin_loader`` may run as root, in which case every probe command has
    to be demoted to the desktop user — a root ``flatpak --user`` query would
    read root's Flatpak state instead of theirs. The prefixes dir is the same
    anchor :mod:`protontricks_access` uses, so both agree on the uid.
    """
    return compat_bridge.PREFIX_ROOT


def _run(argv: list[str], timeout: float) -> tuple[int | None, str, str]:
    """``(returncode, stdout, stderr)``; ``returncode`` is None if it failed."""
    ids_path = _owner_dir()
    try:
        st = ids_path.stat() if ids_path.exists() else Path.home().stat()
    except OSError:
        return None, "", "cannot stat an anchor path to resolve the owner uid"
    proc = run_demoted(argv, st.st_uid, st.st_gid, timeout=timeout)
    if proc is None:
        return None, "", "subprocess did not run (timeout, or binary missing)"
    return proc.returncode, _trim(proc.stdout), _trim(proc.stderr)


def _native_binary() -> str | None:
    """Path to a native/pip ``protontricks``, or None.

    ``shutil.which`` alone is not enough: the backend may run as root with a
    minimal ``PATH`` (``/usr/local/sbin:/usr/local/bin:/usr/bin`` on the
    reporter's box), which misses a ``pip install --user`` at
    ``~/.local/bin``. Missing it would report ``absent`` for a Protontricks
    the user is actively running — the exact ambiguity this probe removes.
    """
    found = shutil.which("protontricks")
    if found:
        return found
    fallback = Path("~/.local/bin/protontricks").expanduser()
    return str(fallback) if fallback.is_file() else None


def _native_block() -> dict[str, Any] | None:
    """Describe a native/pip Protontricks, or None when there isn't one."""
    binary = _native_binary()
    if not binary:
        return None
    rc, out, err = _run([binary, "--version"], _VERSION_TIMEOUT)
    return {
        "kind": "native",
        "path": binary,
        "version": out or err or "unknown",
        "version_rc": rc,
    }


def _flatpak_block() -> dict[str, Any] | None:
    """Describe the Protontricks Flatpak, or None when it isn't installed."""
    rc, out, err = _run(
        ["flatpak", "info", "--show-ref", access.FLATPAK_APP_ID],
        _VERSION_TIMEOUT,
    )
    if rc != 0:
        return None
    return {
        "kind": "flatpak",
        "app_id": access.FLATPAK_APP_ID,
        "ref": out or err or "unknown",
    }


def _distribution() -> dict[str, Any]:
    """Which Protontricks is installed — the ambiguity this probe exists for.

    Both can be installed at once; both are reported, and ``primary`` names
    the one a bare ``protontricks`` command would reach (a native install
    shadows the Flatpak in ``PATH``).
    """
    native = _native_block()
    flatpak = _flatpak_block()
    found = [b for b in (native, flatpak) if b]
    if not found:
        return {"primary": "absent", "installed": []}
    return {
        "primary": found[0]["kind"],
        "installed": found,
    }


def _prefix_bridges(steam_root: Path | None) -> list[dict[str, Any]]:
    """Every compatdata bridge we own, with Protontricks' two gates checked.

    ``find_appid_proton_prefix`` requires ``compatdata/<appid>/pfx`` to be a
    directory, and sorts candidates by the mtime of ``pfx.lock`` beside it. A
    bridge that satisfies neither is invisible to Protontricks even though the
    link looks fine in a directory listing — which is exactly the state that
    is hard to spot by eye.
    """
    if steam_root is None:
        return []
    root = compat_bridge.compatdata_dir(steam_root)
    if not root.is_dir():
        return []
    try:
        entries = sorted(root.iterdir())
    except OSError:
        return []
    rows: list[dict[str, Any]] = []
    for entry in entries:
        if not compat_bridge.is_bridge_link(entry):
            continue
        rows.append({
            "appid": entry.name,
            "target": os.readlink(entry),
            "target_exists": entry.exists(),
            "pfx_is_dir": (entry / "pfx").is_dir(),
            "pfx_lock_is_file": (entry / "pfx.lock").is_file(),
        })
    return rows


def _compat_tool_bridge_block() -> dict[str, Any]:
    """The compat-tool half: the links, and whether the sandbox uses them.

    ``sandbox_access`` is the field that matters when Protontricks reports
    "Active Proton installation could not be found automatically": the links
    are useless unless the Flatpak can both *read* the directory and *search*
    it via ``STEAM_EXTRA_COMPAT_TOOLS_PATHS``. ``partial`` means one of the
    two overrides is missing — a silent failure that looks configured.
    """
    root = compat_tool_bridge.bridge_root()
    block: dict[str, Any] = {
        "root": str(root),
        "root_exists": root.is_dir(),
        "links": compat_tool_bridge.bridged_links(),
        "search_dirs_protontricks_uses": [
            *vdf_compat.SYSTEM_COMPAT_DIRS,
            str(root),
        ],
    }
    try:
        block["sandbox_access"] = access.tool_path_status(_owner_dir(), root)
    except Exception as err:  # optional tooling — report, never raise
        block["sandbox_access"] = f"error: {err!r}"
    return block


def _listing() -> dict[str, Any]:
    """What Protontricks itself reports — its own words, not our inference.

    ``-l`` is the cheapest call that exercises the whole resolution chain
    (Steam root, libraries, shortcuts, prefixes, and the active Proton), so
    its stderr carries the actual error text a reporter would otherwise have
    to be asked for.
    """
    binary = _native_binary()
    argv = (
        [binary, "-l"] if binary
        else [
            "flatpak", "run", "--command=protontricks",
            access.FLATPAK_APP_ID, "-l",
        ]
    )
    rc, out, err = _run(argv, _LIST_TIMEOUT)
    return {
        "argv": " ".join(argv),
        "returncode": rc,
        "stdout": out,
        "stderr": err,
    }


def protontricks_block() -> dict[str, Any]:
    """The full Protontricks readiness report."""
    distribution = _distribution()
    block: dict[str, Any] = {
        "distribution": distribution,
        "prefix_bridge": {
            "root": str(compat_bridge.PREFIX_ROOT),
            "flatpak_prefix_access": _flatpak_prefix_access(),
            "bridges": _prefix_bridges(vdf_compat.resolve_live_steam_root()),
        },
        "compat_tool_bridge": _compat_tool_bridge_block(),
    }
    # Only ask Protontricks itself when there is one to ask: the Flatpak
    # fallback argv would otherwise spend the whole timeout failing.
    block["listing"] = (
        _listing() if distribution["primary"] != "absent"
        else {"skipped": "no Protontricks installed"}
    )
    return block


def _flatpak_prefix_access() -> str:
    """Whether the Flatpak can read the prefixes dir (``has_access``)."""
    root = compat_bridge.PREFIX_ROOT
    if not root.is_dir():
        return "no prefixes yet"
    try:
        return "granted" if access.has_access(root) else "missing"
    except Exception as err:
        return f"error: {err!r}"
