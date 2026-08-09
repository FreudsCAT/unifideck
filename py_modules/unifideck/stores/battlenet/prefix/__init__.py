"""Battle.net prefix lifecycle — public surface.

py_modules/unifideck/stores/battlenet/prefix/__init__.py

Three tiers (auth -> template -> per-game clone), because Unifideck never
shares a prefix between games. The template is PRE-WARMED before it is
cloned: a freshly installed client self-updates within minutes and then
demands a restart via a modal nobody can click in Gaming Mode.
"""

from .manager import (
    MARKER_FILENAME,
    WARMED_MARKER,
    BattlenetPrefixManager,
    PrefixStatus,
    inspect_prefix,
)

__all__ = [
    "MARKER_FILENAME",
    "WARMED_MARKER",
    "BattlenetPrefixManager",
    "PrefixStatus",
    "inspect_prefix",
]
