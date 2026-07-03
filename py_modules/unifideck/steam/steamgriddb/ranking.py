"""Multi-level asset ranking.

5-level sort key:

1. official / locked images first (``is_locked`` flag from SGDB)
2. style preference (alternate > blurred/material/no_logo > white_logo)
3. highest score / upvotes
4. highest resolution
5. API position (popularity tiebreaker — SGDB returns more popular
   assets first within each category)

Why the order matters
=====================
SGDB's free-tier API v2 zeroes the ``score`` / ``upvotes`` /
``downvotes`` fields, so signal #3 is currently inert. That makes
the API's natural ordering (signal #5) the de-facto popularity tie
breaker. The order is preserved by sorting on ``(rank-key, index)``
so equal-rank items keep their server-side order.
"""
from __future__ import annotations

from .assets import ArtworkAsset
from .constants import STYLE_PRIORITY


def _strip_nsfw_humor(assets: list[ArtworkAsset]) -> list[ArtworkAsset]:
    """Drop NSFW / humour entries; fall back to full list if everything
    was filtered out (defensive — the API query already requested
    ``nsfw=false&humor=false`` so this should be a no-op, but caches
    or future API changes could leak entries through)."""
    clean = [a for a in assets if not a.nsfw and not a.humor]
    return clean if clean else assets


def rank_assets(assets: list[ArtworkAsset]) -> list[ArtworkAsset]:
    """Return assets sorted best-first.

    Sort key (lower = better for each component):

    * ``not is_locked`` (False=0 before True=1)
    * ``STYLE_PRIORITY[style]`` (alternate=0 first)
    * ``-score`` (higher score first)
    * ``-(width*height)`` (bigger first)
    * original index (lower = appeared earlier in API response)
    """
    filtered = _strip_nsfw_humor(assets)
    pairs = sorted(
        enumerate(filtered),
        key=lambda pair: (
            not pair[1].is_locked,
            STYLE_PRIORITY.get(pair[1].style, 1),
            -(pair[1].score or 0),
            -((pair[1].width or 0) * (pair[1].height or 0)),
            pair[0],
        ),
    )
    return [a for _, a in pairs]


def pick_best(
    assets: list[ArtworkAsset], rank: int = 0,
) -> ArtworkAsset | None:
    """Return the n-th-best asset (default 0 = best).

    Returns ``None`` for an empty input. Out-of-range ``rank`` clamps
    to the last available asset so callers requesting "third-best"
    when only two exist still get something back.
    """
    if not assets:
        return None
    ranked = rank_assets(assets)
    if not ranked:
        return None
    idx = min(max(rank, 0), len(ranked) - 1)
    return ranked[idx]
