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


def test_ludusavi_c_drive_maps_to_drive_c(tmp_path):
    # Absolute Windows drive paths (~106 in the manifest) map C: -> drive_c.
    pfx = _prefix(tmp_path)
    got = WinePrefixResolver.resolve_ludusavi_path("C:/ProgramData/Game", pfx)
    assert got.endswith("drive_c/ProgramData/Game")


# ── realize_case_insensitive (Wine is case-insensitive, Linux isn't) ──
def test_realize_case_insensitive_repairs_existing_dir(tmp_path):
    # Game created 'documents/My Games' but the manifest casing is 'Documents'.
    real = tmp_path / "drive_c" / "users" / "steamuser" / "documents" / "my games"
    real.mkdir(parents=True)
    asked = str(tmp_path / "drive_c" / "users" / "steamuser" / "Documents" / "My Games")
    got = WinePrefixResolver.realize_case_insensitive(asked)
    assert got == str(real)          # matched the real on-disk casing
    assert os.path.isdir(got)


def test_realize_case_insensitive_passthrough_when_exact(tmp_path):
    real = tmp_path / "a" / "B" / "c"
    real.mkdir(parents=True)
    assert WinePrefixResolver.realize_case_insensitive(str(real)) == str(real)


def test_realize_case_insensitive_keeps_literal_when_missing(tmp_path):
    # Non-existent tail is kept verbatim (created on sync).
    base = tmp_path / "drive_c"
    base.mkdir()
    asked = str(base / "Nope" / "Saves")
    assert WinePrefixResolver.realize_case_insensitive(asked) == asked


def test_realize_case_insensitive_ambiguous_keeps_literal(tmp_path):
    # Two siblings differing only in case -> ambiguous -> keep the literal.
    parent = tmp_path / "p"
    parent.mkdir()
    (parent / "Save").mkdir()
    (parent / "save").mkdir()
    asked = str(parent / "SAVE")
    assert WinePrefixResolver.realize_case_insensitive(asked) == asked


def test_resolve_ludusavi_path_uses_case_insensitive_disk(tmp_path):
    # End-to-end: resolver returns the actual on-disk casing.
    pfx = _prefix(tmp_path)
    created = (
        tmp_path / "pfx" / "drive_c" / "users" / "steamuser"
        / "AppData" / "Roaming" / "bioshockhd"
    )
    created.mkdir(parents=True)
    got = WinePrefixResolver.resolve_ludusavi_path("<winAppData>/BioshockHD", pfx)
    assert got.endswith("AppData/Roaming/bioshockhd")  # not 'BioshockHD'


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


# ── native-Linux resolution (GOG ships native builds we install + run) ──
def test_ludusavi_native_linux_xdg_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    monkeypatch.delenv("XDG_CONFIG_HOME", raising=False)
    data = WinePrefixResolver.resolve_ludusavi_path(
        "<xdgData>/IntoTheBreach", "", native_linux=True)
    assert data == os.path.realpath(str(tmp_path / ".local" / "share" / "IntoTheBreach"))
    conf = WinePrefixResolver.resolve_ludusavi_path(
        "<xdgConfig>/unity3d/Foo", "", native_linux=True)
    assert conf == os.path.realpath(str(tmp_path / ".config" / "unity3d" / "Foo"))


def test_ludusavi_native_linux_xdg_env_override(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xdgdata"))
    got = WinePrefixResolver.resolve_ludusavi_path(
        "<xdgData>/Game", "", native_linux=True)
    assert got == os.path.realpath(str(tmp_path / "xdgdata" / "Game"))


def test_ludusavi_native_linux_home_and_base(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    home = WinePrefixResolver.resolve_ludusavi_path(
        "<home>/.localshare", "", native_linux=True)
    assert home == os.path.realpath(str(tmp_path / ".localshare"))
    base = WinePrefixResolver.resolve_ludusavi_path(
        "<base>/saves", "", "/games/X", native_linux=True)
    assert base == os.path.realpath("/games/X/saves")


def test_ludusavi_native_linux_rejects_windows_token(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    # Windows tokens don't apply to a native-Linux game.
    assert WinePrefixResolver.resolve_ludusavi_path(
        "<winAppData>/Foo", "/pfx", native_linux=True) is None


def test_windows_mode_still_rejects_xdg(tmp_path):
    # And the default (Proton) mode still rejects Linux tokens.
    pfx = _prefix(tmp_path)
    assert WinePrefixResolver.resolve_ludusavi_path("<xdgData>/Foo", pfx) is None


def test_select_rows_prefers_os_match():
    rows = [
        {"path": "<winAppData>/W", "tags": ["save"], "os": ["windows"]},
        {"path": "<xdgData>/L", "tags": ["save"], "os": ["linux"]},
    ]
    assert slr._select_rows(rows, "gog", native_linux=False)[0]["path"] == "<winAppData>/W"
    assert slr._select_rows(rows, "gog", native_linux=True)[0]["path"] == "<xdgData>/L"


def test_resolve_save_dir_native_linux_picks_linux_row(tmp_path, monkeypatch):
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.delenv("XDG_DATA_HOME", raising=False)
    cache = _FakeCache({("metadata", "gog:1"): {"save_locations": [
        {"path": "<winLocalAppData>/Game", "tags": ["save"], "os": ["windows"]},
        {"path": "<xdgData>/Game", "tags": ["save"], "os": ["linux"]},
    ]}})
    got = slr.resolve_save_dir(
        "gog", "1", prefix_path="/pfx", cache=cache, native_linux=True)
    assert got == os.path.realpath(str(tmp_path / ".local" / "share" / "Game"))
