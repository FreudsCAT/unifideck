"""UI RPC mixin for Plugin class.

OP-26g | rpc/mixins/ui.py
"""
from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

from unifideck.rpc.errors import RpcError

logger = logging.getLogger(__name__)


def _resolve_user_path(path: str) -> str:
    """Expand ``~`` and resolve symlinks. Blocking — wrap with to_thread.

    Returns the canonical absolute path. Empty/None input
    falls back to ``/`` so the caller always gets a real path
    to test for ``is_dir``.
    """
    return str(Path(path or "/").expanduser().resolve())


def _collect_subdirs(
    resolved: str, show_hidden: bool, sort_by: str,
) -> list[str]:
    """Return the immediate subdirectory names of ``resolved``.

    Pure synchronous I/O helper extracted from
    ``list_directory`` to:

    * keep the async method under the nesting=4 gate (the
      scandir-loop-isdir branch was nesting=5);
    * make the blocking work atomic so a single
      ``asyncio.to_thread`` call wraps all the filesystem
      touches at once, rather than scattering ``to_thread``
      calls over each ``is_dir`` check.

    Skips dotfiles unless ``show_hidden`` is True. Each
    entry's ``is_dir`` is guarded against transient OSError
    (broken symlink, race with concurrent rm) — that entry
    is dropped silently. Caller handles directory-level
    OSError / PermissionError.
    """
    entries: list[str] = []
    with os.scandir(resolved) as it:
        for entry in it:
            if not show_hidden and entry.name.startswith("."):
                continue
            if _is_dir_safe(entry):
                entries.append(entry.name)
    if sort_by == "name":
        entries.sort(key=str.lower)
    return entries


def _is_dir_safe(entry: os.DirEntry[str]) -> bool:
    """Return True iff ``entry`` is a directory; False on any OSError.

    Tiny wrapper that swallows transient errors (broken
    symlink, race with rm) so the caller's loop doesn't
    need its own try/except — which kept the nesting depth
    of ``list_directory`` past the gate.
    """
    try:
        return entry.is_dir(follow_symlinks=False)
    except OSError:
        return False


def _read_cache_store(cache: Any, namespace: str) -> dict[str, Any]:
    """Return the raw ``_data`` dict of a cache namespace, or empty.

    Reads the same ``_stores`` attribute that
    :meth:`StoreRPCMixin.get_steam_metadata_cache` uses, so a
    cache miss here matches the visible behaviour of that
    RPC for the same key.
    """
    stores = getattr(cache, "_stores", None)
    if not isinstance(stores, dict):
        return {}
    store = stores.get(namespace)
    data = getattr(store, "_data", None)
    return data if isinstance(data, dict) else {}


def _appid_candidates(app_id: int) -> list[str]:
    """Return the signed + unsigned 32-bit string forms of an AppID.

    Sync stores ``Game.app_id`` as signed (matches Steam's on-disk
    representation), but Steam's frontend hands plugins the
    unsigned form via ``overview.appid``. Caches keyed off
    ``str(game.app_id)`` are therefore reachable only via the
    signed string. This helper returns both so callers don't
    have to know which side wrote the cache.
    """
    forms: list[str] = [str(app_id)]
    if app_id > 0x7FFFFFFF:
        forms.append(str(app_id - 0x100000000))
    elif app_id < 0:
        forms.append(str(app_id + 0x100000000))
    return forms


def _read_steam_real_appid(cache: Any, shortcut_app_id: int) -> int:
    """Resolve the shortcut → real-Steam-AppID mapping.

    Populated by :meth:`MetadataService.fetch_appdetails_for_game`
    during sync. ``0`` when the shortcut hasn't been mapped (no
    Steam Store match found or sync hasn't run yet). Tries both
    signed and unsigned AppID forms (see :func:`_appid_candidates`).
    """
    data = _read_cache_store(cache, "steam_real_appid")
    for key in _appid_candidates(shortcut_app_id):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    return 0


def _read_steam_metadata(cache: Any, steam_app_id: int) -> dict[str, Any]:
    """Return the cached Steam ``appdetails`` payload, or empty.

    Keyed by the real Steam AppID (not the shortcut). Returns
    empty when ``steam_app_id == 0`` or the cache hasn't been
    populated for this game.
    """
    if not steam_app_id:
        return {}
    data = _read_cache_store(cache, "steam_metadata")
    entry = data.get(str(steam_app_id))
    return entry if isinstance(entry, dict) else {}


def _read_compat_entry(cache: Any, shortcut_app_id: int) -> dict[str, Any]:
    """Return the ``compat`` cache entry for a shortcut, or empty.

    Populated by :class:`CompatLibrary`. The entry shape is
    ``{"protondb_tier": ..., "deck_status": ..., "title": ...}``.
    Tries both signed and unsigned AppID forms.
    """
    data = _read_cache_store(cache, "compat")
    for key in _appid_candidates(shortcut_app_id):
        entry = data.get(key)
        if isinstance(entry, dict):
            return entry
    return {}


def _has_steam_store_page(
    steam_meta: dict[str, Any], steam_app_id: int,
) -> bool:
    """Validate that ``steam_meta`` corresponds to a real Steam page.

    Mirrors staging's three-field guard: ``type`` is ``"game"``
    or ``"application"`` (rules out DLC / demos), the embedded
    ``steam_appid`` agrees with the lookup key, and the entry
    has a ``name``. Prevents spoofed-only entries from showing
    DLC / Community / Points / Discussions / Guides buttons that
    would 404 in the Steam client.
    """
    if not steam_app_id or not steam_meta:
        return False
    meta_type = str(steam_meta.get("type", "")).lower()
    try:
        meta_appid = int(steam_meta.get("steam_appid", 0) or 0)
    except (TypeError, ValueError):
        meta_appid = 0
    meta_name = str(steam_meta.get("name", "")).strip()
    return (
        meta_type in ("game", "application")
        and meta_appid == steam_app_id
        and bool(meta_name)
    )


def _pick_developer(
    steam_meta: dict[str, Any], enriched: dict[str, Any],
) -> str:
    """Prefer Steam Store ``developers`` list; fall back to UnifiDB."""
    devs = steam_meta.get("developers") if isinstance(steam_meta, dict) else None
    if isinstance(devs, list) and devs:
        return ", ".join(str(d) for d in devs if d)
    return str(enriched.get("developer") or "")


def _pick_publisher(
    steam_meta: dict[str, Any], enriched: dict[str, Any],
) -> str:
    """Prefer Steam Store ``publishers`` list; fall back to UnifiDB."""
    pubs = steam_meta.get("publishers") if isinstance(steam_meta, dict) else None
    if isinstance(pubs, list) and pubs:
        return ", ".join(str(p) for p in pubs if p)
    return str(enriched.get("publisher") or "")


def _pick_description(
    steam_meta: dict[str, Any], enriched: dict[str, Any],
) -> str:
    """Pick the best available synopsis text.

    Priority: Steam short_description → Steam detailed_description
    → UnifiDB description → Metacritic summary.
    """
    if isinstance(steam_meta, dict):
        short = steam_meta.get("short_description")
        if isinstance(short, str) and short.strip():
            return short
        detailed = steam_meta.get("detailed_description")
        if isinstance(detailed, str) and detailed.strip():
            return detailed
    desc = enriched.get("description")
    if isinstance(desc, str) and desc.strip():
        return desc
    summary = enriched.get("summary")
    return summary if isinstance(summary, str) else ""


def _pick_release_date(
    steam_meta: dict[str, Any], enriched: dict[str, Any],
) -> str:
    """Pick the best available release-date string.

    Steam Store nests it as ``{"date": "Jan 1, 2020", "coming_soon": ...}``;
    UnifiDB returns it as a flat string.
    """
    if isinstance(steam_meta, dict):
        rd = steam_meta.get("release_date")
        if isinstance(rd, dict):
            date = rd.get("date")
            if isinstance(date, str) and date:
                return date
    fallback = enriched.get("release_date")
    return fallback if isinstance(fallback, str) else ""


def _pick_metacritic(
    steam_meta: dict[str, Any], enriched: dict[str, Any],
) -> int | None:
    """Return the Metacritic critic score, or ``None``.

    Steam Store embeds it under ``metacritic.score``; the
    enrichment merge also carries it under ``metacritic_score``
    (Metacritic source) or ``score`` (UnifiDB sometimes).
    """
    if isinstance(steam_meta, dict):
        mc = steam_meta.get("metacritic")
        if isinstance(mc, dict):
            score = mc.get("score")
            if isinstance(score, int):
                return score
    score = enriched.get("metacritic_score")
    return score if isinstance(score, int) else None


def _pick_genres(
    steam_meta: dict[str, Any], enriched: dict[str, Any],
) -> list[str]:
    """Extract genre labels.

    Steam Store returns ``[{"id": "...", "description": "Action"}]`` —
    flatten to just the descriptions. UnifiDB returns a flat list of
    strings already.
    """
    if isinstance(steam_meta, dict):
        raw = steam_meta.get("genres")
        if isinstance(raw, list):
            labels = [
                str(g.get("description", "")).strip()
                for g in raw
                if isinstance(g, dict) and g.get("description")
            ]
            if labels:
                return labels
    fallback = enriched.get("genres")
    if isinstance(fallback, list):
        return [str(g).strip() for g in fallback if g]
    return []


def _deck_compat_enum(compat_entry: dict[str, Any]) -> int:
    """Map ``deck_status`` string → numeric compatibility enum.

    Matches the legacy ``ESteamDeckCompatibilityCategory`` values
    that drive the panel's badge colour: 3 verified, 2 playable,
    1 unsupported, 0 unknown.
    """
    status = str(compat_entry.get("deck_status", "")).lower()
    return {"verified": 3, "playable": 2, "unsupported": 1}.get(status, 0)


def _store_search_url(store: str, title: str) -> str:
    """Build a fallback store landing URL for non-Steam stores.

    Used by the "Store Page" button when the shortcut has no
    real Steam store presence. The URLs match staging's
    behaviour — search pages because most store ``game_id`` values
    are internal catalog IDs (not URL slugs).
    """
    import urllib.parse
    encoded = urllib.parse.quote(title or "")
    if store == "epic":
        return f"https://store.epicgames.com/en-US/browse?q={encoded}&sortBy=relevancy"
    if store == "gog":
        return f"https://www.gog.com/games?query={encoded}"
    if store == "amazon":
        return "https://gaming.amazon.com/home"
    if store == "ubisoft":
        return f"https://store.ubisoft.com/us/search?q={encoded}"
    if store == "microsoft":
        return "https://www.xbox.com/en-US/games"
    return ""


class UIRPCMixin:
    """CDP injection, game metadata, and language preferences."""

    config: Any
    services: Any
    sync_service: Any  # Required for the metadata.enrich(game) lookup

    async def get_game_metadata(self, store: str, game_id: str) -> Any:
        """Return merged metadata for a game from the sync cache.

        :class:`MetadataService` does not expose ``get(store, id)``
        — its real public method is :meth:`enrich(game)` which
        takes a ``Game`` object. We resolve the game via the sync
        cache then enrich. An earlier version called
        ``metadata.get(...)`` and the RPC always raised
        ``AttributeError``.
        """
        metadata = getattr(self.services, "metadata", None)
        if metadata is None:
            raise RpcError("service_unavailable", service="metadata")
        sync = getattr(self, "sync_service", None)
        if sync is None:
            raise RpcError("service_unavailable", service="sync_service")
        for game in sync.get_all_games():
            # ``game_id`` here is the store-native id (the RPC
            # argument name predates the rename to
            # ``store_game_id`` on the dataclass).
            if game.store == store and game.store_game_id == game_id:
                return await metadata.enrich(game)
        return {}

    cache: Any

    async def get_game_metadata_display(
        self, app_id: int,
    ) -> dict[str, Any] | None:
        """Aggregate every metadata source into the panel's display payload.

        Looks up the shortcut's :class:`Game` record via
        ``sync_service.get_game_info``, enriches it through
        :class:`MetadataService` (Steam Store + UnifiDB +
        Metacritic merge), and overlays the rich cached
        ``steam_metadata`` payload + ``compat`` cache to
        produce a single dict matching the frontend
        ``GameMetadata`` interface.

        Returns ``None`` when the shortcut is unknown (game not
        in the sync cache). All inner cache lookups are
        defensive — a missing source just contributes empty
        defaults, never raises.
        """
        sync = getattr(self, "sync_service", None)
        if sync is None:
            raise RpcError("service_unavailable", service="sync_service")
        info = sync.get_game_info(app_id)
        if not info:
            return None

        from unifideck.core.types import Game
        # ``sync.get_game_info`` returns the asdict of the
        # dataclass — reconstruct an instance for ``enrich``
        # so its cache key matches what the background sync
        # enrichment populated.
        game = Game(
            app_id=info.get("app_id", app_id),
            store=info.get("store", ""),
            store_game_id=info.get("store_game_id", ""),
            title=info.get("title", ""),
            installed=info.get("installed", False),
            install_path=info.get("install_path"),
            exe_path=info.get("exe_path"),
            size_bytes=info.get("size_bytes", 0),
            tags=list(info.get("tags") or []),
            icon_url=info.get("icon_url"),
            hero_url=info.get("hero_url"),
            logo_url=info.get("logo_url"),
            metadata=dict(info.get("metadata") or {}),
        )

        metadata = getattr(self.services, "metadata", None)
        enriched: dict[str, Any] = {}
        if metadata is not None:
            try:
                enriched = await metadata.enrich(game) or {}
            except Exception as exc:
                logger.debug(
                    "[MetadataDisplay] enrich failed for %d: %s", app_id, exc,
                )

        steam_app_id = _read_steam_real_appid(self.cache, app_id)
        steam_meta = _read_steam_metadata(self.cache, steam_app_id)
        compat_entry = _read_compat_entry(self.cache, app_id)

        has_steam_store_page = _has_steam_store_page(steam_meta, steam_app_id)
        developer = _pick_developer(steam_meta, enriched)
        publisher = _pick_publisher(steam_meta, enriched)
        description = _pick_description(steam_meta, enriched)
        release_date = _pick_release_date(steam_meta, enriched)
        metacritic_score = _pick_metacritic(steam_meta, enriched)
        genres = _pick_genres(steam_meta, enriched)
        deck_compat = _deck_compat_enum(compat_entry)
        homepage = steam_meta.get("website") if isinstance(steam_meta, dict) else None

        return {
            "steam_app_id": steam_app_id,
            "has_steam_store_page": has_steam_store_page,
            "store": game.store,
            "store_url": _store_search_url(game.store, game.title),
            "title": game.title,
            "developer": developer,
            "publisher": publisher,
            "release_date": release_date,
            "metacritic": metacritic_score,
            "description": description,
            "deck_compatibility": deck_compat,
            # No test-result API on this branch yet — empty list is
            # honoured by the frontend (modal shows "no results").
            "deck_test_results": [],
            "genres": genres,
            "homepage_url": homepage,
        }

    async def hide_play_section(self, app_id: int) -> Any:
        """Inject CSS hiding a game's Play button in Steam UI.

        Routes through the :class:`SteamCSSInjector` singleton
        (see :mod:`unifideck.cdp.cdp_inject`) — ``self.services.cdp``
        is the low-level ``CDPClient`` and has no
        ``hide_play_section`` method, so the previous version
        raised ``AttributeError`` on every "Hide" button click.
        """
        from unifideck.cdp import get_cdp_client
        injector = await get_cdp_client()
        return await injector.hide_play_section(app_id)

    async def unhide_play_section(self, app_id: int) -> Any:
        """Remove the hide-play-section CSS injection.

        Real method on the injector is :meth:`show_play_section`
        (matching the inject/show pair). Previous ``unhide_*``
        call didn't exist on either CDPClient or SteamCSSInjector.
        """
        from unifideck.cdp import get_cdp_client
        injector = await get_cdp_client()
        return await injector.show_play_section(app_id)

    async def inject_hide_css(self, app_id: int, css: str) -> Any:
        """Inject arbitrary CSS keyed by app_id.

        :meth:`SteamCSSInjector.inject_css` takes
        ``(css, marker)``. An earlier version passed
        ``(app_id, css)`` so the CSS string was discarded and
        ``app_id`` was treated as the CSS source.
        """
        from unifideck.cdp import get_cdp_client
        from unifideck.cdp.cdp_inject import build_marker_id
        injector = await get_cdp_client()
        marker = build_marker_id(f"app_{app_id}")
        return await injector.inject_css(css, marker)

    async def get_language_preference(self) -> Any:
        """Return the current UI locale config value."""
        return {"locale": self.config.get("ui.locale", "en-US")}

    async def set_language_preference(self, locale: str) -> Any:
        """Persist the UI locale via config."""
        self.config.set("ui.locale", locale)
        return {"success": True, "locale": locale}

    async def list_directory(
        self,
        path: str,
        show_hidden: bool = False,
        sort_by: str = "name",
    ) -> Any:
        """Enumerate immediate subdirectories of ``path``.

        Backs the frontend ``StoragePathPicker`` which
        navigates step-by-step (one ``list_directory`` per
        click) so we never have to ship a tree of the whole
        filesystem.

        Filesystem work (path resolution + scandir) is
        offloaded to ``asyncio.to_thread`` via two helpers
        — ``_resolve_user_path`` and ``_collect_subdirs`` —
        so the event loop is never blocked on slow mounts
        (network shares, SD card, etc.).

        Args:
            path: absolute path to enumerate. ``~`` is
                expanded.
            show_hidden: include dotfile entries.
            sort_by: ``"name"`` (only sort supported today).

        Returns:
            ``{success, path, directories: [str]}``. On any
            OS-level error the response is non-success with
            an ``error`` field — callers don't need to
            handle exceptions.
        """
        try:
            resolved = await asyncio.to_thread(_resolve_user_path, path)
            is_dir = await asyncio.to_thread(Path(resolved).is_dir)
            if not is_dir:
                return {
                    "success": False,
                    "error": "not_a_directory",
                    "path": resolved,
                    "directories": [],
                }
            entries = await asyncio.to_thread(
                _collect_subdirs, resolved, show_hidden, sort_by,
            )
            return {
                "success": True,
                "path": resolved,
                "directories": entries,
            }
        except PermissionError as e:
            return {
                "success": False,
                "error": "permission_denied",
                "path": path,
                "directories": [],
                "detail": str(e),
            }
        except OSError as e:
            return {
                "success": False,
                "error": "os_error",
                "path": path,
                "directories": [],
                "detail": str(e),
            }
