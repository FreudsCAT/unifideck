"""languages.py — Best-effort language code matcher.

# OP-51c | py_modules/unifideck/stores/gog/install/languages.py | Depends: (none)
"""
from __future__ import annotations


def smart_match_language(target: str, choices: list[str]) -> str | None:
    """Smart match language.

    Tries an exact match first, then a case-insensitive match, then a
    prefix match on the two-letter language code, then falls back to
    ``en-US`` / ``en`` if the target language isn't available.
    """
    if not target or not choices:
        return None
    target_norm = target.strip()
    target_lower = target_norm.lower()
    target_prefix = target_lower.split('-', 1)[0] if '-' in target_lower else target_lower
    for choice in choices:
        if choice == target_norm:
            return choice
    for choice in choices:
        if choice.lower() == target_lower:
            return choice
    for choice in choices:
        choice_prefix = choice.lower().split('-', 1)[0] if '-' in choice.lower() else choice.lower()
        if choice_prefix == target_prefix:
            return choice
    for fallback in ('en-US', 'en'):
        for choice in choices:
            if choice.lower() == fallback.lower():
                return choice
    return None
