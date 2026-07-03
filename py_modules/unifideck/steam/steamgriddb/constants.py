"""Static SGDB tables: API base, kind→filter map, style priority, publisher prefixes.

Single source of truth for the values shared across match/search/assets/
ranking. Kept in a leaf module so callers can import without dragging in
the rest of the package.
"""
from __future__ import annotations

SGDB_API_BASE = "https://www.steamgriddb.com/api/v2"

# Five canonical Steam-grid artwork kinds.
# (Public alias for callers; matches services/artwork/service.py.)
ARTWORK_KINDS: tuple[str, ...] = ("grid", "grid_l", "hero", "logo", "icon")

# SGDB endpoint path per kind. ``grid`` and ``grid_l`` (landscape grid)
# share the ``grids`` endpoint and are differentiated by the
# ``dimensions`` query parameter.
KIND_ENDPOINT: dict[str, str] = {
    "grid": "grids",
    "grid_l": "grids",
    "hero": "heroes",
    "logo": "logos",
    "icon": "icons",
}

# Default (dimensions, styles) per kind. Sent as query parameters on the
# SGDB asset-fetch URL so the returned 50-result page contains only
# assets that actually fit Steam's grid layout. Without these filters
# landscape grids are crowded out by portrait grids (the API's default
# ordering favours the more numerous portrait submissions).
#
# ``None`` means "don't send the filter" — used for icons (small + few
# variants so no point narrowing) and logos (no dimension preference).
KIND_DEFAULTS: dict[str, tuple[str | None, str | None]] = {
    "grid": (
        "600x900",
        "alternate,white_logo,no_logo,blurred,material",
    ),
    "grid_l": (
        "920x430,460x215",
        "alternate,white_logo,no_logo,blurred,material",
    ),
    "hero": (
        "1920x620,3840x1240",
        "alternate,blurred,material",
    ),
    "logo": (None, "official,white,black,custom"),
    "icon": (None, None),
}

# Relaxed-dimension fallback when the strict fetch returns nothing.
# Adds Galaxy 2.0 sizes (660x930, 342x482) for portrait grids; drops
# the dimension filter entirely for heroes (any size beats none).
# Empty string means "no filter" (vs ``None`` which means "use default").
KIND_RELAXED: dict[str, tuple[str | None, str | None]] = {
    "grid": ("600x900,660x930,342x482", None),
    "grid_l": (None, None),
    "hero": (None, None),
    "logo": (None, None),
    "icon": (None, None),
}

# Style preference for asset ranking. Lower number = higher priority.
# ``alternate`` is preferred (game-specific cover art); ``white_logo``
# is last because the white-on-image look conflicts with Steam's UI.
STYLE_PRIORITY: dict[str, int] = {
    "alternate": 0,
    "blurred": 1,
    "material": 1,
    "no_logo": 1,
    "white_logo": 2,
}

# Publisher prefixes stripped during the 6-pass search (Pass 5).
# SGDB often indexes games without publisher branding — e.g. "College
# Football 25" rather than "EA SPORTS College Football 25". Removing
# the prefix on retry recovers those matches.
PUBLISHER_PREFIXES: tuple[str, ...] = (
    "ea sports",
    "tom clancys",
    "sid meiers",
    "disney pixar",
    "dreamworks",
    "microsoft",
)
