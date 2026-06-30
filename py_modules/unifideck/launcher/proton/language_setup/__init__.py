"""launcher.proton.language_setup — Pre-launch UI-language wiring.

Per-store helpers that write the user's chosen UI language into the
right files inside the Proton prefix before the game launches (Amazon
Wine registry, Ubisoft UPC config).

GOG is intentionally absent: gogdl already writes the correct
per-language ``goggame-*.info`` at install time (per the ``--lang``
we pass), and the game reads it at runtime — so there is nothing to
do at launch, and rewriting that file only corrupts GOG's own value.
"""

from __future__ import annotations

from .amazon import apply_amazon_language
from .resolver import get_unifideck_language
from .ubisoft import apply_ubisoft_language

__all__ = [
    "apply_amazon_language",
    "apply_ubisoft_language",
    "get_unifideck_language",
]
