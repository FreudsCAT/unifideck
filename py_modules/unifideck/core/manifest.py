"""core/manifest.py — Unifideck game manifest format.

Moved from discovery/startup.py and renamed. The
old location was a solo-file subpackage and the old name only
captured one of the module's two roles (the boot-time scan),
hiding the fact that `write_manifest()` is actually called
throughout the plugin lifetime — every time a store installs a
game. The new name foregrounds the data structure (the
`.unifideck_manifest.json` file) which is the durable concept;
the scan operation is just one consumer of that format.

Provides two related capabilities:

1. **Per-game manifests** — when Unifideck installs a game, it
   writes a `.unifideck_manifest.json` file into the game's
   install directory. This file is the source of truth for
   re-identifying the game even if the plugin's CacheManager
   is wiped. Low-level helpers: `build_manifest` (pure dict
   construction), `write_manifest` (atomic write to disk),
   `read_manifest` (load and parse).

2. **Discovery scan** — on plugin startup, walk every directory
   from `utils.paths.get_all_game_directories()` looking for
   those manifests. Any game found is registered with the
   SyncService so it appears in the library even after a plugin
   reinstall. High-level orchestrator: `discover_all` (returns
   a structured `DiscoveryResult` dataclass and emits one
   `GAME_INSTALLED` event per discovered game).

The legacy module wrote manifests via raw `open()`/`json.dump`,
discovered with synchronous `os.walk` on the asyncio event loop,
and took an untyped `registry` parameter that was actually the
legacy GameRegistry. The refactor:
- Uses `asyncio.to_thread` for all filesystem operations
- Returns structured `DiscoveryResult` dataclass instead of a
  loose `Dict[str, int]` counter
- Accepts an optional ConfigManager so the manifest filename and
  scan paths are configurable
- Decouples from the registry: the discovery function emits one
  `GAME_INSTALLED` event per discovered game and lets subscribers
  decide what to do (the legacy code mutated the registry
  directly)

Consumers:
- `stores/amazon/amazon_install.py` : calls `write_manifest`
  after each successful install
- `stores/epic/epic_install.py` : same pattern
- `main.py` boot sequence : calls `discover_all` once
  to rebuild the library after a plugin reinstall

Reference: Technical Document v1.0 — Section 3.1.5
(infrastructure services), 5.6 (installation pipeline).
"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ..core.types import Events
from ..event_bus.event_bus import EventBus
from ..utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)
DEFAULT_MANIFEST_FILENAME = ".unifideck_manifest.json"
# ══════════════════════════════════════════════════════════════════
# Result dataclasses
# ══════════════════════════════════════════════════════════════════

@dataclass
class GameManifest:
    """Single per-game manifest written into the install directory.
    Mirrors the legacy JSON shape so existing on-disk manifests
    keep loading after the refactor — only the access pattern
    changes (typed dataclass instead of free dict).
    """

    unifideck_version: str
    store: str
    store_id: str
    title: str
    executable_relative: str
    installed_at: str
    platform: str = "windows"
    def to_dict(self) -> dict[str, Any]:  # noqa: D102 — documentation pending (Sprint D)
        return {
        "unifideck_version": self.unifideck_version,
        "store": self.store,
        "store_id": self.store_id,
        "title": self.title,
        "executable_relative": self.executable_relative,
        "installed_at": self.installed_at,
        "platform": self.platform,
        }
    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameManifest | None:
        """Parse a JSON dict, returning None on missing required keys."""
        try:
            return cls(
            unifideck_version=data["unifideck_version"],
            store=data["store"],
            store_id=data["store_id"],
            title=data.get("title", ""),
            executable_relative=data.get("executable_relative", ""),
            installed_at=data.get("installed_at", ""),
            platform=data.get("platform", "windows"),
            )
        except (KeyError, TypeError):
            return None
@dataclass
class DiscoveryResult:
    """Result of a full startup discovery scan."""

    scanned_directories: int = 0
    manifests_found: int = 0
    games_registered: int = 0
    errors: list[str] = field(default_factory=list)
    def to_dict(self) -> dict[str, Any]:  # noqa: D102 — documentation pending (Sprint D)
        return {
        "scanned_directories": self.scanned_directories,
        "manifests_found": self.manifests_found,
        "games_registered": self.games_registered,
        "errors": list(self.errors),
        }

        # ══════════════════════════════════════════════════════════════════
        # Pure helper
        # ══════════════════════════════════════════════════════════════════
def build_manifest(
 store: str,
 store_id: str,
 title: str,
 executable_relative: str,
 platform: str = "windows",
 unifideck_version: str = "1.0",
) -> GameManifest:
    """Build a GameManifest with the current timestamp.
    Pure function — uses the system clock but does no I/O. Easy
    to unit-test by patching `datetime.now`.
    """
    return GameManifest(
    unifideck_version=unifideck_version,
    store=store,
    store_id=store_id,
    title=title,
    executable_relative=executable_relative,
    installed_at=datetime.now(UTC).isoformat(),
    platform=platform,
    )
def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Legacy alias for backward compatibility. Delegates to `get_cfg`."""
    return get_cfg(config, key, default)


# ══════════════════════════════════════════════════════════════════
# Async I/O
# ══════════════════════════════════════════════════════════════════

async def write_manifest(
    install_dir: str,
    store: str,
    store_id: str,
    title: str,
    executable_relative: str,
    platform: str = "windows",
    config: ConfigManager | None = None,
) -> bool:
    """Write a `.unifideck_manifest.json` into a game's install dir.

    Returns True on success. Failures are logged at error level
    and return False (the install pipeline can decide whether
    that's fatal).
    """
    manifest = build_manifest(
        store, store_id, title, executable_relative, platform,
    )
    filename = get_cfg(
        config, "discovery.manifest_filename",
        DEFAULT_MANIFEST_FILENAME,
    )
    path = Path(install_dir) / filename

    def _write_sync() -> None:
        with path.open("w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)

    try:
        await asyncio.to_thread(_write_sync)
        logger.info(
            "[discovery] wrote manifest %s:%s → %s",
            store, store_id, path,
        )
        return True
    except OSError as e:
        logger.error(
            "[discovery] write_manifest %s:%s failed: %s",
            store, store_id, e,
        )
        return False


async def read_manifest(
    game_dir: str, config: ConfigManager | None = None,
) -> GameManifest | None:
    """Load and parse a manifest from a game directory.

    Returns None if the file doesn't exist or fails to parse.
    """
    filename = get_cfg(
        config, "discovery.manifest_filename",
        DEFAULT_MANIFEST_FILENAME,
    )
    path = Path(game_dir) / filename

    def _read_sync() -> dict[str, Any] | None:
        if not path.is_file():
            return None
        try:
            with path.open(encoding="utf-8") as f:
                return cast("dict[str, Any] | None", json.load(f))
        except (OSError, json.JSONDecodeError) as e:
            logger.debug("[discovery] read %s failed: %s", path, e)
            return None

    raw = await asyncio.to_thread(_read_sync)
    if raw is None:
        return None
    return GameManifest.from_dict(raw)


async def discover_all(
    bus: EventBus | None = None,
    config: ConfigManager | None = None,
) -> DiscoveryResult:
    """Walk all game directories looking for manifests.

    For every manifest found, emit a `GAME_INSTALLED` event so
    subscribed services (CacheManager, ShortcutService) can
    re-register the game without having a circular dependency on
    this module.

    The list of directories to scan comes from
    `utils.paths.get_all_game_directories(config)`.
    """
    from ..utils.paths import get_all_game_directories
    result = DiscoveryResult()
    roots = await asyncio.to_thread(get_all_game_directories, config)
    result.scanned_directories = len(roots)
    logger.info("[discovery] scanning %d roots", len(roots))
    for root in roots:
        try:
            await _scan_one_root(root, bus, result, config)
        except OSError as e:
            result.errors.append(f"{root}: {e}")
    logger.info(
        "[discovery] done — %d manifests, %d games registered, %d errors",
        result.manifests_found, result.games_registered,
        len(result.errors),
    )
    return result


async def _scan_one_root(
    root: str,
    bus: EventBus | None,
    result: DiscoveryResult,
    config: ConfigManager | None,
) -> None:
    """Walk a single root directory two levels deep looking for manifests."""

    def _list(p: str) -> list[str]:
        root_path = Path(p)
        return [
            str(entry)
            for entry in root_path.iterdir()
            if entry.is_dir()
        ]

    try:
        subdirs = await asyncio.to_thread(_list, root)
    except OSError:
        return
    for game_dir in subdirs:
        manifest = await read_manifest(game_dir, config)
        if manifest is None:
            continue
        result.manifests_found += 1
        if bus is not None:
            try:
                await bus.emit(
                    Events.GAME_INSTALLED,
                    store=manifest.store,
                    game_id=manifest.store_id,
                    title=manifest.title,
                    install_path=game_dir,
                    executable=manifest.executable_relative,
                )
                result.games_registered += 1
            except (RuntimeError, asyncio.CancelledError,
                    AttributeError, OSError) as e:
                result.errors.append(
                    f"{manifest.store}:{manifest.store_id}: {e}",
                )


# ── Legacy compatibility aliases ─────────────────────────────────
# The pre-refactor module exposed `discover_installed_games(registry)`
# and `discover_and_log()`. New code uses `discover_all(bus, config)`.
# Provide thin async wrappers so legacy callers stay functional.
async def discover_installed_games(registry=None, bus=None, config=None):
    """Legacy alias for `discover_all` — registry parameter ignored."""
    result = await discover_all(bus=bus, config=config)
    return result.to_dict() if hasattr(result, "to_dict") else result


async def discover_and_log(bus=None, config=None):
    """Legacy alias — same as discover_all + logger.info summary."""
    return await discover_all(bus=bus, config=config)
