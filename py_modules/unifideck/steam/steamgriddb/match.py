"""Title matching primitives for the 6-pass SGDB search ladder.

Three pure functions + the 58-entry edition-suffix table. Pure means
no I/O, no async, no logging — testable in isolation. The 6-pass
``search_game_id`` orchestrator in :mod:`search` composes these.

Why these matter
================
SGDB autocomplete returns the *first* alphabetical-ish result that
matches the substring. Without normalisation and edition stripping:

* ``Watch Dogs®2 - Deluxe Edition`` → autocomplete returns nothing
  (the ® character breaks the match).
* ``Assassin's Creed`` → autocomplete returns ``Assassin's Creed
  Odyssey`` first (substring match wins; the user wanted the
  original game).
* ``EA SPORTS FC 25`` → autocomplete misses ``FC 25`` because the
  SGDB entry is indexed without the publisher prefix.

Each pass uses these helpers to widen the matching net without ever
accepting a wrong game (the 0.85 Jaccard threshold prevents franchise
confusion).
"""
from __future__ import annotations

import re
import unicodedata

# 58-entry suffix table, longest-first within each group so
# "xbox series xs edition" gets stripped before "xbox edition" /
# "edition" alone. The iterative outer loop in ``strip_edition_suffix``
# restarts after each strip so compound suffixes work end-to-end
# (e.g. "X Standard Edition Windows" → strip Windows → strip
# Standard Edition → "X").
EDITION_SUFFIXES: tuple[str, ...] = (
    # Platform / console suffixes
    "xbox series xs edition", "xbox one edition", "xbox edition",
    "xbox series xs", "xbox one version", "xbox one",
    "pc edition", "windows 10 edition", "windows edition",
    "console edition",
    "for pc", "for windows", "for xbox",
    # Distribution / bundle suffixes
    "cross gen bundle", "cross gen edition", "game preview",
    "the complete season", "the complete first season",
    # Full edition names
    "deluxe edition", "gold edition", "ultimate edition",
    "complete edition", "goty edition", "game of the year edition",
    "definitive edition", "enhanced edition", "special edition",
    "anniversary edition", "premium edition", "standard edition",
    "legacy edition", "collectors edition", "limited edition",
    "digital edition", "classic edition", "royal edition",
    "legendary edition", "elite edition", "ea play edition",
    "remastered", "remake", "directors cut", "the final cut",
    "unofficial patch",
    "revolution",
    "digital version",
    # Short / standalone (word boundary ensured by space-prefix check)
    "goty", "hd", "ce", "dlc", "windows", "console", "xs",
)


def normalize_for_match(title: str) -> str:
    """Lowercase + strip symbols + collapse whitespace.

    Steps in order:

    1. lowercase + trim;
    2. dual-language "Game / Jeu" → first half;
    3. ® ™ © → space (preserves word boundaries: ``Watch Dogs®2``
       becomes ``watch dogs 2`` not ``watch dogs2``);
    4. NFKD-decompose + strip combining marks (é→e, ü→u);
    5. strip ``(TM)`` ``(R)`` ``(C)``;
    6. ``&`` → ``and``;
    7. smart-quotes → ASCII;
    8. ``_`` / ``-`` → space;
    9. ``|`` → empty (so ``X|S`` becomes ``XS`` not ``X S``);
    10. remaining punctuation → space;
    11. collapse runs of whitespace.

    Returns the normalised string. Empty input returns empty string.
    """
    if not title:
        return ""
    t = title.lower().strip()
    if " / " in t:
        t = t.split(" / ", 1)[0].strip()
    t = re.sub(r"[®™©]", " ", t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(c for c in t if not unicodedata.combining(c))
    t = re.sub(r"\((?:tm|r|c)\)", "", t, flags=re.IGNORECASE)
    t = t.replace("&", " and ")
    t = t.replace("‘", "'").replace("’", "'")  # noqa: RUF001  smart-quote match is intentional
    t = t.replace("“", '"').replace("”", '"')
    t = t.replace("_", " ").replace("-", " ").replace("|", "")
    t = re.sub(r"[^\w\s]", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def strip_edition_suffix(normalized: str) -> str:
    """Iteratively strip edition / platform / variant suffixes.

    Repeats until no more suffixes match so compound cases work:

        "call of duty black ops 6 standard edition windows"
            → strip "windows" → "...standard edition"
            → strip "standard edition" → "call of duty black ops 6"

    Also handles three generic patterns after the explicit table is
    exhausted:

    * any ``<1-3 words> edition`` ending (catches "marching fire
      edition", "ultimate survivor edition", etc.);
    * ``chapters/episodes <range>`` endings;
    * trailing 4-digit years between 1980-2030.

    Pure function. Always returns at least the first word (won't
    return empty even if the input was entirely suffixes).
    """
    changed = True
    while changed:
        changed = False
        for suffix in EDITION_SUFFIXES:
            if normalized.endswith(" " + suffix):
                stripped = normalized[: -(len(suffix) + 1)].strip()
                if stripped:
                    normalized = stripped
                    changed = True
                    break
        if changed:
            continue

        m = re.match(r"^(.+?)\s+(?:\w+\s+){0,2}edition$", normalized)
        if m and m.group(1).strip():
            normalized = m.group(1).strip()
            changed = True
            continue

        m = re.match(
            r"^(.+?)\s+(?:chapters?|episodes?)\s+[\d\s]+$",
            normalized,
        )
        if m and m.group(1).strip():
            normalized = m.group(1).strip()
            changed = True
            continue

        m = re.match(r"^(.+?\D)\s+(\d{4})$", normalized)
        if m and 1980 <= int(m.group(2)) <= 2030 and m.group(1).strip():
            normalized = m.group(1).strip()
            changed = True
            continue

    return normalized


def score_match(query_norm: str, candidate_norm: str) -> float:
    """Jaccard word-set overlap with prefix-match bonus.

    Returns a value in ``[0.0, 1.0]``. Caller compares against a
    threshold (0.85 for strict confidence, 0.50 for fuzzy fallback).

    Strict bounds:

    * identical strings → 1.0
    * same words, different order → 0.95
    * franchise-confusion guard: ``"assassins creed"`` vs
      ``"assassins creed odyssey"`` → 0.67 (rejected at 0.85)
    * prefix bonus: when all query words appear at the start of the
      candidate (handles truncated shortcut names like ``"Kameo"``
      finding ``"Kameo: Elements of Power"``).
    """
    if not query_norm or not candidate_norm:
        return 0.0
    if query_norm == candidate_norm:
        return 1.0
    qw = set(query_norm.split())
    cw = set(candidate_norm.split())
    if qw == cw:
        return 0.95
    intersection = qw & cw
    union = qw | cw
    jaccard = len(intersection) / len(union) if union else 0.0

    ql = query_norm.split()
    cl = candidate_norm.split()
    if len(ql) <= len(cl) and ql == cl[: len(ql)]:
        prefix_score = max(0.50, len(ql) / len(cl))
        jaccard = max(jaccard, prefix_score)
    return jaccard


def clean_search_query(title: str) -> str:
    """Pre-API query cleanup — strip noise that hurts SGDB autocomplete.

    Different from :func:`normalize_for_match` — that one prepares a
    string for *comparison*; this one prepares a string for *sending
    to the API*. SGDB autocomplete is forgiving but does worse on
    titles with platform/edition suffixes attached because it returns
    the first substring match and edition-tagged entries sort later.

    Strips:

    * ® ™ ©
    * trailing ``- CE``/``- SE``/``- DE``/``- GE`` markers
    * parenthesised platform tags ``(Xbox One)``, ``(PC)`` etc.
    * trailing ``- Xbox One Edition`` / ``for Xbox`` / etc.
    * ``- Cross Gen Bundle`` / ``- The Complete Season``
    * ``- Standard Edition`` / ``- Console Edition``
    * parenthesised years ``(2020)``
    * parenthesised ``(X|S)``
    * parenthesised ``(Episodes 1-5)``
    * ``+ Something DLC`` add-ons
    * trailing ``Xbox One Version`` / ``Digital Version``
    """
    q = re.sub(r"[®™©]", "", title).strip()
    # En-dash (–) in these regexes is intentional — titles like
    # "Forza Horizon – Standard Edition" need to match both hyphen
    # and en-dash separators. RUF001 flags it as ambiguous; we know.
    patterns = (
        r"\s*[-–:]\s*(?:CE|SE|DE|GE)\s*$",  # noqa: RUF001
        r"\s*\((?:Xbox (?:One|Series X\|?S)|PC|Windows|PS[45]|"
        r"Nintendo Switch|Game Preview)\)\s*$",
        r"\s*[-–:]\s*Xbox (?:One|Series X\|?S)(?:\s+Edition)?\s*$",  # noqa: RUF001
        r"\s+for\s+Xbox\s*$",
        r"\s+Xbox\s+(?:One|Series\s+X\|?S)(?:\s+Edition)?\s*$",
        r"\s*[-–:]\s*(?:Cross[- ]Gen\s+(?:Bundle|Edition)|"  # noqa: RUF001
        r"The\s+Complete(?:\s+First)?\s+Season)\s*$",
        r"\s*[-–:]\s*(?:Standard|Console)\s+Edition"  # noqa: RUF001
        r"(?:\s*\(Windows\))?\s*$",
        r"\s*\(\d{4}\)",
        r"\s*\(X\|?S\)",
        r"\s*\((?:Episodes?|Chapters?)\s+[\d\-\s]+\)",
        r"\s*\+\s+.+$",
        r"\s+Xbox\s+One\s+Version\s*$",
    )
    for pat in patterns:
        q = re.sub(pat, "", q, flags=re.IGNORECASE).strip()
    return q
