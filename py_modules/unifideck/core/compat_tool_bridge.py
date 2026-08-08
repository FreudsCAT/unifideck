r"""Make the Proton we launch with visible to a sandboxed Protontricks.

py_modules/unifideck/core/compat_tool_bridge.py

``core.compat_bridge`` gets Protontricks as far as *finding the prefix*. It
still has to find the **Proton** the shortcut runs under, and that is a
separate lookup with its own failure mode.

Unifideck deliberately writes no per-app ``CompatToolMapping`` entry (a
per-game override adopted from Steam's *global* default is how a distro's
system Proton leaked onto every shortcut). So Protontricks resolves Proton
the same way we do — off Steam's config — and lands on the same internal
tool name, e.g. ``proton-cachyos-11.0-20260703-slr-x86_64``. It then has to
match that name against a tool it can actually see, or it gives up with::

    Could not find configured Proton installation!
    Active Proton installation could not be found automatically.

Protontricks searches, in order: ``/usr/share/steam/compatibilitytools.d``,
``/usr/local/share/steam/compatibilitytools.d``, every path in
``STEAM_EXTRA_COMPAT_TOOLS_PATHS``, then ``<steam_root>/compatibilitytools.d``.
On CachyOS/Arch the tool is a distro package under ``/usr/share/steam`` — and
the Protontricks **Flatpak** cannot see ``/usr`` at all. Verified on-device:
``flatpak run --filesystem=/usr/share/steam/compatibilitytools.d:ro`` leaves
the path invisible inside the sandbox, because Flatpak silently ignores
filesystem grants under ``/usr``. There is no override that fixes it.

What does work is the env-var entry in that search list. This module keeps a
directory of symlinks::

    ~/.local/share/unifideck/protontricks-tools/unifideck-bridge-<tool> -> <tool dir>

and :mod:`unifideck.services.protontricks_access` grants the Flatpak
read access to it plus ``STEAM_EXTRA_COMPAT_TOOLS_PATHS``. Two properties
make this safe rather than clever:

* Protontricks reads a tool's internal name from the **target's own**
  ``compatibilitytool.vdf`` (``compat_tools`` key), not from the directory
  name — so the ``unifideck-bridge-`` prefix marks the link as provably ours
  (nothing else creates one) while the tool still registers under the exact
  name Steam recorded. Verified in-sandbox against Protontricks 1.14.1's
  ``get_custom_compat_tool_installations_in_dir``.
* **Steam never scans this directory.** An earlier draft bridged into
  ``<steam_root>/compatibilitytools.d`` — reachable, but Steam scans that
  too, so the same internal name would have been registered twice. Our own
  directory has no such side effect. It is also kept separate from
  ``~/.local/share/unifideck/compat-tools`` (the managed-GE install dir the
  *selector* searches) so a bridge link can never shadow a real tool during
  our own Proton resolution.

Stdlib-only by design — imported both by the Decky backend (bundled Python
3.11) and by the out-of-process launcher (system ``/usr/bin/python3``,
3.10-3.14), same constraint as ``core/compat_bridge.py`` and ``core/paths.py``.

All functions are synchronous, do blocking I/O, and are best-effort: a
failure here must never fail a launch, an install, or a sync.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

#: Where the bridge links live. Deliberately NOT
#: ``~/.local/share/unifideck/compat-tools`` — that is the managed-GE install
#: dir which ``launcher.proton.infrastructure.selector`` searches, and a
#: bridge link in there could shadow the very tool it points at.
BRIDGE_ROOT = Path("~/.local/share/unifideck/protontricks-tools").expanduser()

#: Link-name prefix. Ownership proof: Steam, ProtonUp-Qt and distro packages
#: all install real directories, and none of them uses this prefix, so a link
#: carrying it is ours and only ours to remove.
LINK_PREFIX = "unifideck-bridge-"

#: The manifest Protontricks (and Steam) read to learn a tool's internal
#: name. A tool without one cannot be bridged: there would be no name for
#: Protontricks to match against Steam's config.
TOOL_MANIFEST = "compatibilitytool.vdf"

#: Compat-tool roots already inside the Protontricks Flatpak's own
#: ``filesystems=`` allowlist (``~/.steam`` and ``~/.local/share/Steam``). A
#: tool under one of these needs no bridge — this is the GE-Proton /
#: ProtonUp-Qt case, i.e. the overwhelming majority.
_SANDBOX_VISIBLE_ROOTS: tuple[str, ...] = (
    "~/.steam",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam",
)


def bridge_root() -> Path:
    """The directory holding our bridge links (may not exist yet)."""
    return BRIDGE_ROOT


def link_name(tool_name: str) -> str:
    """Link basename for *tool_name*, with path separators neutralised.

    Only uniqueness and the ownership prefix matter here — the name
    Protontricks registers the tool under comes from the target's own
    manifest, never from this — so a Steam config string is safe to use as
    long as it cannot escape :data:`BRIDGE_ROOT`.
    """
    return LINK_PREFIX + tool_name.replace("/", "_")


def is_sandbox_visible(tool_dir: Path | str) -> bool:
    """True iff *tool_dir* already sits where a sandboxed Protontricks looks.

    Used as the skip gate: bridging a tool Protontricks can already see would
    add a second registration of the same internal name for no benefit.
    """
    try:
        resolved = Path(tool_dir).expanduser().resolve()
    except (OSError, RuntimeError):
        return False
    for raw in _SANDBOX_VISIBLE_ROOTS:
        root = Path(raw).expanduser()
        try:
            resolved.relative_to(root.resolve())
            return True
        except (ValueError, OSError, RuntimeError):
            continue
    return False


def _is_ours(link: Path) -> bool:
    """True iff *link* is a bridge link this module created."""
    return link.name.startswith(LINK_PREFIX) and link.is_symlink()


def _points_at(link: Path, tool_dir: Path) -> bool:
    """True iff *link* is a symlink already resolving to *tool_dir*."""
    try:
        return link.is_symlink() and link.resolve() == tool_dir.resolve()
    except (OSError, RuntimeError):
        return False


def link_tool(
    tool_dir: Path | str | None, tool_name: str | None = None,
) -> str:
    """Expose *tool_dir* to Protontricks under its own internal name.

    *tool_name* only names the link; it defaults to the tool's directory
    name. The name Protontricks matches against Steam's config is read from
    the target's ``compatibilitytool.vdf``, so passing a stale or missing
    name cannot break the match — it only makes the log less readable.

    Returns the action taken, for logging and tests:

    ``"noop"``       the link is already correct — the steady state;
    ``"created"``    link created;
    ``"repointed"``  a stale bridge link now points at *tool_dir*;
    ``"skipped"``    missing inputs, no ``compatibilitytool.vdf`` (an official
                     Valve Proton — Protontricks finds those as Steam apps),
                     or the tool is already somewhere Protontricks can see;
    ``"failed"``     the filesystem refused the operation.

    Never raises: callers sit on the launch and sync hot paths.
    """
    tool = _bridgeable(tool_dir)
    if tool is None:
        return "skipped"
    name = tool_name or tool.name
    if not name:
        return "skipped"

    link = BRIDGE_ROOT / link_name(name)
    if _points_at(link, tool):
        return "noop"
    action = _place_link(link, tool)
    if action not in ("created", "repointed"):
        return action

    logger.info(
        "[compat_tool_bridge] %s %s -> %s (internal name from %s)",
        action, link.name, tool, TOOL_MANIFEST,
    )
    return action


def _bridgeable(tool_dir: Path | str | None) -> Path | None:
    """The tool directory to bridge, or ``None`` when it needs no bridge.

    The precondition ladder, split out of :func:`link_tool` to keep that
    function inside the project's fan-out cap. Three separate reasons to
    decline, all of them normal rather than errors.
    """
    if not tool_dir:
        return None
    tool = Path(tool_dir).expanduser()
    if not tool.is_dir():
        return None
    if not (tool / TOOL_MANIFEST).is_file():
        # Official Protons ship only a toolmanifest.vdf, so there is no
        # internal name to register and nothing for Protontricks to match.
        # It discovers those from their appmanifest instead.
        logger.debug(
            "[compat_tool_bridge] %s has no %s, not bridging",
            tool, TOOL_MANIFEST,
        )
        return None
    if is_sandbox_visible(tool):
        return None
    return tool


def _place_link(link: Path, tool: Path) -> str:
    """Point *link* at *tool*. ``"created"``/``"repointed"``/``"failed"``."""
    action = "created"
    try:
        BRIDGE_ROOT.mkdir(parents=True, exist_ok=True)
        if link.is_symlink():
            link.unlink()
            action = "repointed"
        elif link.exists():
            # Never displace a real directory — same rule as
            # ``compat_bridge.unlink_prefix``: if it is not our symlink, it is
            # not ours to move.
            logger.warning(
                "[compat_tool_bridge] %s is not a symlink, leaving it alone",
                link,
            )
            return "failed"
        link.symlink_to(tool, target_is_directory=True)
    except OSError:
        logger.exception(
            "[compat_tool_bridge] link %s -> %s failed", link, tool,
        )
        return "failed"
    return action


def prune_dead_links() -> int:
    """Delete bridge links whose tool directory no longer exists.

    Repairs the case where a Proton build was removed or upgraded in place
    (ProtonUp-Qt replacing a GE build, a distro package upgrade renaming its
    versioned directory). Returns the number of links removed. Only ever
    touches links carrying :data:`LINK_PREFIX`.
    """
    if not BRIDGE_ROOT.is_dir():
        return 0
    try:
        entries = list(BRIDGE_ROOT.iterdir())
    except OSError:
        logger.exception("[compat_tool_bridge] cannot list %s", BRIDGE_ROOT)
        return 0
    removed = 0
    for entry in entries:
        if not _is_ours(entry) or entry.exists():  # exists() follows the link
            continue
        try:
            entry.unlink()
            removed += 1
            logger.info("[compat_tool_bridge] pruned dead link %s", entry.name)
        except OSError:
            logger.exception("[compat_tool_bridge] prune(%s) failed", entry)
    return removed


def bridged_links() -> list[dict[str, object]]:
    """Describe every bridge link, for the support bundle.

    Each row carries the link name, its target, and whether that target still
    exists and still declares a tool manifest — the two things that decide
    whether Protontricks can use it.
    """
    if not BRIDGE_ROOT.is_dir():
        return []
    try:
        entries = sorted(BRIDGE_ROOT.iterdir())
    except OSError:
        return []
    rows: list[dict[str, object]] = []
    for entry in entries:
        if not _is_ours(entry):
            continue
        target = os.readlink(entry) if entry.is_symlink() else ""
        rows.append({
            "link": entry.name,
            "target": target,
            "target_exists": entry.exists(),
            "has_manifest": (entry / TOOL_MANIFEST).is_file(),
        })
    return rows
