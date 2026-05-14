"""launcher.proton.language_setup — Pre-launch UI-language wiring.

Per-store helpers that write the user's chosen UI language into
the right files inside the Proton prefix before the game launches
(GOG ``goggame-*.info``, Amazon Wine registry, Ubisoft UPC config).
"""

from __future__ import annotations

from .amazon import apply_amazon_language
from .gog import apply_gog_language
from .resolver import get_unifideck_language
from .ubisoft import apply_ubisoft_language

__all__ = [
    "apply_amazon_language",
    "apply_gog_language",
    "apply_ubisoft_language",
    "get_unifideck_language",
]
