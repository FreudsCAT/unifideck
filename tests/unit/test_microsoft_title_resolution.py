"""Regression: Microsoft display names degraded to titleId slugs.

``store_game_id`` is sometimes lowercase (e.g. ``brrc2bp0g9p0``) but
``displaycatalog`` returns the canonical UPPERCASE ``ProductId``
(``BRRC2BP0G9P0``), which is what ``_parse_displaycatalog`` keys on. The
old case-sensitive lookup missed every lowercase id and fell back to the
ugly xCloud ``titleId`` slug ("HALO5", "GEARSOFWAR4", "DEADBYDEADLIGHT")
— a wrong title that then poisoned metadata, compatibility, and artwork
search. ``_title_for`` now case-folds the lookup.
"""
from __future__ import annotations

import json

from unifideck.stores.microsoft.microsoft_catalog import (
    _parse_displaycatalog,
    _title_for,
)

# displaycatalog always returns the canonical UPPERCASE ProductId.
_RAW = json.dumps({
    "Products": [
        {
            "ProductId": "BRRC2BP0G9P0",
            "LocalizedProperties": [{"ProductTitle": "Halo 5: Guardians"}],
        },
    ],
})


def test_parse_keys_are_uppercased():
    assert _parse_displaycatalog(_RAW) == {"BRRC2BP0G9P0": "Halo 5: Guardians"}


def test_title_for_resolves_lowercase_store_game_id():
    tm = _parse_displaycatalog(_RAW)
    # the bug: lookup by the lowercase store_game_id used to miss → "HALO5"
    assert _title_for(tm, "brrc2bp0g9p0", "HALO5") == "Halo 5: Guardians"


def test_title_for_still_resolves_uppercase():
    tm = _parse_displaycatalog(_RAW)
    assert _title_for(tm, "BRRC2BP0G9P0", "HALO5") == "Halo 5: Guardians"


def test_title_for_falls_back_to_slug_on_genuine_miss():
    tm = _parse_displaycatalog(_RAW)
    assert _title_for(tm, "NOTINCATALOG1", "SLUGNAME") == "SLUGNAME"
