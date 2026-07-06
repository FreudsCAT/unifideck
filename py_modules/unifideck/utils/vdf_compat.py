"""utils/vdf_compat.py — Steam config.vdf + compatibilitytools.d parsing.

Shared and dependency-light (stdlib + the vendored ``vdf`` KeyValues
parser only) so BOTH the Decky backend (bundled Python) and the
out-of-process game launcher (system ``/usr/bin/python3``) can import
it — ``utils/`` is already on the launcher's import path. Nothing here
may pull in ``aiohttp`` or the ``compatibility`` package; that would
break the launcher process, which imports these helpers directly.

Two cross-distro concerns live here:

1. **Steam root / config.vdf discovery.** SteamOS keeps Steam at
   ``~/.steam/steam``; on Bazzite/CachyOS (native Steam) that symlink
   usually exists too, but ``~/.local/share/Steam`` and the Flatpak
   path are probed as well so resolution never hard-codes one layout.

2. **Compat-tool enumeration** that understands ``compatibilitytool.vdf``
   manifests and the system-wide ``/usr/share/steam/compatibilitytools.d``
   directory where CachyOS's ``proton-cachyos`` package installs. Steam
   itself only lists the user dirs, so distro packaging drops a loose
   ``.vdf`` there whose ``install_path`` points at the system dir. A
   plain directory scan (the pre-0.7.1 behaviour) misses both, so a
   force-selected Proton-CachyOS/GE silently fell through to GE-latest.
"""
from __future__ import annotations

import logging
import re
from collections.abc import Iterator
from pathlib import Path

logger = logging.getLogger(__name__)

# Candidate Steam roots, most-specific first. ``~/.steam/steam`` and
# ``~/.steam/root`` are the symlinks Steam maintains; the share dir is
# the real target on most distros; the last is Flatpak Steam. Kept as a
# single source of truth so ``steam.library.find_steam_path`` and the
# launcher agree (and match ``defaults/config.json``'s advertised list).
STEAM_ROOT_CANDIDATES: tuple[str, ...] = (
    "~/.steam/steam",
    "~/.steam/root",
    "~/.local/share/Steam",
    "~/.var/app/com.valvesoftware.Steam/.steam/steam",
)

# System-wide compat-tool dirs populated by distro packages (CachyOS
# ``proton-cachyos``, Arch ``proton-ge-custom``). Steam does not scan
# these, so tooling that resolves Proton independently must.
SYSTEM_COMPAT_DIRS: tuple[str, ...] = (
    "/usr/share/steam/compatibilitytools.d",
    "/usr/local/share/steam/compatibilitytools.d",
)


def find_steam_root() -> Path | None:
    """First candidate Steam root that has a ``steamapps`` dir, else ``None``.

    Launcher-safe twin of ``steam.library.find_steam_path`` (which pulls
    in ``aiohttp`` at import and so cannot run in the launcher process).
    """
    for candidate in STEAM_ROOT_CANDIDATES:
        root = Path(candidate).expanduser()
        if (root / "steamapps").is_dir():
            return root
    return None


def find_steam_config_vdf() -> Path | None:
    """Global ``config/config.vdf`` under the resolved Steam root, or ``None``.

    ``CompatToolMapping`` (both per-app and the ``"0"`` global default)
    lives in this file — NOT in the per-user ``localconfig.vdf``.
    """
    root = find_steam_root()
    if root is None:
        return None
    cfg = root / "config" / "config.vdf"
    return cfg if cfg.is_file() else None


def _extract_kv_block(content: str, start: int) -> str:
    """Return the balanced ``{ … }`` block beginning at/after *start*.

    Respects nested per-appid blocks (a ``[^}]*`` regex would stop at
    the first inner ``}``); ``""`` when no balanced block is found.
    """
    open_brace = content.find("{", start)
    if open_brace < 0:
        return ""
    depth = 0
    for i in range(open_brace, len(content)):
        ch = content[i]
        if ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                return content[open_brace:i + 1]
    return ""


def parse_compat_tool(content: str, appid: int) -> str:
    """Return the per-app ``CompatToolMapping[appid]`` tool name, or ``""``."""
    if not content:
        return ""
    appid_str = str(appid)
    if f'"{appid_str}"' not in content:
        return ""
    marker = '"CompatToolMapping"'
    marker_pos = content.find(marker)
    if marker_pos < 0:
        return ""
    pattern = re.compile(rf'"{appid_str}"\s*\{{([^}}]*)\}}', re.DOTALL)
    m = pattern.search(content, marker_pos)
    if not m:
        return ""
    name_match = re.search(r'"name"\s+"([^"]*)"', m.group(1))
    return name_match.group(1) if name_match else ""


def parse_global_default_compat_tool(content: str) -> str:
    """Return the global-default tool (``CompatToolMapping["0"]``), or ``""``.

    Bazzite/CachyOS ship this pre-set (e.g. ``Proton-CachyOS``). Bounded
    to the ``CompatToolMapping`` block via ``_extract_kv_block`` so an
    unrelated ``"0" { … }`` elsewhere in ``config.vdf`` can't false-match.
    """
    if not content:
        return ""
    marker = '"CompatToolMapping"'
    marker_pos = content.find(marker)
    if marker_pos < 0:
        return ""
    block = _extract_kv_block(content, marker_pos)
    if not block:
        return ""
    m = re.search(r'"0"\s*\{([^}]*)\}', block, re.DOTALL)
    if not m:
        return ""
    name_match = re.search(r'"name"\s+"([^"]*)"', m.group(1))
    return name_match.group(1) if name_match else ""


def _manifest_tools(manifest: Path, base_dir: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(name, proton_path)`` for each tool in one ``.vdf`` manifest.

    Maps BOTH the internal name (the ``compat_tools`` key) and the
    ``display_name`` to the resolved ``proton`` script, following
    ``install_path`` (``"."``/relative → *base_dir*; absolute → verbatim).
    Only yields tools whose ``proton`` script actually exists. Never raises.
    """
    try:
        import vdf
        with manifest.open(encoding="utf-8", errors="ignore") as f:
            data = vdf.load(f)  # type: ignore[no-untyped-call]  # vendored vdf is untyped
    except Exception as e:
        logger.debug("[vdf_compat] manifest %s parse failed: %s", manifest, e)
        return
    root = data.get("compatibilitytools", {}) if isinstance(data, dict) else {}
    tools = root.get("compat_tools", {}) if isinstance(root, dict) else {}
    if not isinstance(tools, dict):
        return
    for internal_name, spec in tools.items():
        if not isinstance(spec, dict):
            continue
        install_path = str(spec.get("install_path", ".") or ".")
        tool_dir = (
            Path(install_path)
            if Path(install_path).is_absolute()
            else base_dir / install_path
        )
        proton = (tool_dir / "proton").expanduser()
        if not proton.is_file():
            continue
        for name in (internal_name, str(spec.get("display_name", ""))):
            if name:
                yield name, proton


def _entry_tools(entry: Path, root: Path) -> Iterator[tuple[str, Path]]:
    """Yield ``(name, proton)`` for one directory entry under a compat root.

    A tool dir with a ``compatibilitytool.vdf`` (manifest names first, then
    the dir name as a bare fallback), or a loose top-level ``*.vdf`` manifest.
    """
    if entry.is_dir():
        manifest = entry / "compatibilitytool.vdf"
        if manifest.is_file():
            yield from _manifest_tools(manifest, entry)
        bare = entry / "proton"
        if bare.is_file():
            yield entry.name, bare
    elif entry.suffix == ".vdf":
        yield from _manifest_tools(entry, root)


def iter_compat_tools(roots: list[Path] | tuple[Path, ...]) -> dict[str, Path]:
    """Map every compat-tool name found under *roots* to its ``proton`` script.

    Handles three shapes seen across distros: a tool directory holding a
    ``compatibilitytool.vdf`` (per-dir manifest), a loose top-level
    ``*.vdf`` manifest whose ``install_path`` points elsewhere (how
    CachyOS's ``proton-cachyos`` is registered into the user dir), and a
    bare directory with a ``proton`` script and no manifest. Keys include
    internal names, display names, and directory names so a
    ``CompatToolMapping`` value resolves however Steam wrote it. Earlier
    roots win on name collisions (user dir before system dir).
    """
    result: dict[str, Path] = {}
    for raw_root in roots:
        root = Path(raw_root).expanduser()
        if not root.is_dir():
            continue
        try:
            entries = sorted(root.iterdir())
        except OSError:
            continue
        for entry in entries:
            for name, proton in _entry_tools(entry, root):
                result.setdefault(name, proton)
    return result


def resolve_compat_tool(
    tool_id: str, roots: list[Path] | tuple[Path, ...],
) -> Path | None:
    """Resolve *tool_id* to its ``proton`` script under *roots*.

    Exact match first, then case-insensitive (Steam's stored name and a
    tool's directory/display name occasionally differ only in case).
    """
    if not tool_id:
        return None
    tools = iter_compat_tools(roots)
    exact = tools.get(tool_id)
    if exact is not None:
        return exact
    lowered = tool_id.lower()
    for name, proton in tools.items():
        if name.lower() == lowered:
            return proton
    return None
