"""Language matching utility — pure function.

OP-51c | py_modules/unifideck/stores/gog/install/languages.py

A single pure function ``smart_match_language(requested, supported)``
that picks the best supported language code for a requested one. The
matcher handles common region variations (``en-US`` vs ``en``, ``pt``
vs ``pt-BR``) and falls back to English if no acceptable match exists.
"""

from __future__ import annotations


def smart_match_language(target: str, choices: list[str]) -> str | None:
    """Smart match language."""
    if not target or not choices:
        return None
    if target in choices:
        return target
    target_base = target.split("-", maxsplit=1)[0].lower()
    for choice in choices:
        choice_base = choice.split("-")[0].lower()
        if target_base == choice_base:
            return choice
    return None
