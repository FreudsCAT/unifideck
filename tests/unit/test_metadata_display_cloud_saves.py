"""Pre-install cloud-save availability in the App-Details metadata payload.

Feature request (r/Unifideck, 0.7.0 feedback thread): "It would be interesting
to see which games have cloud save even before installing them — if I have the
same game in GOG and Epic but it only has cloud saves on GOG, that is the
version I will choose to play."

The flag comes from the ``cloud`` map the unifiDB save-location pipeline bakes
into the metadata cache, so it needs no install, no wine prefix and no store
CLI call — which is what makes a *pre-install* answer possible at all.

Tri-state matters: ``None`` means "the catalog has no entry for this game" and
must stay distinct from ``False`` ("this store's copy has no cloud saves"), so
the UI can stay silent instead of asserting an absence it cannot back up.
"""
from __future__ import annotations

import types

from unifideck.rpc.mixins._metadata_display import (
    pick_cloud_saves,
    storefront_fields,
)


def test_true_when_store_copy_supports_cloud() -> None:
    enriched = {"cloud": {"gog": True, "epic": False}}
    assert pick_cloud_saves(enriched, "gog") is True


def test_false_when_store_copy_does_not() -> None:
    """The GOG-yes / Epic-no case from the request, read from the Epic side."""
    enriched = {"cloud": {"gog": True, "epic": False}}
    assert pick_cloud_saves(enriched, "epic") is False


def test_none_when_game_absent_from_catalog() -> None:
    assert pick_cloud_saves({}, "gog") is None


def test_none_when_store_absent_from_cloud_map() -> None:
    """Known game, but nothing recorded for THIS storefront."""
    assert pick_cloud_saves({"cloud": {"gog": True}}, "epic") is None


def test_none_when_cloud_field_is_not_a_map() -> None:
    """Defensive: upstream catalog data is not schema-enforced here."""
    assert pick_cloud_saves({"cloud": "yes"}, "gog") is None
    assert pick_cloud_saves({"cloud": None}, "gog") is None


def test_truthy_non_bool_is_normalised_to_bool() -> None:
    # The UI branches on `=== true` / `=== false`, so a stray 1/0 from the
    # catalog must not leak through as a non-boolean.
    assert pick_cloud_saves({"cloud": {"gog": 1}}, "gog") is True
    assert pick_cloud_saves({"cloud": {"gog": 0}}, "gog") is False


def test_storefront_fields_carries_the_flag() -> None:
    """The helper `build_payload` delegates to must include cloud_saves.

    `storefront_fields` exists because adding this as a separate call pushed
    `build_payload` over the fan-out cap; if a refactor drops the key from the
    group, the panel silently loses the cell.
    """
    game = types.SimpleNamespace(store="gog", title="Bastion")
    out = storefront_fields(game, {"cloud": {"gog": True}})
    assert out["cloud_saves"] is True
    assert out["store"] == "gog"
    assert out["title"] == "Bastion"
    assert "store_url" in out
