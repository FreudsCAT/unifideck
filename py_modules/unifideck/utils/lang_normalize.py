"""Language-label normalization — shared, dependency-light.

gogdl (and store metadata) describe languages inconsistently across
titles: full English names ("English"), native names ("Deutsch"),
2-letter ISO 639-1 ("en"), 3-letter ISO 639-2 ("eng"), GOG legacy
quirks ("esp" for Spanish, "br" for Brazilian, "cn" for Chinese),
BCP-47 tags ("en-US"), and composite "Name (code)" forms.

This module lives under ``unifideck.utils`` (imports only stdlib) so
it can be used from BOTH the store backend (``stores.gog``) and the
slim launcher process, which cannot import ``unifideck.stores`` (its
auth → cryptography import chain fails there). Mirrors the frontend
``src/lib/i18n/gog-language-match.ts``.

``normalize_language(raw)`` → ISO 639-1 base code (``"es"``) or None.
It is used ONLY to *match* one label against another across formats
(picker pre-selection, install matching, launch matching). It never
replaces the language code that is actually sent to gogdl or written
into the game's files — those stay verbatim.
"""

from __future__ import annotations

# Each group is (iso_base, *aliases); aliases matched lowercased.
_ALIAS_GROUPS: tuple[tuple[str, ...], ...] = (
    ("en", "eng", "english"),
    ("fr", "fra", "fre", "french", "français", "francais"),
    ("de", "deu", "ger", "german", "deutsch"),
    ("es", "esp", "spa", "spanish", "español", "espanol", "castellano"),
    ("it", "ita", "italian", "italiano"),
    ("pt", "por", "portuguese", "português", "portugues", "brazilian", "br"),
    ("ru", "rus", "russian", "русский"),
    ("pl", "pol", "polish", "polski"),
    ("zh", "zho", "chi", "chinese", "cn", "中文", "简体中文", "繁體中文", "繁体中文"),
    ("ja", "jpn", "jp", "japanese", "日本語"),
    ("ko", "kor", "korean", "한국어"),
    ("nl", "nld", "dut", "dutch", "nederlands"),
    ("tr", "tur", "turkish", "türkçe", "turkce"),
    ("uk", "ukr", "ukrainian", "українська"),
    ("cs", "ces", "cze", "czech", "čeština", "cestina"),
    ("hu", "hun", "hungarian", "magyar"),
    ("sv", "swe", "swedish", "svenska"),
    ("da", "dan", "danish", "dansk"),
    ("fi", "fin", "finnish", "suomi"),
    ("no", "nor", "norwegian", "norsk", "nb", "nob", "bokmål", "bokmal", "nn", "nno", "nynorsk"),
    ("ar", "ara", "arabic", "العربية"),
    ("th", "tha", "thai", "ไทย"),
    ("el", "gre", "ell", "greek", "ελληνικά"),
    ("ro", "ron", "rum", "romanian", "română", "romana"),
    ("bg", "bul", "bulgarian", "български"),
)

_ALIASES: dict[str, str] = {}
for _group in _ALIAS_GROUPS:
    _base = _group[0]
    _ALIASES[_base] = _base
    for _alias in _group[1:]:
        _ALIASES[_alias] = _base


def _lookup_token(token: str) -> str | None:
    """Resolve one cleaned token to an ISO 639-1 base, or None."""
    t = token.strip().replace("_", "-")
    if not t:
        return None
    if t in _ALIASES:
        return _ALIASES[t]
    base = t.split("-", maxsplit=1)[0]
    if base and base in _ALIASES:
        return _ALIASES[base]
    return None


def normalize_language(raw: str) -> str | None:
    """Normalize any language label to an ISO 639-1 base code.

    Unpacks a trailing ``Name (code)`` parenthetical into both
    halves. Returns None for unrecognised labels.
    """
    if not raw:
        return None
    s = raw.strip().lower()
    tokens: list[str] = []
    if "(" in s and s.endswith(")"):
        inner_start = s.index("(")
        outside = s[:inner_start].strip()
        inside = s[inner_start + 1 : -1].strip()
        if outside:
            tokens.append(outside)
        if inside:
            tokens.append(inside)
    tokens.append(s.replace("(", " ").replace(")", " ").strip())
    tokens.append(s)
    for token in tokens:
        hit = _lookup_token(token)
        if hit:
            return hit
    return None
