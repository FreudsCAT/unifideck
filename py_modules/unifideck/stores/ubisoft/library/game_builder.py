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
    r"internal|dev/qc|pts|test server|demo|trial|"
    # iArtorias legacy-list noise rows that aren't ownable games.
    r"subscription|company logo|secured)\b",
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
# Trailing edition qualifier ("Assassin's Creed Valhalla Gold Edition",
# "Anno 1602 - History Edition"). Used to (a) recognise an entry as an
# *edition of a base game* — a real game, never DLC — and (b) derive the
# base title + edition tag for identity dedup. The leading ``\s+`` matches
# the space in both " Gold Edition" and " - History Edition" forms.
_EDITION_SUFFIX_PATTERN = re.compile(
    r"\s+(gold|complete|ultimate|deluxe|premium|special|"
    r"collector'?s?|limited|digital|standard|history|definitive|"
    r"remastered?|anniversary|goty|game of the year|enhanced|"
    r"legendary)\s*(edition)?$",
    re.IGNORECASE,
)
# Edition keywords that denote the *base* SKU (no distinct edition), so an
# owned "X Standard Edition" dedups together with plain "X".
_BASE_EDITION_WORDS = frozenset({"standard"})
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
        base_catalog_norms: set[str] | None = None,
    ) -> list[Game]:
        """Two-pass build of deduped ``Game`` records from owned configs.

        ``db_names`` (normalised community game-ID DB names) widens ``" - "``
        parent detection; empty when the DB is offline. ``connect_ids`` maps
        ``space_id`` → ``ubisoftConnectGameId`` (UPC leveldb cache) and is
        recorded in the id_map so :meth:`UbisoftIdMap.resolve_launch_id`
        returns the deeplink id. ``base_catalog_norms`` (authoritative Algolia
        base-game titles) is both a keep-allowlist and the dedup identity
        anchor. See :meth:`_clean_and_filter` (pass 1) and
        :meth:`_group_by_identity` (pass 2 — canonical ``(base_game,
        edition_tag)`` grouping, then one record per group winner).
        """
        db_names = db_names or set()
        connect_ids = connect_ids or {}
        base_catalog_norms = base_catalog_norms or set()
        cleaned = self._clean_and_filter(matched_configs, base_catalog_norms)
        groups, order = self._group_by_identity(
            cleaned, db_names, base_catalog_norms,
        )
        games: list[Game] = []
        id_map_updates: dict[str, dict[str, Any]] = {}
        for key in order:
            cfg, title = self._select_group_winner(groups[key], connect_ids)
            game = self._build_one_game(
                cfg, title, installed, id_map_updates, connect_ids,
            )
            if game is not None:
                games.append(game)
        if id_map_updates:
            self._id_map.update_bulk(id_map_updates)
        games.sort(key=lambda g: g.title.lower())
        return games

    def _clean_and_filter(
        self,
        matched_configs: list[GameConfig],
        base_catalog_norms: set[str],
    ) -> list[tuple[GameConfig, str, bool]]:
        """Pass 1: clean titles + hard-filter, keeping ``(cfg, title,
        is_known)``.

        A catalog-known base game is kept unconditionally — the keyword
        heuristics only police entries the catalog can't vouch for.
        """
        cleaned: list[tuple[GameConfig, str, bool]] = []
        for cfg in matched_configs:
            title = self._clean_launcher_title(cfg.name)
            if not title:
                continue
            if self._is_third_party_steam_copy(cfg):
                logger.debug(
                    "[UbisoftLibrary] skip Steam-linked copy: %s", title,
                )
                continue
            known = self._is_known_base_game(title, base_catalog_norms)
            if not known and self._should_skip_launcher_title(title):
                continue
            cleaned.append((cfg, title, known))
        return cleaned

    def _group_by_identity(
        self,
        cleaned: list[tuple[GameConfig, str, bool]],
        db_names: set[str],
        base_catalog_norms: set[str],
    ) -> tuple[
        dict[tuple[str, str], list[tuple[GameConfig, str]]],
        list[tuple[str, str]],
    ]:
        """Pass 2: drop separator-DLC, then group by canonical identity.

        Returns ``(groups, order)`` — ``groups`` maps each canonical
        ``(base_game, edition_tag)`` key to its member ``(cfg, title)`` pairs,
        and ``order`` preserves first-seen insertion for a stable display.
        """
        # Base titles = edition-stripped, normalised names of every kept
        # entry. Used to recognise an entry as a DLC of a game we surface.
        base_norms = {
            self._id_map.normalize_for_matching(self._strip_edition(title))
            for _, title, _ in cleaned
        }
        groups: dict[tuple[str, str], list[tuple[GameConfig, str]]] = {}
        order: list[tuple[str, str]] = []
        for cfg, title, known in cleaned:
            if not known and self._is_dlc_by_separator(
                title, base_norms, db_names, base_catalog_norms,
            ):
                logger.debug(
                    "[UbisoftLibrary] dedup skip (DLC of base): %s",
                    title,
                )
                continue
            key = self._canonical_key(title, base_catalog_norms)
            if key not in groups:
                groups[key] = []
                order.append(key)
            groups[key].append((cfg, title))
        return groups, order

    def _is_third_party_steam_copy(self, cfg: GameConfig) -> bool:
        """True if ``cfg`` is a Steam/Epic copy that can't launch via uplay.

        UPC marks these in the config's ``third_party_platform`` block
        (e.g. ``name: Steam``). Such entitlements only launch from the
        third-party store, so their ``uplay://`` shortcut is a dead end.
        Only available on config-matched entries (backfilled synth
        configs leave the field empty). Gated by ``filter_steam_linked``
        so the user can opt out, mirroring the post-build Steam filter.
        """
        if not getattr(self._config, "filter_steam_linked", True):
            return False
        platform = (getattr(cfg, "third_party_platform", "") or "").lower()
        return platform.startswith(("steam", "epic"))

    def _is_known_base_game(
        self, title: str, base_catalog_norms: set[str],
    ) -> bool:
        """True if ``title`` (edition-stripped) is a known catalog base game.

        Exact normalised match only — substring would let DLC whose name
        starts with a base title ("X - Some Expansion") masquerade as a
        game. Such entries fall through to the DLC heuristics instead.
        """
        if not base_catalog_norms:
            return False
        norm = self._id_map.normalize_for_matching(self._strip_edition(title))
        return bool(norm) and norm in base_catalog_norms

    def _canonical_key(
        self, title: str, base_catalog_norms: set[str],
    ) -> tuple[str, str]:
        """Canonical identity ``(base_game, edition_tag)`` for dedup."""
        base_norm = self._id_map.normalize_for_matching(
            self._strip_edition(title),
        )
        canonical = self._resolve_canonical_base(base_norm, base_catalog_norms)
        return (canonical, self._edition_tag(title))

    @staticmethod
    def _edition_tag(title: str) -> str:
        """The edition qualifier ("gold", "history", …) or "" for the base.

        ``Standard`` maps to the base tag so "X Standard Edition" dedups
        with plain "X".
        """
        match = _EDITION_SUFFIX_PATTERN.search(title)
        if not match:
            return ""
        word = " ".join(match.group(1).lower().split())
        return "" if word in _BASE_EDITION_WORDS else word

    @staticmethod
    def _resolve_canonical_base(
        base_norm: str, base_catalog_norms: set[str],
    ) -> str:
        """Map an owned base title to its catalog identity when possible.

        Exact match wins. Otherwise bridge a *prepended* publisher/brand
        prefix ("Tom Clancy's The Division 2" → "The Division 2") by
        requiring the catalog title to be a whole-word **suffix** of the
        owned name. Suffix-only is deliberate: it bridges prefixes
        without collapsing sequels — "Assassin's Creed II" must NOT fold
        into "Assassin's Creed" (the extra token is a suffix, not a
        prefix). Falls back to ``base_norm`` when nothing matches.
        """
        if not base_norm or base_norm in base_catalog_norms:
            return base_norm
        best = ""
        for cat in base_catalog_norms:
            if (
                len(cat) > _MIN_SUBSTRING_PARENT_LEN
                and base_norm.endswith(" " + cat)
                and len(cat) > len(best)
            ):
                best = cat
        return best or base_norm

    @staticmethod
    def _select_group_winner(
        members: list[tuple[GameConfig, str]],
        connect_ids: dict[str, str],
    ) -> tuple[GameConfig, str]:
        """Pick the surviving ``(cfg, title)`` for a canonical group.

        The winning ``cfg`` decides ``store_game_id`` (and thus the
        shortcut's stable ``LaunchOptions``); deterministic selection
        kills the cross-sync id flip that stranded orphan duplicate
        shortcuts. Priority: a ``space_id`` with a leveldb connect id
        (best ``uplay://`` launch) > any ``space_id`` > lowest numeric
        ``install_id``. The display title is the shortest member title
        (the plain base form beats wordier variants).
        """
        def rank(item: tuple[GameConfig, str]) -> tuple[int, int, str]:
            cfg, title = item
            space = cfg.space_id or ""
            if space and connect_ids.get(space):
                tier = 0
            elif space:
                tier = 1
            else:
                tier = 2
            return (tier, cfg.install_id or 0, title)

        winner_cfg = min(members, key=rank)[0]
        display_title = min(
            (t for _, t in members), key=lambda t: (len(t), t),
        )
        return winner_cfg, display_title

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
        base_catalog_norms: set[str],
    ) -> bool:
        """True if ``title`` is a named DLC/expansion of a base we keep.

        Two separators drive parent detection, with very different
        safety profiles:

        * ``" - "`` (``"Base - Expansion Name"``) — the part before the
          dash must match an owned base title or a community-DB title.
          This dash form is DLC-specific enough to trust broadly.

        * ``": "`` (``"Base: Subtitle"``) — used **only** under strict
          catalog gating (below). Ubisoft ships a great many *standalone*
          games as ``"Franchise: Subtitle"`` (Rainbow Six: Siege, Ghost
          Recon: Wildlands, Watch Dogs: Legion, Splinter Cell:
          Blacklist), so a bare colon parent-match would delete real
          owned games. The gate exploits the fact that the Algolia base
          catalog is base-games-only: a standalone subtitled game is
          *itself* a catalog entry, whereas a DLC (Trials Fusion: Riders
          of the Rustlands) is not — so we only drop a colon title whose
          full name is absent from the catalog while its base is present.

        An edition variant ("Base - History Edition", "Base - Gold
        Edition") is a real game, not DLC — the separator here joins the
        base to an edition qualifier, not to an add-on name. We bail out
        before any parent check so editions are kept (this is what made
        "Anno 1602 - History Edition" vanish).
        """
        if _EDITION_SUFFIX_PATTERN.search(title):
            return False
        self_norm = self._id_map.normalize_for_matching(
            self._strip_edition(title),
        )
        if " - " in title:
            parent = self._id_map.normalize_for_matching(
                title.split(" - ", 1)[0],
            )
            if self._parent_matches(
                parent,
                base_norms | db_names,
                base_norms,
                exclude=self_norm,
            ):
                return True
        if ": " in title:
            return self._is_colon_dlc(
                title, self_norm, base_norms, base_catalog_norms,
            )
        return False

    def _is_colon_dlc(
        self,
        title: str,
        self_norm: str,
        base_norms: set[str],
        base_catalog_norms: set[str],
    ) -> bool:
        """Catalog-gated ``"Base: Subtitle"`` DLC test.

        Drops the entry only when *all* hold: the pre-colon base is a
        known Algolia base game (``base_catalog_norms``), that base is
        *separately owned* (``base_norms``), and the full title is **not
        itself** a catalog base game. The last clause is the discriminator
        that keeps standalone subtitled games — "Prince of Persia: The
        Sands of Time" and "Watch Dogs: Legion" are catalog entries in
        their own right; "Trials Fusion: Riders of the Rustlands" is not.
        """
        parent = self._id_map.normalize_for_matching(
            title.split(": ", 1)[0],
        )
        return (
            bool(parent)
            and parent != self_norm
            and parent in base_catalog_norms
            and parent in base_norms
            and self_norm not in base_catalog_norms
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
        id_map_updates: dict[str, dict[str, Any]],
        connect_ids: dict[str, str],
    ) -> Game | None:
        """Build one game from its canonical-group winner ``cfg``.

        ``title`` is the already-cleaned display title; dedup across the
        canonical group already happened in
        :meth:`build_games_from_configs`.
        """
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
