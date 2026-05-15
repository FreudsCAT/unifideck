"""
Build display-ready GameRecord entries from owned + installed data.

OP-57d | py_modules/unifideck/stores/ubisoft/library/game_builder.py

``_GameBuilder`` combines:

* the UPC catalog (owned-games + metadata);
* the install registry (installed-state);
* the id_map (UPC ↔ Unifideck IDs);
* the SteamGridDB artwork URLs (if cached);

into a uniform ``GameRecord`` shape consumed by the UI. The builder
applies normalisation rules (lowercase names for sort, strip trademark
glyphs, deduplicate when UPC reports a game under multiple space_ids)
and assigns each record a stable display order.
"""

from __future__ import annotations

import logging
import re
from typing import TYPE_CHECKING, Any
from ....core.types import Game

# NOTE: ``..steam_filter`` (the Ubisoft Steam dedup filter) was
# removed in commits 6c84e7e and 908d350 because it caused issues
# in production. The feature is currently disabled — see
# ``apply_steam_filter`` below — and will be re-introduced in a
# future update with a fixed implementation.

if TYPE_CHECKING:
    from unifideck.stores.ubisoft.config import UbisoftConfig
    from unifideck.stores.ubisoft.id_map import UbisoftIdMap
    from unifideck.stores.ubisoft.parser import GameConfig
logger = logging.getLogger(__name__)
_MOJIBAKE_REPLACEMENTS = (
    # The replacement strings on the right-hand side intentionally
    # contain "ambiguous" Unicode characters (typographic apostrophe
    # U+2019, trade mark U+2122, registered U+00AE) because the
    # whole purpose of this table is to map mojibake byte sequences
    # back to their correct Unicode glyphs. RUF001 has no signal
    # here.
    ("Â®", "®"),
    ("â\u0080¢", "™"),
    ("â\u0084¢", "™"),
    ("â\u0080\u0099", "’"),  # noqa: RUF001  # intentional: mapping mojibake → correct glyph
    ("Â", ""),
)
_SKIP_TITLE_KEYWORDS = re.compile(
    r"\b(test\b|beta|alpha|closed|preorder|pre-order|promotion|"
    r"internal|dev/qc|pts|test server|demo|trial)\b",
    re.IGNORECASE,
)
_SKIP_DLC_KEYWORDS = re.compile(
    r"\b(dlc|season pass|expansion|pack|bonus|soundtrack|"
    r"art ?book|skins?|outfit|costume|weapon|map|mission|"
    r"episode|revolver|kukri|cane-sword|hammer|knife|dagger|"
    r"conspiracy|runaway train|texture|language|starter edition|"
    r"battle pass|car shipment|full stock|full ownership|"
    r"master unlock|paint|perk|club|credit pack|currency pack|"
    r"ownership|ubicollectibles|legion of the dead|"
    r"calling all units)\b",
    re.IGNORECASE,
)
_STORE_MARKER_PATTERN = re.compile(
    r"\[STEAM\]|\[Uplay",
    re.IGNORECASE,
)
_CYRILLIC_PATTERN = re.compile(r"[\u0400-\u04FF]")
_PLACEHOLDER_L_PATTERN = re.compile(r"(l\d+|[A-Z0-9_]+)")
_PLACEHOLDER_LITERALS = frozenset({"a ubisoft game"})


class _GameBuilder:
    """Game builder."""

    def __init__(
        self,
        *,
        config: UbisoftConfig,
        id_map: UbisoftIdMap,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._id_map = id_map

    @staticmethod
    def build_config_lookup(
        configs: list[GameConfig],
    ) -> dict[int, GameConfig]:
        """Build config lookup."""
        config_by_id: dict[int, GameConfig] = {}
        for cfg in configs:
            config_by_id[cfg.install_id] = cfg
            if cfg.launch_id and cfg.launch_id != cfg.install_id:
                config_by_id[cfg.launch_id] = cfg
        return config_by_id

    @staticmethod
    def cross_reference_ownership(
        configs: list[GameConfig],
        config_by_id: dict[int, GameConfig],
        owned_set: set[int] | None,
    ) -> list[GameConfig]:
        """Cross reference ownership."""
        if owned_set is not None:
            return [
                config_by_id[oid]
                for oid in owned_set
                if oid in config_by_id and config_by_id[oid].name
            ]
        result = [c for c in configs if c.name]
        logger.info(
            "[UbisoftLibrary] no ownership binary — using all %d config entries",
            len(result),
        )
        return result

    def apply_steam_filter(
        self,
        configs: list[GameConfig],
    ) -> list[GameConfig]:
        """No-op passthrough — Steam dedup filter is currently disabled.

        The implementation in ``..steam_filter`` was removed in
        commits 6c84e7e and 908d350 because it caused production
        issues. Until it returns, this method preserves the call
        site (``fetch.py``) without altering the config list.
        Any ``filter_steam_linked`` config flag the user has set
        is silently ignored — re-enabling will require restoring
        ``steam_filter.py`` and reverting this method.
        """
        if self._config.filter_steam_linked:
            logger.debug(
                "[UbisoftLibrary] filter_steam_linked=True ignored — "
                "feature disabled pending steam_filter.py restoration",
            )
        return configs

    def build_games_from_configs(
        self,
        matched_configs: list[GameConfig],
        installed: dict[str, Any],
    ) -> list[Game]:
        """Build games from configs."""
        games: list[Game] = []
        seen_norms: set[str] = set()
        id_map_updates: dict[str, dict[str, Any]] = {}
        for cfg in sorted(
            matched_configs,
            key=lambda c: (c.name or "").lower(),
        ):
            game = self._build_one_game(
                cfg,
                installed,
                seen_norms,
                id_map_updates,
            )
            if game is not None:
                games.append(game)
        if id_map_updates:
            self._id_map.update_bulk(id_map_updates)
        return games

    def _build_one_game(
        self,
        cfg: GameConfig,
        installed: dict[str, Any],
        seen_norms: set[str],
        id_map_updates: dict[str, dict[str, Any]],
    ) -> Game | None:
        """Build one game."""
        title = self._clean_launcher_title(cfg.name)
        if self._should_skip_launcher_title(title):
            return None
        norm_name = self._id_map.normalize_for_matching(title)
        if norm_name in seen_norms:
            return None
        seen_norms.add(norm_name)
        game_id = cfg.space_id if cfg.space_id else str(cfg.install_id)
        is_installed = game_id in installed or cfg.space_id in installed
        install_meta = installed.get(game_id) or installed.get(cfg.space_id) or {}
        id_map_updates[game_id] = {
            "install_id": str(cfg.install_id),
            "launch_id": str(cfg.launch_id),
            "name": title,
            "executable": getattr(cfg, "executable", None),
            "game_identifier": getattr(
                cfg,
                "game_identifier",
                None,
            ),
            "source": "local_binary",
        }
        return Game(
            app_id=0,
            store="ubisoft",
            store_game_id=game_id,
            title=title,
            installed=is_installed,
            install_path=install_meta.get("install_path"),
            exe_path=install_meta.get("executable"),
            metadata={"ownership_type": "owned"},
        )

    @staticmethod
    def _clean_launcher_title(title: Any) -> str:
        """Clean launcher title."""
        if not isinstance(title, str):
            return ""
        cleaned = title.strip().strip('"').strip("'")
        for bad, good in _MOJIBAKE_REPLACEMENTS:
            cleaned = cleaned.replace(bad, good)
        return cleaned

    def _is_launcher_placeholder_title(self, title: str) -> bool:
        """Is launcher placeholder title."""
        cleaned = self._clean_launcher_title(title)
        if not cleaned:
            return True
        normalized = self._id_map.normalize_for_matching(
            cleaned,
        )
        if normalized in _PLACEHOLDER_LITERALS:
            return True
        return bool(_PLACEHOLDER_L_PATTERN.fullmatch(cleaned))

    def _should_skip_launcher_title(self, title: str) -> bool:
        """Should skip launcher title."""
        cleaned = self._clean_launcher_title(title)
        if not cleaned or len(cleaned.strip()) <= 2:
            return True
        if self._is_launcher_placeholder_title(cleaned):
            return True
        if _STORE_MARKER_PATTERN.search(cleaned):
            return True
        if _SKIP_TITLE_KEYWORDS.search(cleaned):
            return True
        if _CYRILLIC_PATTERN.search(cleaned):
            return True
        return bool(_SKIP_DLC_KEYWORDS.search(cleaned))
