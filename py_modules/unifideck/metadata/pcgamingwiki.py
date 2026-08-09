"""Live PCGamingWiki save-location lookup — the hybrid fallback.

unifiDB ships pre-baked save locations (from the Ludusavi manifest, see
``enrich_save_locations.py``) for the common case. For games unifiDB hasn't
covered yet — new releases, niche titles — this module fetches the same data
LIVE from PCGamingWiki per game and caches it under ``pcgw_saves``.

PCGamingWiki stores save paths only in page wikitext as
``{{Game data/saves|<Platform>|<path>}}`` templates (NOT in any Cargo table),
so we fetch the wikitext and parse it. Crucially the ``<path>`` field contains
nested ``{{p|token}}`` templates whose pipes must NOT split the outer template —
so the parser is a recursive brace-matcher + top-level-pipe splitter, never a
flat regex.

Output matches the shape the unifiDB path produces (``save_locations`` rows with
``path``/``tags``/``stores`` using Ludusavi ``<...>`` tokens), so
``save_location_resolver`` / ``WinePrefixResolver.resolve_ludusavi_path`` handle
both identically.
"""
from __future__ import annotations

import asyncio
import logging
import re
import urllib.parse
from typing import TYPE_CHECKING, Any

from unifideck.utils.config_helpers import get_cfg
from unifideck.utils.title_match import titles_match

if TYPE_CHECKING:
    from unifideck.config import ConfigManager

logger = logging.getLogger(__name__)

PCGW_API_BASE = "https://www.pcgamingwiki.com/w/api.php"

# PCGamingWiki {{p|TOKEN}} -> Ludusavi <token>, so the shared resolver applies.
# mac/linux tokens (osxhome, xdg*, linuxhome) map to None → that path is dropped.
_PCGW_TO_LUDUSAVI = {
    "userprofile": "<home>",
    "appdata": "<winAppData>",
    "localappdata": "<winLocalAppData>",
    "public": "<winPublic>",
    "programdata": "<winProgramData>",
    "allusersprofile": "<winProgramData>",
    "windir": "<winDir>",
    "game": "<base>",
    "uid": "<storeUserId>",
    "steam": "<root>",
}

# {{Game data/saves|<Platform>|...}} label -> store scope for that row.
# Windows is generic (no store). Rows for other stores' clouds get tagged so the
# resolver skips ones that don't apply to the game's store.
_PLATFORM_TO_STORES: dict[str, list[str]] = {
    "windows": [],
    "gog.com": ["gog"],
    "epic games launcher": ["epic"],
    "steam": ["steam"],
    "origin": ["origin"],
    "ubisoft connect": ["uplay"],
    "battle.net": ["blizzard", "battlenet"],
    "uplay": ["uplay"],
}

# Per-(api_base, page) locks so concurrent lookups coalesce into one request.
_page_locks: dict[tuple[str, str], asyncio.Lock] = {}


def _grab_template(text: str, start: int) -> tuple[str, int]:
    """Return (inner_text, end_index) for the ``{{`` template at ``start``."""
    i = start + 2
    depth = 1
    buf: list[str] = []
    while i < len(text) and depth > 0:
        two = text[i:i + 2]
        if two == "{{":
            depth += 1
            buf.append(two)
            i += 2
        elif two == "}}":
            depth -= 1
            if depth == 0:
                i += 2
                break
            buf.append(two)
            i += 2
        else:
            buf.append(text[i])
            i += 1
    return "".join(buf), i


def _split_top_pipes(s: str) -> list[str]:
    """Split on ``|`` that are NOT inside ``{{...}}``."""
    parts: list[str] = []
    depth = 0
    cur: list[str] = []
    i = 0
    while i < len(s):
        two = s[i:i + 2]
        if two == "{{":
            depth += 1
            cur.append(two)
            i += 2
        elif two == "}}":
            depth -= 1
            cur.append(two)
            i += 2
        elif s[i] == "|" and depth == 0:
            parts.append("".join(cur))
            cur = []
            i += 1
        else:
            cur.append(s[i])
            i += 1
    parts.append("".join(cur))
    return parts


def _translate_path(pcgw_path: str) -> str | None:
    """Translate a PCGamingWiki path into a Ludusavi-token path, or None."""
    out: list[str] = []
    i = 0
    text = pcgw_path.strip()
    while i < len(text):
        if text[i:i + 2] == "{{":
            inner, end = _grab_template(text, i)
            # inner like "p|userprofile" or "P|userprofile\\Documents"
            bits = inner.split("|", 1)
            if len(bits) != 2 or bits[0].strip().lower() != "p":
                return None  # unexpected template — bail on this path
            token_full = bits[1].strip()
            main, *sub = re.split(r"[\\/]", token_full)
            mapped = _PCGW_TO_LUDUSAVI.get(main.strip().lower())
            if mapped is None:
                return None  # mac/linux/unknown token → drop this path
            out.append(mapped)
            if sub:
                out.append("/" + "/".join(s for s in sub if s))
            i = end
        else:
            out.append(text[i])
            i += 1
    translated = "".join(out).replace("\\", "/")
    translated = re.sub(r"/{2,}", "/", translated).strip()
    return translated or None


def _rows_from_fields(
    path_fields: list[str], stores: list[str],
) -> list[dict[str, Any]]:
    """Translate every path segment in a row's path fields into rows."""
    out: list[dict[str, Any]] = []
    for raw_field in path_fields:
        for seg in _split_top_pipes(raw_field):
            stripped = seg.strip()
            translated = _translate_path(stripped) if stripped else None
            if translated:
                out.append({"path": translated, "tags": ["save"], "stores": stores})
    return out


def _parse_saves(wikitext: str) -> list[dict[str, Any]]:
    """Extract save_locations rows from ``{{Game data/saves}}`` templates."""
    rows: list[dict[str, Any]] = []
    for m in re.finditer(r"\{\{Game data/saves", wikitext):
        inner, _ = _grab_template(wikitext, m.start())
        inner = inner[len("Game data/saves"):]
        fields = [f.strip() for f in _split_top_pipes(inner) if f.strip()]
        if len(fields) < 2:
            continue
        platform = fields[0].lower()
        if platform not in _PLATFORM_TO_STORES:
            continue  # OS X / Linux / Microsoft Store etc.
        rows.extend(_rows_from_fields(fields[1:], _PLATFORM_TO_STORES[platform]))
    return rows


async def _fetch(url: str, timeout: int) -> Any:  # noqa: ASYNC109  # timeout forwarded to aiohttp ClientTimeout, not an asyncio.timeout context
    import aiohttp
    try:
        connector = aiohttp.TCPConnector(ssl=False)
        async with (
            aiohttp.ClientSession(
                connector=connector,
                headers={"User-Agent": "unifideck/1.0 (cloud-save location lookup)"},
            ) as session,
            session.get(url, timeout=aiohttp.ClientTimeout(total=timeout)) as resp,
        ):
            if resp.status != 200:
                return None
            return await resp.json(content_type=None)
    except Exception as e:
        logger.debug("[pcgw] fetch(%s) failed: %s", url, e)
        return None


async def _resolve_page(
    title: str, steam_appid: int | None, timeout: int,  # noqa: ASYNC109  # timeout forwarded to aiohttp ClientTimeout, not an asyncio.timeout context
) -> str | None:
    """Find the PCGamingWiki page: Steam AppID Cargo join, then title search."""
    if steam_appid:
        q = PCGW_API_BASE + "?" + urllib.parse.urlencode({
            "action": "cargoquery", "format": "json", "limit": "1",
            "tables": "Infobox_game", "fields": "_pageName=Page",
            "where": f'Steam_AppID HOLDS "{steam_appid}"', "maxlag": "5",
        })
        data = await _fetch(q, timeout)
        rows = (data or {}).get("cargoquery") or []
        if rows:
            page: str | None = rows[0].get("title", {}).get("Page")
            return page
    # Title-search fallback, gated by titles_match (no guessing).
    q = PCGW_API_BASE + "?" + urllib.parse.urlencode({
        "action": "query", "format": "json", "list": "search",
        "srsearch": title, "srlimit": "5", "maxlag": "5",
    })
    data = await _fetch(q, timeout)
    hits = (((data or {}).get("query") or {}).get("search")) or []
    for hit in hits:
        hit_page: str | None = hit.get("title")
        if hit_page and titles_match(title, hit_page):
            return hit_page
    return None


async def _fetch_save_locations(
    api_base: str, page: str, timeout: int,  # noqa: ASYNC109  # timeout forwarded to aiohttp ClientTimeout, not an asyncio.timeout context
) -> list[dict[str, Any]]:
    """Fetch + parse a page's ``{{Game data/saves}}`` rows (coalesced)."""
    key = (api_base, page)
    lock = _page_locks.setdefault(key, asyncio.Lock())
    async with lock:
        url = api_base + "?" + urllib.parse.urlencode({
            "action": "parse", "page": page, "prop": "wikitext",
            "format": "json", "formatversion": "2", "redirects": "1", "maxlag": "5",
        })
        data = await _fetch(url, timeout)
        wikitext = (((data or {}).get("parse") or {}).get("wikitext")) or ""
        return _parse_saves(wikitext) if wikitext else []


async def lookup(
    store: str, game_id: str, title: str,
    steam_appid: int | None = None,
    config: ConfigManager | None = None,
) -> dict[str, Any] | None:
    """Live PCGamingWiki save-location lookup. Returns enriched data or None.

    Shape: ``{"save_locations": [{path, tags, stores}], "page": str,
    "source": "pcgamingwiki"}`` — same as the unifiDB-baked records.
    """
    if not get_cfg(config, "metadata.pcgamingwiki.enabled", True):
        return None
    timeout = int(get_cfg(config, "metadata.pcgamingwiki.fetch_timeout_seconds", 15))
    api_base = get_cfg(config, "metadata.pcgamingwiki.api_base", PCGW_API_BASE)

    page = await _resolve_page(title, steam_appid, timeout)
    if not page:
        return None
    save_locations = await _fetch_save_locations(api_base, page, timeout)
    if not save_locations:
        return None
    logger.debug(
        "[pcgw] %s:%s matched page %r (%d rows)",
        store, game_id, page, len(save_locations),
    )
    return {
        "save_locations": save_locations,
        "page": page,
        "source": "pcgamingwiki",
    }
