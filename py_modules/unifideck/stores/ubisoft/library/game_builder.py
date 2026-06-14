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

from unifideck.core.types import Game

# The Ubisoft Steam dedup filter now lives in ``.steam_filter`` and is
# applied at the Game level in ``fetch.py`` (after build), not here.

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
    ("â\u0080\u0099", "’"),  # noqa: RUF001 — intentional: mapping mojibake → correct glyph
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
# Trailing edition qualifier ("Assassin's Creed Valhalla Gold Edition").
# Stripped so an edition collapses onto its base title in dedup, and so
# the base name can be matched for variant detection.
_EDITION_SUFFIX_PATTERN = re.compile(
    r"\s+(gold|complete|ultimate|deluxe|premium|special|"
    r"collector'?s?|limited|digital|standard)\s*(edition)?$",
    re.IGNORECASE,
)
# Minimum parent length before the substring fallback in
# :meth:`_GameBuilder._parent_matches` is allowed to fire \u2014 short
# prefixes ("the", "tom") match far too eagerly.
_MIN_SUBSTRING_PARENT_LEN = 5


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
        installed: dict[str, Any] | None = None,
    ) -> list[GameConfig]:
        """Cross reference ownership.

        When the ownership binary is present (``owned_set is not None``)
        we trust it. When it's missing — which happens when no account is
        signed in, or before UPC has written the file post-login — we no
        longer return *every* config: the local binaries catalogue lists
        all configurable titles, not the ones the user owns, so that
        path invented phantom "installed" games. The fallback now keeps
        only configs that are actually installed on disk (matching the
        bootstrap-marker scan), which mirrors the ``is_installed`` test
        in :meth:`_build_one_game`.
        """
        if owned_set is not None:
            return [
                config_by_id[oid]
                for oid in owned_set
                if oid in config_by_id and config_by_id[oid].name
            ]
        installed = installed or {}
        result = [
            c for c in configs
            if c.name and _GameBuilder._config_is_installed(c, installed)
        ]
        logger.info(
            "[UbisoftLibrary] no ownership binary — keeping %d installed "
            "config entries (of %d total)",
            len(result), len([c for c in configs if c.name]),
        )
        return result

    @staticmethod
    def _config_is_installed(
        cfg: GameConfig, installed: dict[str, Any],
    ) -> bool:
        """True if ``cfg`` matches an entry in the install scan.

        Same key resolution as :meth:`_build_one_game`: a game is keyed
        by its ``space_id`` when present, otherwise by its install id.
        """
        game_id = cfg.space_id if cfg.space_id else str(cfg.install_id)
        return game_id in installed or cfg.space_id in installed

    def build_games_from_configs(
        self,
        matched_configs: list[GameConfig],
        installed: dict[str, Any],
        *,
        db_names: set[str] | None = None,
        connect_ids: dict[str, str] | None = None,
    ) -> list[Game]:
        """Build games from configs.

        Two passes so DLC/edition variants can be dropped against the
        base titles we actually keep — mirrors staging's
        ``known_base_names`` / ``all_db_names`` dedup. ``db_names`` is
        the normalised community game-ID database name set; it widens
        parent detection for the ``" - "`` separator and degrades to an
        empty set when the database is offline. ``connect_ids`` maps
        ``space_id`` → ``ubisoftConnectGameId`` (from UPC's leveldb
        cache); when present for a game it is recorded in the id_map so
        :meth:`UbisoftIdMap.resolve_launch_id` returns the canonical
        deeplink id.
        """
        db_names = db_names or set()
        connect_ids = connect_ids or {}
        # Pass 1: clean + hard-filter, keeping (cfg, cleaned_title).
        cleaned: list[tuple[GameConfig, str]] = []
        for cfg in sorted(
            matched_configs,
            key=lambda c: (c.name or "").lower(),
        ):
            title = self._clean_launcher_title(cfg.name)
            if self._should_skip_launcher_title(title):
                continue
            cleaned.append((cfg, title))
        # Base titles = edition-stripped, normalised names of every kept
        # entry. Used to recognise an entry as an edition/DLC of a game
        # we already surface.
        base_norms = {
            self._id_map.normalize_for_matching(self._strip_edition(title))
            for _, title in cleaned
        }
        # Pass 2: drop separator-DLC, build the rest (editions collapse
        # onto their base via the strip-edition dedup key in
        # :meth:`_build_one_game`).
        games: list[Game] = []
        seen_norms: set[str] = set()
        id_map_updates: dict[str, dict[str, Any]] = {}
        for cfg, title in cleaned:
            if self._is_dlc_by_separator(title, base_norms, db_names):
                logger.debug(
                    "[UbisoftLibrary] dedup skip (DLC of base): %s",
                    title,
                )
                continue
            game = self._build_one_game(
                cfg,
                title,
                installed,
                seen_norms,
                id_map_updates,
                connect_ids,
            )
            if game is not None:
                games.append(game)
        if id_map_updates:
            self._id_map.update_bulk(id_map_updates)
        return games

    @staticmethod
    def _strip_edition(title: str) -> str:
        """Strip a trailing edition qualifier (``Gold Edition`` …)."""
        match = _EDITION_SUFFIX_PATTERN.search(title)
        return title[: match.start()].strip() if match else title

    def _is_dlc_by_separator(
        self,
        title: str,
        base_norms: set[str],
        db_names: set[str],
    ) -> bool:
        """True if ``title`` is a named DLC/expansion of a base we keep.

        Only the ``" - "`` separator drives parent detection
        (``"Base - Expansion Name"``): the part before the dash must
        match an owned base title or a community-DB title.

        The ``": "`` separator is **deliberately not** used here, unlike
        staging. Staging only ran colon dedup on ownership-binary
        entries that GraphQL had *not* already claimed, so its
        authoritative owned-games list shielded standalone titles. With
        the API gone, every owned game flows through this path — and
        Ubisoft ships a great many *standalone* games as
        ``"Franchise: Subtitle"`` (Rainbow Six: Siege, Ghost Recon:
        Wildlands, Watch Dogs: Legion, Splinter Cell: Blacklist), so
        colon parent-matching would delete real owned games. Genuine
        colon-suffixed DLC ("Game: Season Pass") is already removed by
        the keyword filter in :meth:`_should_skip_launcher_title`.
        """
        if " - " not in title:
            return False
        parent = self._id_map.normalize_for_matching(
            title.split(" - ", 1)[0],
        )
        self_norm = self._id_map.normalize_for_matching(
            self._strip_edition(title),
        )
        return self._parent_matches(
            parent,
            base_norms | db_names,
            base_norms,
            exclude=self_norm,
        )

    @staticmethod
    def _parent_matches(
        parent: str,
        exact_set: set[str],
        substring_set: set[str],
        *,
        exclude: str = "",
    ) -> bool:
        """Exact membership first, then a length-guarded substring match.

        ``exclude`` is the candidate's own normalised title — it is
        skipped so the substring fallback never matches an entry against
        itself (the parent is always a prefix of its own full title).
        """
        if not parent:
            return False
        if parent in exact_set and parent != exclude:
            return True
        if len(parent) > _MIN_SUBSTRING_PARENT_LEN:
            return any(
                (parent in known or known in parent) and known != exclude
                for known in substring_set
            )
        return False

    def _build_one_game(
        self,
        cfg: GameConfig,
        title: str,
        installed: dict[str, Any],
        seen_norms: set[str],
        id_map_updates: dict[str, dict[str, Any]],
        connect_ids: dict[str, str],
    ) -> Game | None:
        """Build one game.

        ``title`` is the already-cleaned launcher title. Dedup keys on
        the *edition-stripped* normalised name so ``"X"`` and
        ``"X Gold Edition"`` collapse (the alphabetically-first, i.e.
        plain, title wins).
        """
        norm_name = self._id_map.normalize_for_matching(
            self._strip_edition(title),
        )
        if norm_name in seen_norms:
            return None
        seen_norms.add(norm_name)
        game_id = cfg.space_id if cfg.space_id else str(cfg.install_id)
        is_installed = game_id in installed or cfg.space_id in installed
        install_meta = installed.get(game_id) or installed.get(cfg.space_id) or {}
        id_map_entry: dict[str, Any] = {
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
        # Prefer the leveldb-sourced connect id (the value
        # ``uplay://launch/{id}/0`` expects) when UPC has cached it.
        connect_id = connect_ids.get(cfg.space_id) if cfg.space_id else None
        if connect_id:
            id_map_entry["ubisoftconnect_game_id"] = connect_id
        id_map_updates[game_id] = id_map_entry
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
