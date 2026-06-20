"""rpc/mixins/_library_facets.py — assemble per-shortcut library facets.

Reshapes the metadata / compat caches the sync already populates
into one ``FacetRecord`` per Unifideck shortcut. The frontend uses
the result to:

* drive Steam's **native** library Sort menu + Library Filters by
  enriching the live ``AppOverview`` (metacritic, deck category,
  store categories/tags, release date, reviews, date-added), and
* resolve **Great on Deck** by shortcut AppID with zero title
  matching (``protondb_tier`` / ``deck_status``).

Pure cache reads — this module never issues a network fetch. The
two genuinely-new sources (Steam reviews, first-seen timestamp)
are read from their own cache namespaces, which the sync phase
populates; both degrade to ``None`` / ``0`` when cold.

Underscore-prefixed: internal to ``library_facets.LibraryFacetsRPCMixin``.
"""
from __future__ import annotations

from typing import Any

from unifideck.rpc.mixins._metadata_display import (
    appid_candidates,
    deck_compat_enum,
    read_cache_store,
    read_compat_entry,
    read_steam_metadata,
)

# Cache namespaces the sync phase fills with the two facets that are
# NOT already derivable from steam_metadata / compat.
STEAM_REVIEWS_NS = "steam_reviews"  # {str(real_steam_appid): {review_score, review_percentage, total_reviews}}
DATE_ADDED_NS = "shortcut_added"    # {str(shortcut_app_id): int unix seconds}


def _extract_ids(raw: Any) -> list[int]:
    """Pull integer ``id``s from a Steam appdetails ``categories`` /
    ``genres`` list (``[{"id": int, "description": str}, ...]``)."""
    if not isinstance(raw, list):
        return []
    out: list[int] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        cid = item.get("id")
        if isinstance(cid, int):
            out.append(cid)
        elif isinstance(cid, str) and cid.isdigit():
            out.append(int(cid))
    return out


def _metacritic(
    steam_meta: dict[str, Any],
    steam_app_id: int,
    composite_mc: dict[int, int],
) -> int | None:
    """Metacritic critic score.

    Steam's own ``appdetails`` only carries a score for titles Metacritic
    rated *and* Steam surfaces — many games (esp. non-AAA) miss it there.
    The sync's metacritic backfill (``metadata_backfill`` →
    ``metacritic.com``) fills the gap into the composite ``metadata``
    cache, so fall back to that, keyed by the resolved real Steam AppID.
    """
    mc = steam_meta.get("metacritic")
    if isinstance(mc, dict):
        score = mc.get("score")
        if isinstance(score, int):
            return score
    return composite_mc.get(steam_app_id)


def _build_composite_metacritic(cache: Any) -> dict[int, int]:
    """Map real-Steam-AppID → metacritic score from the composite
    ``metadata`` cache (keyed ``store:game_id``), which includes the
    backfilled metacritic.com scores Steam's appdetails lacks. Only
    entries with a positive ``steam_appid`` + score are usable here.
    """
    out: dict[int, int] = {}
    for entry in read_cache_store(cache, "metadata").values():
        if not isinstance(entry, dict):
            continue
        score = entry.get("metacritic_score")
        if not isinstance(score, int) or score <= 0:
            continue
        try:
            sid = int(entry.get("steam_appid") or 0)
        except (TypeError, ValueError):
            continue
        if sid > 0:
            out.setdefault(sid, score)
    return out


def _release_date_str(steam_meta: dict[str, Any]) -> str:
    """Raw Steam release-date string (e.g. ``"12 Sep, 2023"``).

    Returned verbatim; the frontend converts to the unix timestamp
    Steam's overview expects, reusing the same ``new Date(...)`` path
    ``buildOverview`` already used — keeps date parsing in one place.
    """
    rd = steam_meta.get("release_date")
    if isinstance(rd, dict):
        date = rd.get("date")
        if isinstance(date, str):
            return date
    return ""


def _recommendations_total(steam_meta: dict[str, Any]) -> int | None:
    rec = steam_meta.get("recommendations")
    if isinstance(rec, dict):
        total = rec.get("total")
        if isinstance(total, int):
            return total
    return None


def _deck_category(compat: dict[str, Any]) -> int:
    """Numeric Deck-compat enum (0..3) with a ProtonDB-optimism fallback.

    Prefer Valve's Deck-Verified status. When Valve hasn't rated the
    game (``Unknown`` → 0) but ProtonDB reports ``platinum``/``native``,
    treat it as ``Playable`` (2) so the title still surfaces in Steam's
    native "Verified and Playable" filter — matching Unifideck's own
    Great-on-Deck criteria.
    """
    category = deck_compat_enum(compat)
    if category == 0:
        tier = str(compat.get("protondb_tier", "")).lower()
        if tier in ("platinum", "native"):
            return 2
    return category


def _read_int(data: dict[str, Any], shortcut_app_id: int) -> int:
    """Read an int from a per-shortcut namespace, trying both appid forms."""
    for key in appid_candidates(shortcut_app_id):
        raw = data.get(key)
        if raw is None:
            continue
        try:
            return int(raw)
        except (TypeError, ValueError):
            return 0
    return 0


def build_facet_record(
    cache: Any,
    shortcut_app_id: int,
    steam_app_id: int,
    reviews_data: dict[str, Any],
    added_data: dict[str, Any],
    composite_mc: dict[int, int],
) -> dict[str, Any]:
    """Assemble one shortcut's facet record from the warm caches.

    All inputs are plain dicts already read once by the caller so this
    stays O(1) per shortcut (no per-call store lookups).
    """
    steam_meta = read_steam_metadata(cache, steam_app_id)
    compat = read_compat_entry(cache, shortcut_app_id, steam_app_id)

    review = reviews_data.get(str(steam_app_id)) if steam_app_id else None
    review = review if isinstance(review, dict) else {}

    return {
        "steam_app_id": steam_app_id,
        # Sort dimensions
        "metacritic": _metacritic(steam_meta, steam_app_id, composite_mc),
        "release_date": _release_date_str(steam_meta),
        "recommendations_total": _recommendations_total(steam_meta),
        "review_score": review.get("review_score"),
        "review_percentage": review.get("review_percentage"),
        "date_added_unix": _read_int(added_data, shortcut_app_id),
        # Filter dimensions
        "deck_category": _deck_category(compat),
        "store_category": _extract_ids(steam_meta.get("categories")),
        "store_tag": _extract_ids(steam_meta.get("genres")),
        # Great-on-Deck (shortcut-keyed compat — no title matching)
        "protondb_tier": compat.get("protondb_tier"),
        "deck_status": compat.get("deck_status"),
    }


def build_enrichment_map(cache: Any) -> dict[str, dict[str, Any]]:
    """Build ``{shortcut_app_id: FacetRecord}`` for every mapped shortcut.

    Enumerates the ``steam_real_appid`` cache (shortcut → real Steam
    AppID, written during sync) — exactly the set of shortcuts with
    resolvable Steam metadata/compat. Emits **both** the signed and
    unsigned 32-bit string forms of each shortcut AppID so the
    frontend (which gets the unsigned form via ``overview.appid``) and
    the sync layer (which stores signed) can both look up.
    """
    real_appid_data = read_cache_store(cache, "steam_real_appid")
    reviews_data = read_cache_store(cache, STEAM_REVIEWS_NS)
    added_data = read_cache_store(cache, DATE_ADDED_NS)
    composite_mc = _build_composite_metacritic(cache)

    out: dict[str, dict[str, Any]] = {}
    for raw_key, raw_real in real_appid_data.items():
        try:
            shortcut_app_id = int(raw_key)
            steam_app_id = int(raw_real)
        except (TypeError, ValueError):
            continue
        record = build_facet_record(
            cache, shortcut_app_id, steam_app_id, reviews_data, added_data,
            composite_mc,
        )
        for key in appid_candidates(shortcut_app_id):
            out[key] = record
    return out
