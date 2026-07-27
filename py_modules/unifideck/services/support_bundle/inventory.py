"""support_bundle/inventory.py — What exists that we did not collect.

The deliberate counterpart to the collector. Every artifact we exclude
by policy — Wine prefixes, the browser profile, save data, installed
games — still gets *enumerated*: name, size, mtime, mode. Never a byte
of content.

The reasoning is that "we did not ship this" and "this is not there"
are completely different answers to a support question, and the audit
only distinguishes them for paths someone thought to register. This
module closes the rest of the gap: it walks the excluded areas so an
engineer can see a save backup exists, a prefix was built, or a game
directory is empty, without any of it leaving the device.

Two hard rules:

* **Names and metadata only.** Nothing here opens a file. Contents of
  prefixes and browser profiles stay on the device.
* **Bounded.** Each root has its own depth and entry cap, because a
  Wine prefix holds tens of thousands of files. Whenever a cap trims
  the walk it is stated in the output rather than silently applied.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any, NamedTuple

logger = logging.getLogger(__name__)

# Per-root entry cap. Deep enough to be useful, bounded enough that a
# prefix's drive_c cannot flood the report.
_ENTRY_CAP = 400

# Known state inside a Ubisoft prefix. Checked by name rather than
# found by walking, because their *absence* is the diagnostic: a
# prefix with upc.exe but no ownership file has Ubisoft Connect
# installed and no entitlement data, which is a specific failure.
_UPC_PROBES = (
    "pfx/drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe",
    "pfx/drive_c/ProgramData/Ubisoft/Ubisoft Game Launcher/settings.yml",
    "pfx/drive_c/ProgramData/Ubisoft/Ubisoft Game Launcher/ownership",
    "pfx/drive_c/ProgramData/Ubisoft/Ubisoft Game Launcher/logs",
    "pfx/drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher",
    "pfx/drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher/cache/http2",
    "config_info",
    "version",
    "tracked_files",
)


class Root(NamedTuple):
    """One directory tree to enumerate."""

    label: str
    path: str
    depth: int
    note: str = ""


def _stat_line(path: Path, indent: str) -> str:
    """One ``kind name size mtime mode`` row."""
    try:
        info = path.stat()
    except OSError as err:
        return f"{indent}{path.name}  <unreadable: {err.strerror}>"
    kind = "dir " if path.is_dir() else "file"
    stamp = time.strftime("%Y-%m-%d %H:%M", time.localtime(info.st_mtime))
    return (
        f"{indent}{kind} {path.name:<44} {info.st_size:>12}  {stamp}  "
        f"{oct(info.st_mode & 0o7777)}"
    )


def _walk(root: Path, depth: int, budget: list[int], indent: str = "  ") -> list[str]:
    """Enumerate ``root`` to ``depth``, spending from ``budget``."""
    rows: list[str] = []
    try:
        children = sorted(root.iterdir())
    except OSError as err:
        return [f"{indent}<unreadable: {err.strerror}>"]
    for child in children:
        if budget[0] <= 0:
            rows.append(f"{indent}... (entry cap reached, {_ENTRY_CAP} shown)")
            return rows
        budget[0] -= 1
        rows.append(_stat_line(child, indent))
        if child.is_dir() and depth > 1:
            rows.extend(_walk(child, depth - 1, budget, indent + "  "))
    return rows


def _roots(ctx: Any, install_dirs: list[str]) -> list[Root]:
    """Every tree worth enumerating, with its own depth."""
    data = ctx.root("data")
    home = ctx.root("home") or str(Path.home())
    roots = [
        Root("data", data or "", 2),
        Root("config", ctx.root("config") or "", 1),
        Root("decky_logs", ctx.root("decky_logs") or "", 1),
        Root("launches", ctx.root("launches") or "", 1),
    ]
    if data:
        roots.extend([
            # Depth 2 lists every prefix and, for the namespaced
            # Ubisoft layout, every prefix inside it. Depth 3 descended
            # into drive_c and burned the whole entry budget on one
            # prefix, truncating the list it was meant to show.
            Root("prefixes", f"{data}/prefixes", 2, "excluded from the archive"),
            Root("edge_auth_profile", f"{data}/edge-auth", 1,
                 "browser profile, excluded (holds cookies)"),
            Root("saves", f"{data}/saves", 2, "excluded from the archive"),
            Root("save_backups", f"{data}/save_backups", 2,
                 "excluded from the archive"),
            Root("ubisoft_installer_cache", f"{data}/ubisoft_installer_cache", 1),
        ])
    steam = ctx.root("steam")
    if steam:
        # ~186 MB, nearly all CEF/webhelper noise, so only the launch,
        # compat and install logs are collected. Enumerating the whole
        # directory means an engineer can still ask for a specific one.
        roots.append(
            Root("steam_logs", f"{steam}/logs", 1,
                 "only compat/console/content/gameprocess/cloud/shader collected"),
        )
    roots.extend([
        Root("legendary_config", f"{home}/.config/legendary", 1),
        Root("nile_config", f"{home}/.config/nile", 1),
        Root("umu_runtime", f"{home}/.local/share/umu", 1,
             "runtime payload excluded; completeness is checked instead"),
    ])
    # Depth 1: the complete list of installed games. Descending into
    # them spent the budget on one game's file tree and cut the list
    # off partway, losing the very thing it was there to show.
    roots.extend(
        Root(f"install_location[{index}]", raw, 1, "installed games")
        for index, raw in enumerate(install_dirs)
    )
    return roots


def build_inventory(ctx: Any, install_dirs: list[str]) -> str:
    """Render the existence inventory."""
    lines = [
        "EXISTENCE INVENTORY",
        "=" * 60,
        "",
        "Names, sizes and modes only - no file contents are read here.",
        "This covers what is on the device but deliberately NOT collected",
        "(Wine prefixes, the browser profile, save data, installed games),",
        "so 'we did not ship it' can be told apart from 'it is not there'.",
        "",
    ]
    for root in _roots(ctx, install_dirs):
        lines.extend(_render_root(root))
    lines.extend(_render_upc(ctx))
    return "\n".join(lines) + "\n"


def _render_root(root: Root) -> list[str]:
    """Header plus enumeration for one root."""
    suffix = f"  ({root.note})" if root.note else ""
    if not root.path:
        return [f"[{root.label}] <unresolved>{suffix}", ""]
    path = Path(root.path)
    if not path.exists():
        return [f"[{root.label}] {root.path}{suffix}", "  <does not exist>", ""]
    if not path.is_dir():
        return [f"[{root.label}] {root.path}{suffix}", _stat_line(path, "  "), ""]
    budget = [_ENTRY_CAP]
    return [f"[{root.label}] {root.path}{suffix}", *_walk(path, root.depth, budget), ""]


def _render_upc(ctx: Any) -> list[str]:
    """Existence of known Ubisoft Connect state, per prefix.

    Probed by name because absence is the signal. Ubisoft is the only
    store whose prefixes are namespaced a level deeper, and its install
    and sign-in failures usually come down to which of these exist.
    """
    data = ctx.root("data")
    if not data:
        return []
    base = Path(data) / "prefixes" / "ubisoft"
    if not base.is_dir():
        return ["[ubisoft_upc_state] no Ubisoft prefixes on this device", ""]
    lines = ["[ubisoft_upc_state] per-prefix Ubisoft Connect state (existence only)"]
    try:
        prefixes = sorted(child for child in base.iterdir() if child.is_dir())
    except OSError as err:
        return [*lines, f"  <unreadable: {err.strerror}>", ""]
    for prefix in prefixes:
        lines.append(f"  {prefix.name}")
        lines.extend(f"    {row}" for row in _upc_rows(prefix))
    lines.append("")
    return lines


def _upc_rows(prefix: Path) -> list[str]:
    """One existence row per known UPC path inside ``prefix``."""
    rows: list[str] = []
    for relative in _UPC_PROBES:
        target = prefix / relative
        try:
            info = target.stat()
        except OSError:
            rows.append(f"absent   {relative}")
            continue
        size = "dir" if target.is_dir() else f"{info.st_size} bytes"
        rows.append(f"EXISTS   {relative}  ({size})")
    return rows
