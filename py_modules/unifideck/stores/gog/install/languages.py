"""Language matching utility — pure functions.

OP-51c | py_modules/unifideck/stores/gog/install/languages.py

``smart_match_language(requested, supported)`` picks the best
supported language code for a requested one. gogdl labels its
languages inconsistently across titles (full names, native names,
2-/3-letter codes, GOG quirks like ``esp``/``br``, BCP-47 tags), so
the matcher tries, in order:

  1. exact membership;
  2. 2-letter prefix equality (``en-US`` vs ``en``);
  3. normalized base-language equality (``Spanish`` vs ``es-ES`` vs
     ``esp``), via ``normalize_language``.

Normalization lives in ``unifideck.utils.lang_normalize`` (shared
with the slim launcher process, which can't import this package) and
mirrors the frontend ``src/lib/i18n/gog-language-match.ts``.
"""

from __future__ import annotations

# Re-exported so existing importers (and tests) can keep importing
# ``normalize_language`` from here.
from unifideck.utils.lang_normalize import normalize_language

__all__ = ["normalize_language", "smart_match_language"]


def smart_match_language(target: str, choices: list[str]) -> str | None:
    """Smart match language."""
    if not target or not choices:
        return None
    # 1. Exact membership.
    if target in choices:
        return target
    # 2. 2-letter prefix equality.
    target_base = target.split("-", maxsplit=1)[0].lower()
    for choice in choices:
        choice_base = choice.split("-")[0].lower()
        if target_base == choice_base:
            return choice
    # 3. Normalized base-language equality (handles names / 3-letter
    #    / GOG quirks across differing label formats).
    target_norm = normalize_language(target)
    if target_norm:
        for choice in choices:
            if normalize_language(choice) == target_norm:
                return choice
    return None
