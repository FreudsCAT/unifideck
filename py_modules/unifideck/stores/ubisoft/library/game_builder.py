"""game_builder.py — Convert parsed configurations into ``Game`` objects.

# OP-57d | py_modules/unifideck/stores/ubisoft/library/game_builder.py | Depends: OP-05
"""
from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any

from ....core.types import Game
from ..steam_filter import filter_steam_linked_configs

if TYPE_CHECKING:
    from ..config import UbisoftConfig
    from ..id_map import UbisoftIdMap
    from ..parser import GameConfig

logger = logging.getLogger(__name__)
_MOJIBAKE_REPLACEMENTS = (
    ('Â®', '®'), ('â\x80¢', '™'), ('â¢', '™'), ('â\x80\x99', "'"),
    ('Â', ''),
)
_SKIP_TITLE_KEYWORDS = re.compile(
    r'\b(test\b|beta|alpha|closed|preorder|pre-order|promotion|internal|'
    r'dev|qc|pts|test server|demo|trial)\b',
    re.IGNORECASE,
)
_SKIP_DLC_KEYWORDS = re.compile(
    r'\b(dlc|season pass|expansion|pack|bonus|soundtrack|art ?book|skins?|'
    r'outfit|costume|weapon|map|mission|episode|revolver|kukri|cane-sword|'
    r'hammer|knife|dagger|conspiracy|runaway train|texture|language|'
    r'starter edition|battle pass|car shipment|full stock|full unlock|master '
    r'unlock|paint|perk|club|credit pack|currency pack|ownership|'
    r'ubicollectibles|legion of the dead|calling all units)\b',
    re.IGNORECASE,
)
_STORE_MARKER_PATTERN = re.compile(r'\[STEAM\]|\[Uplay', re.IGNORECASE)
_CYRILLIC_PATTERN = re.compile(r'[Ѐ-ӿ]')
_PLACEHOLDER_L_PATTERN = re.compile(r'(l\d+|[A-Z0-9_]+)')
_PLACEHOLDER_LITERALS = frozenset({'a ubisoft game'})


class _GameBuilder:
    """Game builder."""

    def __init__(
        self, *, config: UbisoftConfig, id_map: UbisoftIdMap,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._id_map = id_map

    @staticmethod
    def build_config_lookup(
        configs: list[GameConfig],
    ) -> dict[int, GameConfig]:
        """Build config lookup."""
        return {cfg.launch_id: cfg for cfg in configs if cfg.launch_id}

    @staticmethod
    def cross_reference_ownership(
        configs: list[GameConfig],
        config_by_id: dict[int, GameConfig],
        owned_set: set[int] | None,
    ) -> list[GameConfig]:
        """Cross reference ownership."""
        if not owned_set:
            return configs
        owned: list[GameConfig] = []
        for launch_id in owned_set:
            cfg = config_by_id.get(launch_id)
            if cfg is not None:
                owned.append(cfg)
        return owned

    def apply_steam_filter(
        self, configs: list[GameConfig],
    ) -> list[GameConfig]:
        """Apply steam filter."""
        if not self._config.filter_steam_linked:
            return configs
        return self._filter_steam_linked_configs(configs)

    def _filter_steam_linked_configs(
        self, configs: list[GameConfig],
    ) -> list[GameConfig]:
        """Filter steam linked configs."""
        return filter_steam_linked_configs(
            configs,
            self._config.steam_library_cross_ref,
            self._id_map,
        )

    def build_games_from_configs(
        self,
        matched_configs: list[GameConfig],
        installed: dict[str, Any],
    ) -> list[Game]:
        """Build games from configs."""
        seen_norms: set[str] = set()
        id_map_updates: dict[str, dict[str, Any]] = {}
        out: list[Game] = []
        for cfg in matched_configs:
            game = self._build_one_game(
                cfg, installed, seen_norms, id_map_updates,
            )
            if game is not None:
                out.append(game)
        if id_map_updates:
            self._id_map.update_bulk(id_map_updates)
        return out

    def _build_one_game(
        self,
        cfg: GameConfig,
        installed: dict[str, Any],
        seen_norms: set[str],
        id_map_updates: dict[str, dict[str, Any]],
    ) -> Game | None:
        """Build one game."""
        title = self._clean_launcher_title(cfg.name)
        if not title or self._should_skip_launcher_title(title):
            return None
        norm = self._id_map.normalize_for_matching(title)
        if not norm or norm in seen_norms:
            return None
        seen_norms.add(norm)
        space_id = cfg.space_id or str(cfg.launch_id)
        installed_info = installed.get(space_id, {})
        is_installed = bool(installed_info)
        if cfg.space_id:
            id_map_updates[cfg.space_id] = {
                'install_id': str(cfg.install_id),
                'launch_id': str(cfg.launch_id),
                'name': title,
            }
        return Game(
            store='ubisoft',
            game_id=space_id,
            title=title,
            installed=is_installed,
            install_path=str(installed_info.get('install_path', '')),
        )

    @staticmethod
    def _clean_launcher_title(title: Any) -> str:
        """Clean launcher title."""
        if not isinstance(title, str):
            return ''
        cleaned = title
        for src, dst in _MOJIBAKE_REPLACEMENTS:
            cleaned = cleaned.replace(src, dst)
        return cleaned.strip()

    def _is_launcher_placeholder_title(self, title: str) -> bool:
        """Is launcher placeholder title."""
        if not title:
            return True
        lower = title.lower().strip()
        if lower in _PLACEHOLDER_LITERALS:
            return True
        if _PLACEHOLDER_L_PATTERN.fullmatch(title):
            return True
        return False

    def _should_skip_launcher_title(self, title: str) -> bool:
        """Should skip launcher title."""
        if self._is_launcher_placeholder_title(title):
            return True
        if _SKIP_TITLE_KEYWORDS.search(title):
            return True
        if _SKIP_DLC_KEYWORDS.search(title):
            return True
        if _STORE_MARKER_PATTERN.search(title):
            return True
        if _CYRILLIC_PATTERN.search(title):
            return True
        return False
