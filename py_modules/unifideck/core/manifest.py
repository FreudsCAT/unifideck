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

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..event_bus.event_bus import EventBus

if TYPE_CHECKING:
    from ..config import ConfigManager

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
        raise NotImplementedError("OP-04e: dataclasses.asdict(self)")

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> GameManifest | None:
        """Parse a JSON dict, return None on missing required keys."""
        raise NotImplementedError("OP-04e: validate required keys, return cls(**data) or None")


@dataclass
class DiscoveryResult:
    """Result of a full startup discovery scan."""

    scanned_directories: int = 0
    manifests_found: int = 0
    games_registered: int = 0
    errors: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """Return the dataclass as a JSON-serializable dict."""
        raise NotImplementedError("OP-04e: dataclasses.asdict(self)")


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
    raise NotImplementedError("OP-04e: create GameManifest with datetime.utcnow().isoformat()")


def _cfg(config: ConfigManager | None, key: str, default: Any) -> Any:
    """Legacy alias for backward-compat. Delegates to ``get_cfg``."""
    raise NotImplementedError("OP-04e: config.get(key, default) if config else default")


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
    raise NotImplementedError("OP-04e: build_manifest() then async write_json()")


async def read_manifest(
    game_dir: str, config: ConfigManager | None = None,
) -> GameManifest | None:
    """Load and parse a manifest from a game directory.
    Return None if the file doesn't exist or fails to parse.
    """
    raise NotImplementedError("OP-04e: async read_json() then GameManifest.from_dict()")


async def discover_all(
    bus: EventBus | None = None,
    config: ConfigManager | None = None,
) -> DiscoveryResult:
    """Walk every directory from ``get_all_game_directories(config)``
    looking for manifests. Emit ``GAME_INSTALLED`` on ``bus`` for each
    one found so subscribers can re-register without a circular dep
    on this module.
    """
    raise NotImplementedError("OP-04e: scan dirs, emit GAME_INSTALLED per manifest")


async def _scan_one_root(
    root: str,
    bus: EventBus | None,
    result: DiscoveryResult,
    config: ConfigManager | None,
) -> None:
    """Walk a single root directory two levels deep for manifests.
    Mutates ``result`` in place (counters + errors list).
    """
    raise NotImplementedError("OP-04e: os.scandir two levels, read_manifest each subdir")


async def discover_installed_games(registry=None, bus=None, config=None):
    """Legacy alias — ``registry`` arg ignored, delegates to
    ``discover_all`` and returns the dict form of the result.
    """
    raise NotImplementedError("OP-04e: delegate to discover_all(bus, config)")


async def discover_and_log(bus=None, config=None):
    """Legacy alias — identical to ``discover_all`` with logging."""
    raise NotImplementedError("OP-04e: delegate to discover_all(bus, config)")
