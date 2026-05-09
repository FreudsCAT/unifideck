from __future__ import annotations
from .resolver import LOCALE_MAP
def smart_match_locale(
    target: str,
) -> tuple[str, str, str, str] | None:
    """Smart match locale."""
    if not target:
        return None
    if target in LOCALE_MAP:
        return LOCALE_MAP[target]
    base = target.split("-", maxsplit=1)[0].lower()
    for code, data in LOCALE_MAP.items():
        if code.split("-")[0].lower() == base:
            return data
    return None
def smart_match_gog_language(
    target: str, available: list[str],
) -> str | None:
    """Smart match GOG language."""
    if not target or not available:
        return None
    if target in available:
        return target
    base = target.split("-", maxsplit=1)[0].lower()
    for lang in available:
        if lang.split("-")[0].lower() == base:
            return lang
    return None