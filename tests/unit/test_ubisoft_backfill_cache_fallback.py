"""Community-DB-unnamed owned IDs must fall back to Unifideck's own cache.

Regression: an owned Ubisoft install_id the crowd-sourced community
game-ID DB has never catalogued (e.g. an older title superseded by an
HD remaster with a different install_id) was silently dropped from the
library every sync — even when Unifideck had already correctly
identified the exact same game in an earlier session via local-binary
detection, with its name/space_id/executable sitting right there in
``ubisoft_id_map.json``. Real case: "Beyond Good and Evil" (install_id
232), correctly cached with source=local_binary/executable=BGE.exe,
vanished from the synced library because the community DB doesn't know
install_id 232 — only the separate "HD" remaster's id.
"""
from __future__ import annotations

from typing import Any

from unifideck.stores.ubisoft.id_map import UbisoftIdMap
from unifideck.stores.ubisoft.library.fetch import _LibraryFetcher


def _id_map(cache: dict[str, dict[str, Any]]) -> UbisoftIdMap:
    """A real ``UbisoftIdMap`` with its cache pre-seeded, bypassing the
    file-backed constructor (only ``self._cache`` is touched by the code
    under test)."""
    idmap = UbisoftIdMap.__new__(UbisoftIdMap)
    idmap._cache = cache
    return idmap


def _fetcher(id_map: UbisoftIdMap) -> _LibraryFetcher:
    """A ``_LibraryFetcher`` with only ``_id_map`` wired — the sole
    attribute ``_build_backfill_configs`` reads."""
    fetcher = _LibraryFetcher.__new__(_LibraryFetcher)
    fetcher._id_map = id_map
    return fetcher


# ── UbisoftIdMap.find_cached_entry_by_install_id ──────────────────────

def test_find_cached_entry_matches_by_install_id() -> None:
    idmap = _id_map({
        "41b67b23-c7e1-417b-af71-4d24a7a50c45": {
            "install_id": "232",
            "launch_id": "232",
            "name": "Beyond Good and Evil",
            "executable": "BGE.exe",
            "game_identifier": "Beyond Good and Evil",
            "source": "local_binary",
        },
    })

    entry = idmap.find_cached_entry_by_install_id(232)

    assert entry is not None
    assert entry["name"] == "Beyond Good and Evil"
    assert entry["space_id"] == "41b67b23-c7e1-417b-af71-4d24a7a50c45"
    assert entry["executable"] == "BGE.exe"


def test_find_cached_entry_accepts_string_install_id() -> None:
    idmap = _id_map({"space-x": {"install_id": "99", "name": "Some Game"}})

    assert idmap.find_cached_entry_by_install_id("99") is not None
    assert idmap.find_cached_entry_by_install_id(99) is not None


def test_find_cached_entry_returns_none_when_no_match() -> None:
    idmap = _id_map({"space-x": {"install_id": "99", "name": "Some Game"}})

    assert idmap.find_cached_entry_by_install_id(232) is None


def test_find_cached_entry_ignores_entry_without_name() -> None:
    """An entry with no name isn't useful to recover from -- treat it as a miss."""
    idmap = _id_map({"space-x": {"install_id": "232"}})

    assert idmap.find_cached_entry_by_install_id(232) is None


# ── _LibraryFetcher._build_backfill_configs ───────────────────────────

def test_backfill_recovers_name_and_space_id_from_cache_when_db_unnamed() -> None:
    fetcher = _fetcher(_id_map({
        "41b67b23-c7e1-417b-af71-4d24a7a50c45": {
            "install_id": "232",
            "name": "Beyond Good and Evil",
            "executable": "BGE.exe",
            "game_identifier": "Beyond Good and Evil",
            "source": "local_binary",
        },
    }))

    backfilled = fetcher._build_backfill_configs(
        owned_set={232},
        config_by_id={},
        configs=[],
        db_entries=[],  # community DB has nothing for 232
    )

    assert len(backfilled) == 1
    cfg = backfilled[0]
    assert cfg.install_id == 232
    assert cfg.name == "Beyond Good and Evil"
    assert cfg.space_id == "41b67b23-c7e1-417b-af71-4d24a7a50c45"
    assert cfg.executable == "BGE.exe"


def test_backfill_still_drops_id_unresolved_by_db_and_absent_from_cache() -> None:
    """No community-DB name AND no cached identity -- still correctly unlisted."""
    fetcher = _fetcher(_id_map({}))

    backfilled = fetcher._build_backfill_configs(
        owned_set={999},
        config_by_id={},
        configs=[],
        db_entries=[],
    )

    assert backfilled == []


def test_backfill_prefers_community_db_name_over_cache() -> None:
    """When the community DB DOES have a name, use it -- cache is only a fallback."""
    fetcher = _fetcher(_id_map({
        "some-space": {"install_id": "232", "name": "Cached Stale Name"},
    }))

    backfilled = fetcher._build_backfill_configs(
        owned_set={232},
        config_by_id={},
        configs=[],
        db_entries=[("232", "Community DB Name")],
    )

    assert len(backfilled) == 1
    assert backfilled[0].name == "Community DB Name"
    # Not routed through the cache fallback, so no space_id enrichment.
    assert backfilled[0].space_id == ""
