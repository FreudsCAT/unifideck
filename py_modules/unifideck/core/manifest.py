"""Per-game install manifest — write/read/discover.

OP-08j | py_modules/unifideck/core/manifest.py

When Unifideck installs a game (or detects an existing
install), it drops a small JSON manifest file
(``.unifideck_manifest.json`` by default) inside the game's
directory. The manifest records the store + game_id + title
+ executable path so a later discovery pass can re-attach
the install to Unifideck without re-querying every store.

Three public surfaces:

* ``GameManifest`` dataclass + ``DiscoveryResult`` — typed
  records;
* ``build_manifest`` / ``write_manifest`` / ``read_manifest``
  — single-game CRUD;
* ``discover_all`` (+ thin wrappers ``discover_installed_games``
  and ``discover_and_log``) — walk every configured game
  root, read manifests, emit ``GAME_INSTALLED`` events.

All disk I/O goes through ``asyncio.to_thread`` so reads /
writes don't block the event loop on slow storage.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from unifideck.core.types import Events
from unifideck.event_bus.event_bus import EventBus
from unifideck.utils.config_helpers import get_cfg

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

DEFAULT_MANIFEST_FILENAME = ".unifideck_manifest.json"


@dataclass
class GameManifest:
    """One game's install manifest record.

    Attributes:
        unifideck_version: plugin version that wrote the
            manifest (used for forward-compatibility
            decisions during discovery).
        store: store identifier (``"epic"``, ``"gog"``, …).
        store_id: store-specific game id, used to call back
            into the store.
        title: human-readable title.
        executable_relative: path to the launcher .exe
            relative to the install directory.
        installed_at: ISO-8601 install timestamp.
        platform: ``"windows"`` / ``"linux"`` — drives the
            Proton-vs-native launch path.
    """

    unifideck_version: str
    store: str
    store_id: str
    title: str
    executable_relative: str
    installed_at: str
    platform: str = "windows"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to a JSON-friendly dict.

        Explicit field-by-field copy (rather than
        ``dataclasses.asdict``) because the on-disk format
        is a stable wire contract — we want any new
        dataclass field to consciously decide whether to
        join the manifest.

        Returns:
            Seven-key dict ready for ``json.dump``.
        """
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
        """Build a ``GameManifest`` from a raw dict, or ``None`` on bad data.

        Tolerant by design: three fields
        (``unifideck_version``, ``store``, ``store_id``) are
        mandatory; ``KeyError`` on those returns ``None``.
        The rest fall back to safe defaults if missing.
        ``TypeError`` (non-dict input) also returns ``None``.

        Returns ``None`` rather than raising because
        discovery is best-effort — one malformed manifest
        shouldn't abort the whole scan.

        Args:
            data: parsed JSON dict.

        Returns:
            ``GameManifest`` instance, or ``None`` on bad
            data.
        """
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
    """Aggregate counters returned by ``discover_all``.

    Attributes:
        scanned_directories: total game roots walked.
        manifests_found:     manifests successfully parsed.
        games_registered:    games for which the
            ``GAME_INSTALLED`` event was emitted (less
            than ``manifests_found`` when the bus rejected
            some emissions).
        errors:              free-form error strings;
            populated on per-directory or per-emit
            failures but doesn't abort the scan.
    """

    scanned_directories: int = 0
    manifests_found: int = 0
    games_registered: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Serialise the counters as a JSON-friendly dict.

        ``errors`` is shallow-copied so the caller can
        treat the dict as immutable.

        Returns:
            Four-key dict ready for RPC return.
        """
        return {
            "scanned_directories": self.scanned_directories,
            "manifests_found": self.manifests_found,
            "games_registered": self.games_registered,
            "errors": list(self.errors),
        }


def build_manifest(
    store: str,
    store_id: str,
    title: str,
    executable_relative: str,
    platform: str = "windows",
    unifideck_version: str = "1.0",
) -> GameManifest:
    """Construct a ``GameManifest`` with ``installed_at`` set to now.

    Convenience constructor that fills in the timestamp
    (UTC, ISO-8601) so callers don't have to import
    ``datetime`` themselves.

    Args:
        store: store identifier.
        store_id: store-specific game id.
        title: human-readable title.
        executable_relative: launcher .exe path relative to
            install dir.
        platform: ``"windows"`` or ``"linux"``.
        unifideck_version: plugin version stamping the
            manifest. Default ``"1.0"`` for legacy
            callers.

    Returns:
        Freshly-built ``GameManifest``.
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
    """Thin wrapper around ``get_cfg`` — local convention.

    Re-exports the shared config-reading helper under a
    short name to keep call sites compact. The wrapper
    has no extra logic — it exists purely for cosmetic
    readability inside this module.

    Args:
        config: optional ``ConfigManager``.
        key: dotted config key.
        default: fallback value.

    Returns:
        Config value or ``default``.
    """
    return get_cfg(config, key, default)


async def write_manifest(
    install_dir: str,
    store: str,
    store_id: str,
    title: str,
    executable_relative: str,
    platform: str = "windows",
    config: ConfigManager | None = None,
) -> bool:
    """Build + persist a manifest JSON file inside ``install_dir``.

    Calls ``build_manifest`` then writes the result to
    disk via a ``to_thread`` sync write. The filename is
    config-overridable via
    ``discovery.manifest_filename`` (defaults to
    ``.unifideck_manifest.json``).

    Args:
        install_dir: target directory (manifest written
            inside it).
        store / store_id / title / executable_relative /
            platform: forwarded to ``build_manifest``.
        config: optional ``ConfigManager`` (for filename
            override).

    Returns:
        ``True`` on successful write, ``False`` on
        ``OSError`` (logged at ERROR with the store +
        game_id context).
    """
    manifest = build_manifest(
        store,
        store_id,
        title,
        executable_relative,
        platform,
    )
    filename = get_cfg(
        config,
        "discovery.manifest_filename",
        DEFAULT_MANIFEST_FILENAME,
    )
    path = Path(install_dir) / filename

    def _write_sync() -> None:
        """Open + dump + close. Runs on a thread.

        Closure over ``path`` and ``manifest`` from the
        enclosing ``write_manifest``. Plain text-mode
        open + ``json.dump`` with 2-space indent for
        human-readable manifests (these may be inspected
        by users debugging an install).
        """
        with path.open("w", encoding="utf-8") as f:
            json.dump(manifest.to_dict(), f, indent=2)

    try:
        await asyncio.to_thread(_write_sync)
        logger.info(
            "[discovery] wrote manifest %s:%s → %s",
            store,
            store_id,
            path,
        )
        return True
    except OSError:
        logger.exception("[discovery] write_manifest %s:%s failed", store, store_id)
        return False


async def read_manifest(
    game_dir: str,
    config: ConfigManager | None = None,
) -> GameManifest | None:
    """Read + parse a manifest from ``game_dir``, or ``None`` on any failure.

    Tolerates the three common failure modes:

    * **No manifest file** → ``None``;
    * **OSError on open** → ``None`` (logged at DEBUG —
      expected during discovery when scanning
      arbitrary dirs);
    * **JSONDecodeError** → ``None`` (corrupt manifest);
    * **Missing required fields** → ``None`` (handled
      inside ``GameManifest.from_dict``).

    All disk I/O is offloaded to a thread.

    Args:
        game_dir: directory expected to contain the
            manifest file.
        config: optional ``ConfigManager`` for filename
            override.

    Returns:
        ``GameManifest`` on success, ``None`` otherwise.
    """
    filename = get_cfg(
        config,
        "discovery.manifest_filename",
        DEFAULT_MANIFEST_FILENAME,
    )
    path = Path(game_dir) / filename

    def _read_sync() -> dict[str, Any] | None:
        """Open + parse + close. Returns None on missing file or bad JSON.

        Closure over ``path``. Three-arm error policy
        absorbed inside: missing file → None; JSON /
        OSError → None + DEBUG log. The caller
        (``read_manifest``) then runs the dict through
        ``GameManifest.from_dict`` for the typed
        conversion.
        """
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
    """Walk every configured game root, read manifests, emit GAME_INSTALLED.

    Pipeline:

    1. Resolve game roots via
       ``utils.paths.get_all_game_directories``.
    2. For each root, list immediate subdirs and try to
       read a manifest from each.
    3. For every manifest found, emit
       ``GAME_INSTALLED`` on the bus (if a bus was
       supplied).

    Returns aggregate counters so the caller can log /
    surface scan health.

    OSError on a root walks into ``errors`` but doesn't
    abort other roots — partial-success semantics.

    Args:
        bus: optional event bus to emit on. ``None``
            still scans + counts but skips emissions.
        config: optional ``ConfigManager``.

    Returns:
        ``DiscoveryResult`` with counters.
    """
    from unifideck.utils.paths import get_all_game_directories

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
        result.manifests_found,
        result.games_registered,
        len(result.errors),
    )
    return result


async def _scan_one_root(
    root: str,
    bus: EventBus | None,
    result: DiscoveryResult,
    config: ConfigManager | None,
) -> None:
    """Walk one game root, reading every manifest and emitting events.

    List immediate subdirs of ``root`` (one level deep —
    no recursive walk; the convention is that each game
    has its own top-level dir under the root). For each
    subdir, read its manifest and, if present, emit
    ``GAME_INSTALLED`` with the manifest's data.

    Per-subdir failures (manifest unreadable, bus emit
    failure) are recorded in ``result.errors`` but don't
    abort the scan.

    Args:
        root: one game-root path from
            ``get_all_game_directories``.
        bus: optional event bus.
        result: shared result accumulator (mutated).
        config: optional config manager.
    """

    def _list(p: str) -> list[str]:
        """Return subdirectory paths under ``p`` (one level deep).

        Used inside ``_scan_one_root`` to enumerate the
        per-game directories under one game root. Only
        directories — files at the root level are
        skipped. Returns ``str`` paths (not ``Path``)
        because the caller passes them to
        ``read_manifest`` which takes a string.

        Args:
            p: root directory to enumerate.

        Returns:
            List of subdirectory paths.
        """
        root_path = Path(p)
        return [str(entry) for entry in root_path.iterdir() if entry.is_dir()]

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
            except (RuntimeError, asyncio.CancelledError, AttributeError, OSError) as e:
                result.errors.append(
                    f"{manifest.store}:{manifest.store_id}: {e}",
                )


async def discover_installed_games(
    registry: Any | None = None,
    bus: EventBus | None = None,
    config: ConfigManager | None = None,
) -> dict[str, Any] | DiscoveryResult:
    """Run ``discover_all`` and return a JSON-friendly dict.

    Compatibility wrapper for callers expecting a dict
    return rather than the typed ``DiscoveryResult``.
    Notably, the ``registry`` arg is accepted for
    backward signature compatibility but currently
    unused.

    Args:
        registry: unused (legacy parameter).
        bus: optional event bus.
        config: optional config manager.

    Returns:
        Dict from ``DiscoveryResult.to_dict``, or the raw
        result if it somehow lacks the method.
    """
    result = await discover_all(bus=bus, config=config)
    return result.to_dict() if hasattr(result, "to_dict") else result


async def discover_and_log(
    bus: EventBus | None = None,
    config: ConfigManager | None = None,
) -> DiscoveryResult:
    """Alias for ``discover_all`` — keeps a verb-style name on the API.

    Some legacy call sites use this name; kept exported
    to avoid churn during the migration.

    Args:
        bus: optional event bus.
        config: optional config manager.

    Returns:
        ``DiscoveryResult`` from ``discover_all``.
    """
    return await discover_all(bus=bus, config=config)
