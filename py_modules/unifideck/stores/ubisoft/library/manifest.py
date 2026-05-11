"""manifest.py — Visible-games manifest filter & enrichment.

# OP-57e | py_modules/unifideck/stores/ubisoft/library/manifest.py | Depends: (none)

The "visible manifest" is an optional allow-list living under the
unifideck data dir. When present, only entries from the manifest are
shown — useful for free-to-claim Ubisoft titles that don't appear in
the local UPC ownership cache.
"""
from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ....core.types import Game
from ..config import UbisoftConfig
from ..id_map import UbisoftIdMap

logger = logging.getLogger(__name__)


def _first_non_empty(raw: dict[str, Any], keys: tuple[str, ...]) -> str:
    """First non empty."""
    for key in keys:
        value = raw.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ''


@dataclass
class _VisibleManifestIndex:
    """Visible manifest index."""

    by_norm: dict[str, dict[str, Any]] = field(default_factory=dict)
    by_id: dict[str, dict[str, Any]] = field(default_factory=dict)
    norms: set[str] = field(default_factory=set)
    ids: set[str] = field(default_factory=set)

    def lookup(
        self, game_id: str, norm_title: str,
    ) -> dict[str, Any] | None:
        """Lookup."""
        if game_id and game_id in self.by_id:
            return self.by_id[game_id]
        if norm_title and norm_title in self.by_norm:
            return self.by_norm[norm_title]
        return None

    def matches(self, game_id: str, norm_title: str) -> bool:
        """Matches."""
        if game_id and game_id in self.ids:
            return True
        if norm_title and norm_title in self.norms:
            return True
        return False


class _VisibleManifestProcessor:
    """Visible manifest processor."""

    def __init__(
        self,
        config: UbisoftConfig,
        id_map: UbisoftIdMap,
        load_json_file_safe: Callable[[str], Any | None],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._id_map = id_map
        self._load_json = load_json_file_safe

    def load_manifest(self) -> list[dict[str, Any]]:
        """Load manifest."""
        path = self._config.visible_games_file_expanded
        payload = self._load_json(path)
        if payload is None:
            return []
        raw_games = (
            payload.get('games', []) if isinstance(payload, dict) else payload
        )
        manifest: list[dict[str, Any]] = []
        for raw in raw_games or []:
            normalised = self._normalize_entry(raw)
            if normalised is not None:
                manifest.append(normalised)
        return manifest

    @staticmethod
    def _normalize_entry(raw: dict[str, Any]) -> dict[str, Any] | None:
        """Normalize entry."""
        if not isinstance(raw, dict):
            return None
        title = _first_non_empty(raw, ('title', 'name'))
        if not title:
            return None
        space_id = _first_non_empty(raw, ('space_id', 'spaceId'))
        install_id = _first_non_empty(raw, ('install_id',))
        launch_id = _first_non_empty(raw, ('launch_id',)) or install_id
        ubic = _first_non_empty(
            raw, ('ubisoftconnect_game_id', 'product_id'),
        )
        return {
            'title': title,
            'space_id': space_id,
            'install_id': install_id,
            'launch_id': launch_id,
            'ubisoftconnect_game_id': ubic,
        }

    @staticmethod
    def _game_id_for(entry: dict[str, Any]) -> str:
        """Game ID for."""
        return (
            entry.get('space_id')
            or entry.get('install_id')
            or entry.get('ubisoftconnect_game_id')
            or ''
        )

    def _merge_into_id_map(self, entry: dict[str, Any]) -> bool:
        """Merge into ID map."""
        space_id = entry.get('space_id')
        if not space_id:
            return False
        return self._id_map.merge_entry(
            space_id,
            {
                'install_id': entry.get('install_id') or '',
                'launch_id': entry.get('launch_id') or '',
                'name': entry.get('title') or '',
                'ubisoftconnect_game_id': entry.get('ubisoftconnect_game_id') or '',
            },
        )

    def _build_index(
        self, manifest: list[dict[str, Any]],
    ) -> _VisibleManifestIndex:
        """Build index."""
        index = _VisibleManifestIndex()
        for entry in manifest:
            game_id = self._game_id_for(entry)
            norm_title = self._id_map.normalize_for_matching(
                entry.get('title') or '',
            )
            if game_id:
                index.by_id[game_id] = entry
                index.ids.add(game_id)
            if norm_title:
                index.by_norm[norm_title] = entry
                index.norms.add(norm_title)
        return index

    def apply_filter(
        self,
        games: list[Game],
        installed: dict[str, Any],
        manifest: list[dict[str, Any]] | None,
        source_label: str,
    ) -> list[Game]:
        """Apply filter."""
        if not manifest:
            return games
        self._merge_manifest_into_id_map(manifest)
        index = self._build_index(manifest)
        filtered, seen_ids, seen_norms = self._filter_and_enrich_games(
            games, index, source_label,
        )
        injected = self._inject_unseen_manifest_entries(
            manifest, installed, filtered, seen_ids, seen_norms,
            source_label,
        )
        if injected:
            logger.info(
                '[Ubisoft.manifest] %s injected %d unseen entries',
                source_label, injected,
            )
        return filtered

    def _merge_manifest_into_id_map(
        self, manifest: list[dict[str, Any]],
    ) -> bool:
        """Merge manifest into ID map."""
        changed = False
        for entry in manifest:
            if self._merge_into_id_map(entry):
                changed = True
        return changed

    def _enrich_game_from_entry(
        self, game: Game, entry: dict[str, Any],
    ) -> None:
        """Enrich game from entry."""
        if entry.get('title'):
            game.title = entry['title']
        if entry.get('install_id') and not getattr(game, 'install_id', None):
            game.install_id = entry['install_id']

    def _filter_and_enrich_games(
        self,
        games: list[Game],
        index: _VisibleManifestIndex,
        source_label: str,
    ) -> tuple[list[Game], set[str], set[str]]:
        """Filter and enrich games."""
        out: list[Game] = []
        seen_ids: set[str] = set()
        seen_norms: set[str] = set()
        for game in games:
            game_id = getattr(game, 'game_id', '') or ''
            norm_title = self._id_map.normalize_for_matching(
                getattr(game, 'title', '') or '',
            )
            entry = index.lookup(game_id, norm_title)
            if entry is None:
                continue
            self._enrich_game_from_entry(game, entry)
            out.append(game)
            if game_id:
                seen_ids.add(game_id)
            if norm_title:
                seen_norms.add(norm_title)
        logger.info(
            '[Ubisoft.manifest] %s kept %d / %d',
            source_label, len(out), len(games),
        )
        return out, seen_ids, seen_norms

    def _inject_unseen_manifest_entries(
        self,
        manifest: list[dict[str, Any]],
        installed: dict[str, Any],
        filtered: list[Game],
        seen_ids: set[str],
        seen_norms: set[str],
        source_label: str,
    ) -> int:
        """Inject unseen manifest entries."""
        injected = 0
        for entry in manifest:
            game_id = self._game_id_for(entry)
            norm_title = self._id_map.normalize_for_matching(
                entry.get('title') or '',
            )
            if game_id and game_id in seen_ids:
                continue
            if norm_title and norm_title in seen_norms:
                continue
            game = self._build_synthetic_game(entry, installed)
            if game is None:
                continue
            filtered.append(game)
            injected += 1
            if game_id:
                seen_ids.add(game_id)
            if norm_title:
                seen_norms.add(norm_title)
        return injected

    @staticmethod
    def _build_synthetic_game(
        entry: dict[str, Any], installed: dict[str, Any],
    ) -> Game | None:
        """Build synthetic game."""
        title = entry.get('title') or ''
        space_id = entry.get('space_id') or ''
        install_id = entry.get('install_id') or ''
        if not title:
            return None
        game_id = space_id or install_id
        if not game_id:
            return None
        is_installed = bool(installed.get(game_id))
        return Game(
            store='ubisoft',
            game_id=game_id,
            title=title,
            installed=is_installed,
            install_path=str((installed.get(game_id) or {}).get('install_path', '')),
        )
