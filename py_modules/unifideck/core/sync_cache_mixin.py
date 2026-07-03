"""Library-cache persistence mixin for :class:`SyncService`.

OP-08l-ter | core/sync_cache_mixin.py

Extracted from ``core/sync_service.py`` to keep that file under the
550-LOC volumetry cap. Owns the on-disk ``library_cache.json`` round
trip — loaded once at construction, saved after every finalize and
install-state flip — so a Decky reload restarts with the last synced
library instead of an empty one.

Declares its consumed attributes (``_config``, ``_all_games``,
``_last_sync_time``) as ``TYPE_CHECKING`` annotations only; the host
``SyncService`` provides them at runtime, the same convention the
other sync mixins use.
"""
from __future__ import annotations

import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .types import Game

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)


class _SyncCacheMixin:
    """``library_cache.json`` load/save for :class:`SyncService`."""

    # Provided by the host SyncService at runtime.
    _config: ConfigManager | None
    _all_games: dict[str, list[Game]]
    _last_sync_time: float | None

    def _get_library_cache_path(self) -> Path:
        """Resolve the library_cache.json file path."""
        from unifideck.utils.paths import get_games_map_path
        map_path = get_games_map_path(self._config)
        return Path(map_path).parent / "library_cache.json"

    def _load_library_cache(self) -> None:
        """Load synced libraries from disk cache."""
        try:
            cache_path = self._get_library_cache_path()
            if not cache_path.is_file():
                return

            from unifideck.config.config_persistence import load_json_layer
            data = load_json_layer(cache_path)
            if not data:
                return

            last_sync = data.get("last_sync_time")
            if isinstance(last_sync, (int, float)):
                self._last_sync_time = float(last_sync)

            libraries_data = data.get("libraries", {})
            if not isinstance(libraries_data, dict):
                return

            self._all_games = _deserialize_libraries(libraries_data)
            logger.info(
                "[SyncService] Loaded %d cached games from library_cache.json",
                sum(len(g) for g in self._all_games.values()),
            )
        except Exception as e:
            logger.warning("[SyncService] Failed to load library cache: %s", e)

    def _save_library_cache(self) -> None:
        """Save current unified library state to disk cache."""
        try:
            cache_path = self._get_library_cache_path()
            from dataclasses import asdict

            libraries_data = {}
            for store_name, games in self._all_games.items():
                libraries_data[store_name] = [asdict(g) for g in games]

            payload = {
                "last_sync_time": self._last_sync_time,
                "libraries": libraries_data,
            }

            from unifideck.config.config_persistence import atomic_write_json
            atomic_write_json(cache_path, payload)
            logger.info(
                "[SyncService] Saved library cache (%d games) to "
                "library_cache.json",
                sum(len(g) for g in self._all_games.values()),
            )
        except Exception as e:
            logger.warning("[SyncService] Failed to save library cache: %s", e)


def _deserialize_libraries(
    libraries_data: dict[str, Any],
) -> dict[str, list[Game]]:
    """Rebuild ``{store: [Game]}`` from the cached JSON dicts.

    Unknown keys are dropped so a cache written by a newer build (with
    extra ``Game`` fields) still loads on an older one.
    """
    from dataclasses import fields

    game_fields = {f.name for f in fields(Game)}
    loaded: dict[str, list[Game]] = {}
    for store_name, game_dicts in libraries_data.items():
        if not isinstance(game_dicts, list):
            continue
        games_list = [
            Game(**{k: v for k, v in gd.items() if k in game_fields})
            for gd in game_dicts
            if isinstance(gd, dict)
        ]
        loaded[store_name] = games_list
    return loaded
