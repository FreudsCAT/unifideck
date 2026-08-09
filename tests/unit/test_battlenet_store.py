"""The Battle.net store: contract, library join, and non-destructive logout.

The join tested here is the one that silently broke first: install state is
keyed on the product CODE (``hsb``) while the catalog addresses titles by
uid (``hs_beta``). Matching on code reports every installed game as not
installed, and the library still looks plausible — 22 titles, all named,
none playable.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from unifideck.stores.battlenet import BattlenetStore
from unifideck.stores.battlenet import paths as bpaths
from unifideck.stores.battlenet.library import build_library
from unifideck.stores.battlenet.ownership import (
    AccountFacts,
    InstalledGame,
    merge_fragments,
)
from unifideck.stores.shared.store_base import StoreBase

FIXTURES = Path(__file__).parent.parent / "fixtures" / "battlenet"
LAUNCHER = "/plugin/bin/unifideck-launcher"


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[Any, dict]] = []

    async def emit(self, event: Any, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


class _Cache:
    def __init__(self) -> None:
        self.cleared: list[str] = []

    def get(self, *_a: Any, **_k: Any) -> None:
        return None

    def clear(self, name: str) -> None:
        self.cleared.append(name)


class _Config:
    def __init__(self, data_dir: Path, prefixes_dir: Path) -> None:
        self._values = {"data_dir": str(data_dir), "prefixes_dir": str(prefixes_dir)}

    def get(self, key: str, default: Any = None) -> Any:
        return self._values if key == "stores.battlenet" else default


@pytest.fixture
def store(tmp_path: Path) -> BattlenetStore:
    prefixes = tmp_path / "prefixes"
    prefixes.mkdir(parents=True)
    return BattlenetStore(
        _Bus(), _Cache(), plugin_dir="/plugin",
        config=_Config(tmp_path, prefixes),
    )


def _sign_in(store: BattlenetStore, licences: list[int]) -> Path:
    """Create an auth prefix carrying a licence ledger."""
    prefix = store.prefixes.auth_prefix
    drive_c = prefix / "drive_c"
    db = drive_c / "users/steamuser/AppData/Local/Battle.net/CachedData.db"
    db.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE key_value_store (key TEXT, value TEXT)")
    con.execute("CREATE TABLE login_cache (battle_tag TEXT)")
    con.execute(
        "INSERT INTO key_value_store VALUES ('features_cached_data_points', ?)",
        (json.dumps({"licenses": licences, "account_id": 1}),),
    )
    con.commit()
    con.close()
    return prefix


# --------------------------------------------------------------------------
# contract
# --------------------------------------------------------------------------


def test_satisfies_the_storebase_contract() -> None:
    assert issubclass(BattlenetStore, StoreBase)
    assert not inspect.isabstract(BattlenetStore)


def test_store_info_declares_a_wine_wrapper_store() -> None:
    info = BattlenetStore.store_info
    assert info.name == "battlenet"
    assert info.uses_wine is True
    assert info.supports_install is True
    # No cloud-save strategy exists: Blizzard progress is server-side.
    assert info.supports_cloud_saves is False


def test_module_layout_is_auto_discoverable() -> None:
    """``stores/<name>/store.py`` needs no registry edit."""
    assert BattlenetStore.__module__ == "unifideck.stores.battlenet.store"


def test_get_installed_path_is_async_like_the_base() -> None:
    assert inspect.iscoroutinefunction(BattlenetStore.get_installed_path)


# --------------------------------------------------------------------------
# availability
# --------------------------------------------------------------------------


def test_not_available_without_a_client_prefix(store: BattlenetStore) -> None:
    assert asyncio.run(store.is_available()) is False


def test_available_once_the_client_holds_a_licence_ledger(store: BattlenetStore) -> None:
    _sign_in(store, [1, 2, 3])
    assert asyncio.run(store.is_available()) is True


def test_empty_library_without_a_prefix_rather_than_an_error(store: BattlenetStore) -> None:
    assert asyncio.run(store.get_library()) == []


def test_start_auth_reports_a_missing_client_as_structured_error(store: BattlenetStore) -> None:
    result = asyncio.run(store.start_auth())
    assert result.success is False
    assert result.error_code == "client_not_installed"
    assert result.metadata["needs_bootstrap"] is True


# --------------------------------------------------------------------------
# the library join
# --------------------------------------------------------------------------


def _catalog():
    return merge_fragments(iter([json.loads((FIXTURES / "pub_catalog_fragment.json").read_bytes())]))


def test_installed_state_joins_on_uid_not_product_code() -> None:
    """The regression: 'hsb' vs 'hs_beta' made every game look uninstalled."""
    catalog = _catalog()
    entry = catalog.entry_for("ARK")
    uid = entry.uid_for()
    installed = {
        # Keyed by CODE, as aggregate.json/product.db are.
        "arkcode": InstalledGame(code="arkcode", uid=uid, name="ARK", is_ready=True),
    }
    games = build_library(
        catalog,
        AccountFacts(licence_ids=frozenset({1105059})),
        installed,
        launcher_path=LAUNCHER,
    )
    ark = next(g for g in games if g.store_game_id == uid)
    assert ark.installed is True


def test_installed_entry_without_a_uid_does_not_join_a_catalog_title() -> None:
    """No uid means no join key — marking ARK installed would be a guess.

    It still surfaces separately under its own code, because losing an
    installed game is worse than showing it unmatched.
    """
    catalog = _catalog()
    installed = {"arkcode": InstalledGame(code="arkcode", uid=None, is_ready=True)}
    games = build_library(
        catalog, AccountFacts(licence_ids=frozenset({1105059})), installed,
        launcher_path=LAUNCHER,
    )
    ark = next(g for g in games if g.store_game_id == "ark")
    assert ark.installed is False
    orphan = next(g for g in games if g.store_game_id == "arkcode")
    assert orphan.installed is True


def test_library_is_keyed_on_uid_so_family_renames_cannot_orphan_shortcuts() -> None:
    """Blizzard renamed Diablo IV D4 -> Fen; uids never change."""
    catalog = _catalog()
    games = build_library(
        catalog, AccountFacts(licence_ids=frozenset({1105059})), {},
        launcher_path=LAUNCHER,
    )
    ark = next(g for g in games if g.title == "The Outer Worlds 2")
    assert ark.store_game_id == "ark"
    assert ark.metadata["family"] == "ARK"


def test_app_id_is_derived_from_the_uid() -> None:
    from unifideck.services.shortcut.games_map import generate_app_id

    catalog = _catalog()
    games = build_library(
        catalog, AccountFacts(licence_ids=frozenset({1105059})), {},
        launcher_path=LAUNCHER,
    )
    ark = next(g for g in games if g.store_game_id == "ark")
    assert ark.app_id == generate_app_id(LAUNCHER, "battlenet:ark")


def test_an_installed_game_the_rules_did_not_grant_is_kept() -> None:
    """An ownership hiccup must not make an installed game disappear."""
    games = build_library(
        _catalog(),
        AccountFacts(),
        {"zzz": InstalledGame(code="zzz", uid="zzz", name="Mystery", is_ready=True)},
        launcher_path=LAUNCHER,
    )
    assert [g.store_game_id for g in games] == ["zzz"]
    assert games[0].installed is True


def test_mid_download_titles_are_not_reported_installed() -> None:
    games = build_library(
        _catalog(),
        AccountFacts(licence_ids=frozenset({1105059})),
        {"arkcode": InstalledGame(code="arkcode", uid="ark", is_ready=False)},
        launcher_path=LAUNCHER,
    )
    assert next(g for g in games if g.store_game_id == "ark").installed is False


def test_free_to_play_and_handheld_status_become_tags() -> None:
    config = {
        "WTCG": {"run_each_rule": [{
            "match": {"game_account": {"program_id": "WTCG"}},
            "actions": [
                {"add_product": {"product_id": {"id": "WTCG", "type": "retail"}}},
                {"add_tag": {"name": "play_for_free"}},
            ],
        }]},
    }
    catalog = merge_fragments(iter([{
        "fragment_id": "hs",
        "program_configuration": config,
        "products": [{"id": "WTCG", "base": {
            "program_id": "WTCG", "name": "hs#N",
            "handheld_status": ["handheld_unsupported"],
            "types": {"retail": {"uid": "hs_beta"}}}}],
        "strings": {"default": {"hs#N": "Hearthstone"}},
    }]))
    games = build_library(
        catalog, AccountFacts(game_account_programs=frozenset({"WTCG"})), {},
        launcher_path=LAUNCHER,
    )
    assert set(games[0].tags) == {"free_to_play", "handheld_unsupported"}
    assert games[0].title == "Hearthstone"


def test_titles_without_an_install_uid_are_skipped() -> None:
    """A tile that cannot be installed or launched is a dead tile."""
    catalog = merge_fragments(iter([{
        "fragment_id": "x",
        "program_configuration": {"X": {"run_each_rule": [{
            "match": {"license_id": [7]},
            "actions": [{"add_product": {"product_id": {"id": "X", "type": "retail"}}}]}]}},
        "products": [{"id": "X", "base": {"program_id": "X"}}],
    }]))
    assert build_library(
        catalog, AccountFacts(licence_ids=frozenset({7})), {}, launcher_path=LAUNCHER,
    ) == []


# --------------------------------------------------------------------------
# destructive-operation guards
# --------------------------------------------------------------------------


def test_logout_never_touches_a_prefix(store: BattlenetStore) -> None:
    """Opposite of Ubisoft: here the prefix holds the game."""
    prefix = _sign_in(store, [1])
    result = asyncio.run(store.logout())
    assert result.success is True
    assert prefix.is_dir()
    assert bpaths.drive_c(prefix) is not None


def test_uninstall_refuses_when_no_prefix_was_recorded(store: BattlenetStore) -> None:
    result = asyncio.run(store.uninstall_game("wow"))
    assert result.success is False
    assert result.error_code == "prefix_unknown"


def test_uninstall_refuses_a_prefix_we_did_not_create(store: BattlenetStore, tmp_path: Path) -> None:
    stranger = tmp_path / "someone-elses-prefix"
    (stranger / "drive_c").mkdir(parents=True)
    store.id_map.merge("wow", prefix_path=str(stranger))
    result = asyncio.run(store.uninstall_game("wow"))
    assert result.success is False
    assert result.error_code == "prefix_not_owned"
    assert stranger.is_dir()


def test_install_refuses_when_the_template_is_not_warmed(store: BattlenetStore) -> None:
    result = asyncio.run(store.install_game("wow"))
    assert result.success is False
    assert result.error_code == "template_not_ready"


def test_check_for_updates_reports_nothing_rather_than_guessing(store: BattlenetStore) -> None:
    assert asyncio.run(store.check_for_updates()) == []
