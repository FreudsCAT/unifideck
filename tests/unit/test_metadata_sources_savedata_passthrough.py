"""``fetch_unifidb`` must not drop the unifiDB save-location block.

Found while wiring pre-install cloud-save availability: the metadata cache on
a real device held 1208 fresh entries and NOT ONE carried ``cloud`` or
``save_locations``, even though ``unifidb.lookup`` returned both when called
directly. The cause was a five-key projection in ``fetch_unifidb`` that kept
only the display fields, one layer above the cache write.

The consequences were all user-visible, and each matched a separate report:

* ``save_location_resolver`` lost its PRIMARY source (it reads
  ``metadata`` → ``save_locations`` before falling back to ``pcgw_saves``),
  leaving the wine-prefix title guesser to pick a save path — which is how a
  game's save location ends up as the whole install folder;
* ``_cloud_supported`` always returned ``None``, so the cloud-save button
  could never dim for a game that genuinely has no cloud saves;
* the App-Details panel had nothing to show for cloud-save availability.

These tests pin the passthrough so a future tidy-up of the projection can't
silently re-break all three at once.
"""
from __future__ import annotations

import types

import pytest

from unifideck.services import metadata_sources

_FULL_RECORD = {
    "title": "Bastion",
    "description": "An action RPG.",
    "developers": ["Supergiant Games"],
    "publisher": "Supergiant Games",
    "release_date": "2011-07-20",
    "genres": ["Action", "RPG"],
    # The block that was being dropped:
    "save_locations": [{"path": "<winAppData>/Bastion", "store": "gog"}],
    "cloud": {"gog": True, "steam": True},
    "save_source": "PCGamingWiki (CC BY-NC-SA 3.0) via Ludusavi manifest",
    # Present in the catalog but deliberately NOT carried through:
    "platforms": ["Windows"],
    "external_ids": {"steam": "107100"},
}


def _game() -> types.SimpleNamespace:
    return types.SimpleNamespace(
        store="gog", store_game_id="1207666643", title="Bastion",
    )


@pytest.fixture
def _stub_lookup(monkeypatch: pytest.MonkeyPatch):
    """Replace ``unifidb.lookup`` with a canned record."""
    def _install(record):
        async def fake_lookup(_store, _gid, _title, config=None):
            return record
        from unifideck.metadata import unifidb
        monkeypatch.setattr(unifidb, "lookup", fake_lookup)
    return _install


@pytest.mark.asyncio
async def test_save_block_reaches_the_caller(_stub_lookup) -> None:
    _stub_lookup(_FULL_RECORD)
    out = await metadata_sources.fetch_unifidb(_game())

    assert out["cloud"] == {"gog": True, "steam": True}
    assert out["save_locations"] == [{"path": "<winAppData>/Bastion", "store": "gog"}]
    assert "Ludusavi" in out["save_source"]


@pytest.mark.asyncio
async def test_display_fields_still_projected(_stub_lookup) -> None:
    _stub_lookup(_FULL_RECORD)
    out = await metadata_sources.fetch_unifidb(_game())

    assert out["description"] == "An action RPG."
    assert out["developer"] == "Supergiant Games"
    assert out["publisher"] == "Supergiant Games"
    assert out["release_date"] == "2011-07-20"
    assert out["genres"] == ["Action", "RPG"]


@pytest.mark.asyncio
async def test_unconsumed_catalog_fields_stay_out(_stub_lookup) -> None:
    """One cache entry per owned game — don't carry what nothing reads."""
    _stub_lookup(_FULL_RECORD)
    out = await metadata_sources.fetch_unifidb(_game())

    assert "platforms" not in out
    assert "external_ids" not in out


@pytest.mark.asyncio
async def test_absent_save_block_adds_no_empty_keys(_stub_lookup) -> None:
    """Most catalog entries have no save data; don't write null placeholders."""
    _stub_lookup({k: v for k, v in _FULL_RECORD.items()
                  if k not in ("save_locations", "cloud", "save_source")})
    out = await metadata_sources.fetch_unifidb(_game())

    assert "save_locations" not in out
    assert "cloud" not in out
    assert "save_source" not in out


@pytest.mark.asyncio
async def test_no_catalog_match_returns_empty(_stub_lookup) -> None:
    _stub_lookup(None)
    assert await metadata_sources.fetch_unifidb(_game()) == {}


@pytest.mark.asyncio
async def test_lookup_failure_is_swallowed(monkeypatch: pytest.MonkeyPatch) -> None:
    """Enrichment is best-effort — a catalog outage must not fail the sync."""
    async def boom(*_a, **_kw):
        raise RuntimeError("cdn down")
    from unifideck.metadata import unifidb
    monkeypatch.setattr(unifidb, "lookup", boom)

    assert await metadata_sources.fetch_unifidb(_game()) == {}
