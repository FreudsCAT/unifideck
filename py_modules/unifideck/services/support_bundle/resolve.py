"""support_bundle/resolve.py — Path resolution, device-agnostic.

Every root the registry refers to, resolved through fallback chains
and reported with the label of the rung that won. That label is why
"it looked in the wrong place" is answerable from the bundle alone.

Nothing here is allowed to create a directory it is *searching* for.
Creating an empty ``~/homebrew/logs/Unifideck`` would permanently mask
the real one at a lower rung, so search resolvers only ever test. The
one exception is the destination chain, which must be able to create
``~/Downloads`` on a device where it was deleted.
"""
from __future__ import annotations

import logging
import os
import re
import shutil
import subprocess
import time
from fnmatch import fnmatch
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .spec import BundleContext, SourceSpec

logger = logging.getLogger(__name__)

_DEFAULT_LAUNCHES = "~/.local/share/unifideck/launches"
_DEFAULT_DATA = "~/.local/share/unifideck"
_DEFAULT_EXPORT = "~/Downloads"
_PLUGIN_NAME = "Unifideck"

# Characters allowed in an archive entry name. Decky's log filenames
# contain spaces ("2026-07-23 16.21.34.log"), which break every shell
# pipeline a support engineer will write against the extracted bundle.
_UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")
_GLOB_CHARS = ("*", "?", "[")


def _as_dir(raw: str | None) -> Path | None:
    """Expand ``raw`` and return it only if it is an existing dir."""
    if not raw:
        return None
    try:
        candidate = Path(raw).expanduser()
    except (OSError, RuntimeError):
        return None
    return candidate if candidate.is_dir() else None


def resolve_decky_log_dir() -> tuple[Path | None, str, list[str]]:
    """Locate Decky's per-plugin log directory.

    Decky exports ``DECKY_PLUGIN_LOG_DIR`` but the backend has never
    read it, so all five rungs are exercised in practice depending on
    how the plugin was started. Returns the directory, the winning
    rung's label, and every candidate tried (for the manifest).
    """
    home = Path.home()
    plugin_dir = os.environ.get("DECKY_PLUGIN_DIR")
    sibling = None
    if plugin_dir:
        base = Path(plugin_dir)
        sibling = str(base.parents[1] / "logs" / base.name)
    decky_home = os.environ.get("DECKY_HOME") or str(home / "homebrew")
    name = os.environ.get("DECKY_PLUGIN_NAME") or _PLUGIN_NAME
    rungs = (
        ("DECKY_PLUGIN_LOG_DIR", os.environ.get("DECKY_PLUGIN_LOG_DIR")),
        ("DECKY_HOME", str(Path(decky_home) / "logs" / name)),
        ("plugin_dir_sibling", sibling),
        ("home_default", str(home / "homebrew" / "logs" / _PLUGIN_NAME)),
    )
    tried: list[str] = []
    for label, raw in rungs:
        if not raw:
            continue
        tried.append(f"{raw} ({label})")
        found = _as_dir(raw)
        if found is not None:
            return found, label, tried
    scanned = _scan_logs_case_insensitive(home)
    if scanned is not None:
        tried.append(f"{scanned} (case_insensitive_scan)")
        return scanned, "case_insensitive_scan", tried
    return None, "not_found", tried


def _scan_logs_case_insensitive(home: Path) -> Path | None:
    """Find ``homebrew/logs/<unifideck>`` ignoring case.

    Decky's own docs spell the plugin dir lowercase while our
    ``plugin.json`` name is capitalised; a case-mismatched install
    would otherwise report "no logs found" with the logs right there.
    """
    logs = home / "homebrew" / "logs"
    if not logs.is_dir():
        return None
    try:
        for child in logs.iterdir():
            if child.is_dir() and child.name.lower() == _PLUGIN_NAME.lower():
                return child
    except OSError as err:
        logger.debug("[support_bundle] scan %s failed: %s", logs, err)
    return None


def _cfg_str(config: Any, key: str, default: str) -> str:
    """Read a string config key, tolerating a missing manager."""
    if config is None or not hasattr(config, "get_str"):
        return default
    value = config.get_str(key, default)
    return value or default


def paths_field(paths: Any, name: str) -> Path | None:
    """Resolve a named :class:`ServicePaths` field to a path."""
    raw = getattr(paths, name, None) if paths is not None else None
    if not raw:
        return None
    try:
        return Path(str(raw)).expanduser()
    except (OSError, RuntimeError):
        return None


def build_roots(
    config: Any, paths: Any,
) -> tuple[dict[str, str | None], dict[str, str], list[str]]:
    """Resolve every registry root once.

    Returns ``(roots, root_sources, decky_candidates)``. Roots that
    fail to resolve are recorded as ``None`` rather than omitted, so
    the audit can say "this root was unresolvable" instead of silently
    dropping every row that depends on it.
    """
    decky_dir, decky_src, tried = resolve_decky_log_dir()
    data, data_src = _first_of(
        ("paths.data_dir", paths_field(paths, "data_dir")),
        ("config", Path(_cfg_str(config, "paths.data_dir", _DEFAULT_DATA)).expanduser()),
    )
    steam, steam_src = _first_of(
        ("paths.steam_root", paths_field(paths, "steam_root")),
        ("resolve_live_steam_root", _live_steam_root()),
    )
    plugin, plugin_src = _first_of(
        ("paths.plugin_dir", paths_field(paths, "plugin_dir")),
        ("resolve_plugin_dir", _plugin_dir()),
    )
    launches = Path(
        _cfg_str(config, "logs.archive_path", _DEFAULT_LAUNCHES),
    ).expanduser()
    roots: dict[str, str | None] = {
        "decky_logs": str(decky_dir) if decky_dir else None,
        "launches": str(launches),
        "data": str(data) if data else None,
        "config": str(_user_config_dir()),
        "steam": str(steam) if steam else None,
        "plugin": str(plugin) if plugin else None,
        "home": str(Path.home()),
    }
    sources = {
        "decky_logs": decky_src,
        "launches": "logs.archive_path",
        "data": data_src,
        "config": "resolve_user_config_path",
        "steam": steam_src,
        "plugin": plugin_src,
        "home": "Path.home",
    }
    return roots, sources, tried


def _first_of(*candidates: tuple[str, Path | None]) -> tuple[Path | None, str]:
    """Return the first resolved candidate with its own label.

    Keeps the label honest: an earlier version hardcoded
    ``paths.steam_root`` as the source even when the value had actually
    come from the liveness probe, which is exactly the kind of detail a
    reader would rely on when a root resolves to the wrong place.
    """
    for label, value in candidates:
        if value is not None:
            return value, label
    return None, "unresolved"


def _plugin_dir() -> Path | None:
    """Fall back to the plugin-directory resolver."""
    from unifideck.core.paths import resolve_plugin_dir

    try:
        return resolve_plugin_dir()
    except Exception:
        logger.debug("[support_bundle] plugin dir probe failed", exc_info=True)
        return None


def _user_config_dir() -> Path:
    """Return the directory holding the user's ``config.json``."""
    from unifideck.config.user_config_path import resolve_user_config_path

    return Path(resolve_user_config_path()).expanduser().parent


def _live_steam_root() -> Path | None:
    """Fall back to the liveness-ranked Steam root probe."""
    from unifideck.utils.vdf_compat import resolve_live_steam_root

    try:
        return resolve_live_steam_root()
    except Exception:
        logger.debug("[support_bundle] steam root probe failed", exc_info=True)
        return None


def _is_glob(pattern: str) -> bool:
    """True when ``pattern`` needs globbing rather than a join."""
    return any(ch in pattern for ch in _GLOB_CHARS)


def expand(spec: SourceSpec, ctx: BundleContext) -> list[Path]:
    """Resolve one registry row to **every** matching path.

    Literal patterns return their single path even when it does not
    exist — the audit needs to report it as MISSING. Glob patterns
    return everything on disk.

    Deliberately does two things it would be easy to get wrong:

    * no deny-list filtering, so a credential file is still
      *stat-able* and the audit can report that it exists. The deny
      check happens at read time, in the collector;
    * no newest-N limiting. That belongs to the collector, which
      records what it excluded. Applying it here made the audit report
      ``present(20)`` for a directory holding 21 files — understating
      the device to the person reading the bundle.
    """
    if spec.root == "generated":
        return []
    if spec.root == "paths":
        found = paths_field(ctx.paths, spec.pattern)
        return [found] if found is not None else []
    root = ctx.root(spec.root)
    if root is None:
        return []
    base = Path(root)
    if not _is_glob(spec.pattern):
        return [base / spec.pattern]
    try:
        matches = sorted(base.glob(spec.pattern))
    except OSError as err:
        logger.debug("[support_bundle] glob %s failed: %s", spec.key, err)
        return []
    if spec.exclude:
        matches = [p for p in matches if not fnmatch(p.name, spec.exclude)]
    return matches


def split_newest(
    paths: list[Path], newest_n: int,
) -> tuple[list[Path], list[Path]]:
    """Split ``paths`` into the newest ``newest_n`` and the remainder.

    Returns both halves so the caller can record what it left out. A
    cap that discards files without saying so makes a bundle look
    complete when it is not, which is worse than a smaller bundle.
    """
    if not newest_n or len(paths) <= newest_n:
        return paths, []
    ordered = sorted(paths, key=_mtime_or_zero, reverse=True)
    return ordered[:newest_n], ordered[newest_n:]


def _mtime_or_zero(path: Path) -> float:
    """Sort key that survives a file vanishing mid-capture."""
    try:
        return path.stat().st_mtime
    except OSError:
        return 0.0


def sanitize_entry_name(name: str) -> str:
    """Make ``name`` safe as a zip entry component."""
    collapsed = _UNSAFE_NAME.sub("_", name)
    return collapsed or "unnamed"


def _writable_dir(path: Path) -> bool:
    """True when ``path`` is a directory we can create files in."""
    return path.is_dir() and os.access(path, os.W_OK | os.X_OK)


def _xdg_download_dir() -> str | None:
    """Ask ``xdg-user-dir`` for the localised Downloads folder.

    Handles the case where the user's Downloads folder is named in
    their own language. Skipped entirely when the helper is absent.
    """
    if shutil.which("xdg-user-dir") is None:
        return None
    try:
        done = subprocess.run(
            ["xdg-user-dir", "DOWNLOAD"],
            capture_output=True, text=True, timeout=2, check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        logger.debug("[support_bundle] xdg-user-dir failed: %s", err)
        return None
    return done.stdout.strip() or None


def _dest_candidates(
    dest_path: str, config: Any, paths: Any,
) -> list[tuple[str, str, bool]]:
    """Build the ordered ``(label, raw_path, may_create)`` chain."""
    data_dir = paths_field(paths, "data_dir")
    return [
        ("explicit", dest_path, True),
        ("config", _cfg_str(config, "logs.export_path", _DEFAULT_EXPORT), True),
        ("xdg", _xdg_download_dir() or "", False),
        ("home_downloads", str(Path.home() / "Downloads"), True),
        ("home", str(Path.home()), False),
        ("data_dir", str(data_dir) if data_dir else "", False),
    ]


def resolve_dest(
    dest_path: str, config: Any, paths: Any,
) -> tuple[Path, str, list[str]]:
    """Pick a writable destination directory.

    An empty ``dest_path`` skips the explicit rung entirely — it never
    degrades into ``Path.home() / ""``, which is how the older
    single-file export ended up writing bare logs into ``$HOME``.

    Raises:
        OSError: when every rung is unusable. The caller turns this
            into ``bundle_dest_unwritable`` with the full list tried.
    """
    tried: list[str] = []
    for label, raw, may_create in _dest_candidates(dest_path, config, paths):
        if not raw:
            continue
        tried.append(f"{raw} ({label})")
        resolved = _try_dest(raw, may_create)
        if resolved is not None:
            return resolved, label, tried
    raise OSError(f"no writable destination; tried {tried}")


def _try_dest(raw: str, may_create: bool) -> Path | None:
    """Return ``raw`` as a usable directory, creating it if allowed."""
    try:
        candidate = Path(raw).expanduser()
    except (OSError, RuntimeError):
        return None
    if candidate.suffix == ".zip":
        candidate = candidate.parent
    if _writable_dir(candidate):
        return candidate
    if not may_create:
        return None
    try:
        candidate.mkdir(parents=True, exist_ok=True)
    except OSError:
        return None
    return candidate if _writable_dir(candidate) else None


def archive_name(dest_path: str) -> str:
    """Build the archive filename, honouring an explicit ``.zip``.

    Uses ``time.strftime`` rather than ``datetime`` on purpose: the
    project's lint gate rejects naive ``datetime.utcnow()`` and a
    local-time filename is what the user sees in their file manager.
    """
    if dest_path and dest_path.endswith(".zip"):
        return Path(dest_path).name
    stamp = time.strftime("%Y%m%d-%H%M%S")
    return f"unifideck-logs-{stamp}.zip"


def unique_path(dest_dir: Path, name: str) -> Path:
    """Return a non-colliding path inside ``dest_dir``.

    Two captures inside the same second are rare but a lost bundle is
    worse than an ugly filename, so the suffix walk is unconditional.
    """
    target = dest_dir / name
    if not target.exists():
        return target
    stem, suffix = target.stem, target.suffix
    for index in range(2, 10):
        alt = dest_dir / f"{stem}-{index}{suffix}"
        if not alt.exists():
            return alt
    return dest_dir / f"{stem}-{os.getpid()}{suffix}"
