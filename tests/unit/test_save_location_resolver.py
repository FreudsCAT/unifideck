"""Tests for the Ludusavi/PCGamingWiki save-location resolution chain.

Covers:
* ``WinePrefixResolver.resolve_ludusavi_path`` — token → prefix-dir mapping,
  wildcard/storeUserId truncation, install-dir + Linux/unknown handling.
* ``save_location_resolver.resolve_save_dir`` — reading enriched metadata from
  the cache, store-row selection (skip other stores' rows), on-disk preference.
"""
import os

from unifideck.services.cloud_save import save_location_resolver as slr
from unifideck.services.cloud_save.path_resolver import WinePrefixResolver


# ── resolve_ludusavi_path ────────────────────────────────────────────
def _prefix(tmp_path):
    pfx = tmp_path / "pfx"
    (pfx / "drive_c" / "users" / "steamuser").mkdir(parents=True)
    return str(pfx)


def test_ludusavi_appdata_is_roaming(tmp_path):
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path("<winAppData>/Foo/Saves", pfx)
    assert got.endswith("drive_c/users/steamuser/AppData/Roaming/Foo/Saves")


def test_ludusavi_localappdata(tmp_path):
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path("<winLocalAppData>/Bar", pfx)
    assert got.endswith("AppData/Local/Bar")


def test_ludusavi_home_and_documents(tmp_path):
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path("<home>/Documents/My Games", pfx)
    assert got.endswith("drive_c/users/steamuser/Documents/My Games")


def test_ludusavi_base_is_install_dir(tmp_path):
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path("<base>/save", pfx, "/games/X")
    assert got == os.path.realpath("/games/X/save")


def test_ludusavi_base_without_install_returns_none(tmp_path):
    pfx = _prefix(tmp_path)
    assert WinePrefixResolver.resolve_ludusavi_path("<base>/save", pfx, "") is None


def test_ludusavi_wildcard_truncates_to_dir(tmp_path):
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path("<base>/save/user_*.dat", pfx, "/g")
    assert got == os.path.realpath("/g/save")


def test_ludusavi_storeuserid_truncates_to_parent(tmp_path):
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path(
        "<winAppData>/Game_EGS/<storeUserId>", pfx,
    )
    assert got.endswith("AppData/Roaming/Game_EGS")


def test_ludusavi_linux_token_returns_none(tmp_path):
    pfx = _prefix(tmp_path)
    assert WinePrefixResolver.resolve_ludusavi_path("<xdgConfig>/Foo", pfx) is None


def test_ludusavi_leading_dynamic_returns_none(tmp_path):
    pfx = _prefix(tmp_path)
    assert WinePrefixResolver.resolve_ludusavi_path("<storeUserId>/1/remote", pfx) is None


# ── save_location_resolver.resolve_save_dir ──────────────────────────
class _FakeCache:
    def __init__(self, data):
        self._data = data

    def get(self, namespace, key):
        return self._data.get((namespace, key))


def test_resolve_save_dir_prefers_ondisk_generic_skips_steam(tmp_path):
    pfx = tmp_path / "pfx"
    real = pfx / "drive_c" / "users" / "steamuser" / "AppData" / "Roaming" / "MyGame" / "Saves"
    real.mkdir(parents=True)
    cache = _FakeCache({
        ("metadata", "gog:42"): {
            "save_locations": [
                {"path": "<root>/userdata/<storeUserId>/1/remote", "tags": ["save"], "stores": ["steam"]},
                {"path": "<winAppData>/MyGame/Saves", "tags": ["save"], "stores": []},
            ],
        },
    })
    got = slr.resolve_save_dir(
        "gog", "42", prefix_path=str(pfx), install_path="", cache=cache,
    )
    assert got == os.path.realpath(str(real))


def test_resolve_save_dir_falls_back_to_pcgw_cache(tmp_path):
    pfx = tmp_path / "pfx"
    (pfx / "drive_c" / "users" / "steamuser").mkdir(parents=True)
    cache = _FakeCache({
        ("pcgw_saves", "epic:abc"): {
            "save_locations": [
                {"path": "<winAppData>/EpicGame/saves", "tags": ["save"], "stores": []},
            ],
        },
    })
    got = slr.resolve_save_dir(
        "epic", "abc", prefix_path=str(pfx), install_path="", cache=cache,
    )
    assert got.endswith("AppData/Roaming/EpicGame/saves")


def test_resolve_save_dir_none_when_no_cache():
    assert slr.resolve_save_dir("gog", "1", prefix_path="/x", cache=None) is None


def test_resolve_save_dir_skips_negative_entry(tmp_path):
    cache = _FakeCache({("metadata", "gog:1"): {"_negative": True}})
    assert slr.resolve_save_dir("gog", "1", prefix_path="/x", cache=cache) is None


class _Cfg:
    def __init__(self, games_map_path):
        self._gm = games_map_path

    def get(self, key, default=None):
        return self._gm if key == "paths.games_map" else default


def test_resolve_save_dir_keeps_foreign_tagged_install_dir_as_backup(tmp_path):
    # Half-Life 2's <base>/save is tagged 'steam' in Ludusavi, but a GOG copy
    # saves to the same install-dir path — so it must NOT be skipped.
    gm = tmp_path / "games.map"
    gm.write_text("gog:70=/games/HL2/hl2.exe\t/games/HL2\t-1\n", encoding="utf-8")
    cache = _FakeCache({
        ("metadata", "gog:70"): {
            "save_locations": [
                {"path": "<root>/userdata/<storeUserId>/220/remote", "tags": ["save"], "stores": ["steam"]},
                {"path": "<base>/hl2/save", "tags": ["save"], "stores": ["steam"]},
            ],
        },
    })
    got = slr.resolve_save_dir(
        "gog", "70", prefix_path=str(tmp_path / "pfx"), config=_Cfg(str(gm)), cache=cache,
    )
    # The store-agnostic install-dir path is used; the Steam cloud-mirror is not.
    assert got == os.path.realpath("/games/HL2/hl2/save")


def test_foreign_cloud_path_detection():
    assert slr._is_foreign_cloud_path("<root>/userdata/<storeUserId>/220/remote")
    assert not slr._is_foreign_cloud_path("<base>/hl2/save")
    assert not slr._is_foreign_cloud_path("<winAppData>/Game/saves")


def test_install_path_from_games_map_custom_location(tmp_path):
    # games.map records a user-chosen (e.g. SD-card) install dir per game.
    gm = tmp_path / "games.map"
    gm.write_text(
        "gog:55=/run/media/deck/SD/Games/Spire/Spire.exe\t"
        "/run/media/deck/SD/Games/Spire\t-12345\n",
        encoding="utf-8",
    )
    cfg = _Cfg(str(gm))
    assert slr._install_path_from_games_map("gog", "55", cfg) == "/run/media/deck/SD/Games/Spire"
    # <base> save resolves into that custom location, not a default dir.
    cache = _FakeCache({
        ("metadata", "gog:55"): {
            "save_locations": [{"path": "<base>/preferences", "tags": ["save"], "stores": []}],
        },
    })
    got = slr.resolve_save_dir("gog", "55", prefix_path=str(tmp_path / "pfx"), config=cfg, cache=cache)
    assert got == os.path.realpath("/run/media/deck/SD/Games/Spire/preferences")
