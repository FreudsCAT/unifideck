"""core/manifest.py — Unifideck game manifest format.

# OP-04e | core/manifest.py | Depends: OP-09a, OP-33a

Two capabilities:

1. **Per-game manifests** — ``.unifideck_manifest.json`` files
   written into each game's install dir. Source of truth for
   re-identifying a game after a plugin wipe.
2. **Discovery scan** — walk game directories on plugin startup,
   emit ``GAME_INSTALLED`` on every manifest found.
"""
from __future__ import annotations

import dataclasses
import logging
import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

from ..event_bus.event_bus import EventBus
from ..core.types import Events

if TYPE_CHECKING:
    from ..config import ConfigManager

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_FILENAME = ".unifideck_manifest.json"


@dataclass
class GameManifest:
    """Per-game manifest written into the install directory.
    Mirrors the legacy JSON shape so existing on-disk manifests
    keep loading after refactors.
    """

    unifideck_version: str
    store: str
    store_id: str
    title: str
    executable_relative: str
    installed_at: str
    platform: str = "windows"

    def to_dict(self) -> dict[str, Any]:
        """Return the dataclass as a plain JSON-serializable dict."""
        return dataclasses.asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameManifest | None:
        """Parse a JSON dict, return None on missing required keys."""
        required = {"unifideck_version", "store", "store_id", "title",
                     "executable_relative", "installed_at"}
        if not required.issubset(data.keys()):
            return None
        try:
            return cls(
                unifideck_version=str(data["unifideck_version"]),
                store=str(data["store"]),
                store_id=str(data["store_id"]),
                title=str(data["title"]),
                executable_relative=str(data["executable_relative"]),
                installed_at=str(data["installed_at"]),
                platform=str(data.get("platform", "windows")),
            )
        except Exception:
            return None


@dataclass
class DiscoveryResult:
    """Result of a full startup discovery scan."""

    scanned_directories: int = 0
    manifests_found: int = 0
    games_registered: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the dataclass as a JSON-serializable dict."""
        return dataclasses.asdict(self)


def build_manifest(
    store: str,
    store_id: str,
    title: str,
    executable_relative: str,
    platform: str = "windows",
    unifideck_version: str = "1.0",
) -> GameManifest:
    """Build a ``GameManifest`` with the current UTC timestamp (pure).
    Uses the system clock but does no I/O — easy to unit-test by
    patching ``datetime.now``.
    """
    return GameManifest(
        unifideck_version=unifideck_version,
        store=store,
        store_id=store_id,
        title=title,
        executable_relative=executable_relative,
        installed_at=datetime.now(timezone.utc).isoformat(),
        platform=platform,
    )


def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Legacy alias for backward-compat. Delegates to ``get_cfg``."""
    if config is None:
        return default
    try:
        val = config.get(key, default)
        return val if val is not None else default
    except Exception:
        return default


async def write_manifest(
    install_dir: str,
    store: str,
    store_id: str,
    title: str,
    executable_relative: str,
    platform: str = "windows",
    config: ConfigManager | None = None,
) -> bool:
    """Write ``.unifideck_manifest.json`` into the game's install dir.
    Return True on success. Logs + returns False on OSError so
    install pipelines can decide whether that's fatal.
    """
    from ..core.io import async_file_ops as aio

    filename = _cfg(config, "discovery.manifest_filename", DEFAULT_MANIFEST_FILENAME)
    manifest = build_manifest(store, store_id, title, executable_relative, platform)
    path = os.path.join(install_dir, filename)
    ok = await aio.write_json(path, manifest.to_dict())
    if not ok:
        logger.warning("[Manifest] Failed to write manifest to %s", path)
    return ok


async def read_manifest(
    game_dir: str, config: ConfigManager | None = None,
) -> GameManifest | None:
    """Load and parse a manifest from a game directory.
    Return None if the file doesn't exist or fails to parse.
    """
    from ..core.io import async_file_ops as aio

    filename = _cfg(config, "discovery.manifest_filename", DEFAULT_MANIFEST_FILENAME)
    path = os.path.join(game_dir, filename)
    data = await aio.read_json(path)
    if not data:
        return None
    return GameManifest.from_dict(data)


async def discover_all(
    bus: EventBus | None = None,
    config: ConfigManager | None = None,
) -> DiscoveryResult:
    """Walk every directory from ``get_all_game_directories(config)``
    looking for manifests. Emit ``GAME_INSTALLED`` on ``bus`` for each
    one found so subscribers can re-register without a circular dep
    on this module.
    """
    result = DiscoveryResult()

    # Get game directories from config
    roots: list[str] = []
    for store_key in ("epic", "gog", "amazon", "ubisoft"):
        install_root = _cfg(config, f"stores.{store_key}.default_install_root", "")
        if install_root:
            expanded = os.path.expanduser(install_root)
            if os.path.isdir(expanded):
                roots.append(expanded)

    # Also check SD card
    sd_root = _cfg(config, "paths.sd_card_root", "/run/media")
    if os.path.isdir(sd_root):
        try:
            for entry in os.scandir(sd_root):
                if entry.is_dir():
                    games_dir = os.path.join(entry.path, "Games")
                    if os.path.isdir(games_dir):
                        roots.append(games_dir)
        except OSError:
            pass

    for root in roots:
        await _scan_one_root(root, bus, result, config)

    logger.info(
        "[Manifest] Discovery: scanned=%d, found=%d, registered=%d, errors=%d",
        result.scanned_directories, result.manifests_found,
        result.games_registered, len(result.errors),
    )
    return result


async def _scan_one_root(
    root: str,
    bus: EventBus | None,
    result: DiscoveryResult,
    config: ConfigManager | None,
) -> None:
    """Walk a single root directory two levels deep for manifests.
    Mutates ``result`` in place (counters + errors list).
    """
    try:
        entries = os.scandir(root)
    except OSError as e:
        result.errors.append(f"Cannot scan {root}: {e}")
        return

    for entry in entries:
        if not entry.is_dir():
            continue
        result.scanned_directories += 1
        manifest = await read_manifest(entry.path, config)
        if manifest is not None:
            result.manifests_found += 1
            result.games_registered += 1
            if bus is not None:
                await bus.emit(
                    Events.GAME_INSTALLED,
                    store=manifest.store,
                    store_id=manifest.store_id,
                    title=manifest.title,
                    install_path=entry.path,
                )


async def discover_installed_games(registry=None, bus=None, config=None):
    """Legacy alias — ``registry`` arg ignored, delegates to
    ``discover_all`` and returns the dict form of the result.
    """
    result = await discover_all(bus=bus, config=config)
    return result.to_dict()


async def discover_and_log(bus=None, config=None):
    """Legacy alias — identical to ``discover_all`` with logging."""
    return await discover_all(bus=bus, config=config)
