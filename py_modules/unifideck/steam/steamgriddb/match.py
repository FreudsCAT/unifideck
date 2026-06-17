"""Backward-compat shim — title-match primitives moved to a shared util.

The normalisation / edition-stripping / Jaccard-scoring helpers used to
live here (SGDB-internal). They're storefront-agnostic, so metadata and
compat can reuse them; the canonical home is now
:mod:`unifideck.utils.title_match`. This module re-exports them so the
SGDB package's ``from .match import ...`` (and any legacy importer)
keeps working.
"""
from __future__ import annotations

from unifideck.utils.title_match import (
    EDITION_SUFFIXES,
    clean_search_query,
    normalize_for_match,
    score_match,
    strip_edition_suffix,
)

__all__ = [
    "EDITION_SUFFIXES",
    "clean_search_query",
    "normalize_for_match",
    "score_match",
    "strip_edition_suffix",
]
