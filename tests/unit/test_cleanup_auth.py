"""Tests for the auth-clearing side of ``perform_full_cleanup``.

Covers the three pieces hardened so "Delete all Unifideck data" is an
authoritative sign-out:

* ``_logout_all_stores`` counts only stores that *actually* reported a
  successful logout (the registry maps each store to a
  ``{"success", "error"}`` dict — a naive ``if v`` over-counts every
  store).
* ``_delete_auth_data`` unlinks each store's persisted credential file.
* ``_reset_store_availability`` clears the in-memory ``_cached_available``
  flag on every registered store.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from unifideck.rpc.mixins.sync import SyncRPCMixin
from unifideck.services.artwork.event_handlers import _EventHandlersMixin
from unifideck.services.artwork.fetcher import delete_artwork_files


def _mixin(**attrs: Any) -> SyncRPCMixin:
    m = SyncRPCMixin()
    for k, v in attrs.items():
        setattr(m, k, v)
    return m


class _Registry:
    def __init__(self, results: dict[str, Any], stores: dict[str, Any]):
        self._results = results
        self._stores = stores

    async def logout_all(self) -> dict[str, Any]:
        return self._results


@pytest.mark.asyncio
async def test_logout_all_stores_counts_only_successes() -> None:
    registry = _Registry(
        results={
            "epic": {"success": True, "error": None},
            "gog": {"success": False, "error": "boom"},
            "amazon": {"success": True, "error": None},
        },
        stores={},
    )
    m = _mixin(registry=registry)

    assert await m._logout_all_stores() == 2


@pytest.mark.asyncio
async def test_logout_all_stores_handles_missing_registry() -> None:
    m = _mixin(registry=None)
    assert await m._logout_all_stores() == 0


@pytest.mark.asyncio
async def test_delete_auth_data_unlinks_credential_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))

    # The credential files each store's ``is_available`` probe reads.
    creds = [
        tmp_path / ".config/legendary/user.json",
        tmp_path / ".config/nile/user.json",
        tmp_path / ".config/unifideck/gog_token.json",
        tmp_path / ".config/unifideck/gogdl/gog_credentials.json",
        tmp_path / ".config/unifideck/microsoft_tokens.json",
        tmp_path / ".local/share/unifideck/microsoft_tokens.json",
    ]
    for f in creds:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("secret")

    m = _mixin()
    count = await m._delete_auth_data()

    assert count == len(creds)
    for f in creds:
        assert not f.exists()


@pytest.mark.asyncio
async def test_delete_auth_data_is_safe_when_nothing_present(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    m = _mixin()
    assert await m._delete_auth_data() == 0


def test_reset_store_availability_clears_cached_flag() -> None:
    stores = {
        "epic": SimpleNamespace(_cached_available=True, store_name="epic"),
        "gog": SimpleNamespace(_cached_available=True, store_name="gog"),
    }
    m = _mixin(registry=SimpleNamespace(_stores=stores))

    m._reset_store_availability()

    assert all(not s._cached_available for s in stores.values())


def test_reset_store_availability_handles_missing_registry() -> None:
    # Should not raise when the registry or its store map is absent.
    _mixin(registry=None)._reset_store_availability()
    _mixin(registry=SimpleNamespace())._reset_store_availability()


# --- artwork deletion -------------------------------------------------

# bit 0x80000000 set → every Unifideck shortcut appid is ≥ 2³¹.
_UNSIGNED = 0x80000000 + 12345  # 2147495993


def _grid_mixin(grid_dir: Path) -> SyncRPCMixin:
    artwork = SimpleNamespace(grid_dir=str(grid_dir))
    return _mixin(services=SimpleNamespace(artwork=artwork))


def _write_all_art(grid_dir: Path, unsigned: int) -> list[Path]:
    """Create one file per artwork kind, named as the fetcher names them."""
    names = [
        f"{unsigned}p.jpg",      # grid (portrait)
        f"{unsigned}.jpg",       # grid_l (landscape header)
        f"{unsigned}_hero.jpg",  # hero banner
        f"{unsigned}_logo.png",  # logo
        f"{unsigned}_icon.jpg",  # icon
    ]
    files = []
    for n in names:
        p = grid_dir / n
        p.write_bytes(b"img")
        files.append(p)
    return files


# --- fetcher.delete_artwork_files (single appid, unconditional) -------

@pytest.mark.asyncio
async def test_delete_artwork_files_removes_every_kind(tmp_path: Path) -> None:
    grid = tmp_path / "grid"
    grid.mkdir()
    art = _write_all_art(grid, _UNSIGNED)
    bystander = grid / "730p.jpg"  # real Steam appid (< 2³¹)
    bystander.write_bytes(b"cs2")

    count = await delete_artwork_files(str(grid), _UNSIGNED)

    assert count == len(art)
    assert all(not p.exists() for p in art)
    assert bystander.exists()


@pytest.mark.asyncio
async def test_delete_artwork_files_accepts_signed_appid(tmp_path: Path) -> None:
    """Art is named with the unsigned id; a signed (negative) input must
    resolve to the same prefix and still delete it."""
    grid = tmp_path / "grid"
    grid.mkdir()
    art = _write_all_art(grid, _UNSIGNED)
    signed = _UNSIGNED - 0x100000000

    assert await delete_artwork_files(str(grid), signed) == len(art)
    assert all(not p.exists() for p in art)


@pytest.mark.asyncio
async def test_delete_artwork_files_noop_when_absent(tmp_path: Path) -> None:
    grid = tmp_path / "grid"
    grid.mkdir()
    # No files for this appid → "try to delete" yields 0, no error.
    assert await delete_artwork_files(str(grid), _UNSIGNED) == 0
    assert await delete_artwork_files(str(tmp_path / "missing"), _UNSIGNED) == 0


# --- Fix A: artwork cleanup on SHORTCUT_REMOVED -----------------------

@pytest.mark.asyncio
async def test_on_shortcut_removed_deletes_artwork(tmp_path: Path) -> None:
    grid = tmp_path / "grid"
    grid.mkdir()
    art = _write_all_art(grid, _UNSIGNED)
    stub = SimpleNamespace(_grid_dir=str(grid))

    await _EventHandlersMixin._on_shortcut_removed(stub, app_id=_UNSIGNED)

    assert all(not p.exists() for p in art)


@pytest.mark.asyncio
async def test_on_shortcut_removed_suppressed_during_bulk(tmp_path: Path) -> None:
    """Bulk 'delete all data' sets a flag and sweeps the grid itself, so
    the per-game handler must skip (no redundant per-shortcut globbing)."""
    grid = tmp_path / "grid"
    grid.mkdir()
    art = _write_all_art(grid, _UNSIGNED)
    stub = SimpleNamespace(_grid_dir=str(grid), _suppress_removal_cleanup=True)

    await _EventHandlersMixin._on_shortcut_removed(stub, app_id=_UNSIGNED)

    assert all(p.exists() for p in art)  # untouched — sweep will handle it


@pytest.mark.asyncio
async def test_on_shortcut_removed_ignores_bad_payload(tmp_path: Path) -> None:
    grid = tmp_path / "grid"
    grid.mkdir()
    art = _write_all_art(grid, _UNSIGNED)
    stub = SimpleNamespace(_grid_dir=str(grid))

    # Missing / non-int app_id must be a no-op (no crash, art untouched).
    await _EventHandlersMixin._on_shortcut_removed(stub)
    await _EventHandlersMixin._on_shortcut_removed(stub, app_id="oops")
    assert all(p.exists() for p in art)


# --- Fix B: full-delete sweep (all non-Steam art except keep set) -----

@pytest.mark.asyncio
async def test_delete_nonsteam_artwork_wipes_orphans_keeps_others(
    tmp_path: Path,
) -> None:
    grid = tmp_path / "grid"
    grid.mkdir()
    unifideck = _write_all_art(grid, _UNSIGNED)          # our current art
    orphan = _write_all_art(grid, 0x80000000 + 99999)    # no shortcut left
    foreign_unsigned = 0x80000000 + 555                  # a Heroic shortcut
    foreign = _write_all_art(grid, foreign_unsigned)
    steam = grid / "730p.jpg"                            # real Steam art
    steam.write_bytes(b"cs2")

    m = _grid_mixin(grid)
    count = await m._delete_nonsteam_artwork(keep_appids={foreign_unsigned})

    # Unifideck + orphan art gone; foreign + Steam art preserved.
    assert count == len(unifideck) + len(orphan)
    assert all(not p.exists() for p in unifideck)
    assert all(not p.exists() for p in orphan)
    assert all(p.exists() for p in foreign)
    assert steam.exists()


@pytest.mark.asyncio
async def test_delete_nonsteam_artwork_noop_without_grid() -> None:
    m = _mixin(services=SimpleNamespace(artwork=None))
    assert await m._delete_nonsteam_artwork(keep_appids=set()) == 0


def test_nonunifideck_unsigned_appids_filters_owned() -> None:
    # Two shortcuts: one Unifideck-tagged (excluded), one foreign (kept).
    from unifideck.services.shortcut.games_map import UNIFIDECK_TAG

    shortcuts = {
        "shortcuts": {
            "0": {"appid": -11936521, "tags": {"0": "Heroic"},
                  "LaunchOptions": ""},
            "1": {"appid": -1379918704, "tags": {"0": UNIFIDECK_TAG},
                  "LaunchOptions": "amazon:amzn1.adg.product.x"},
        }
    }
    svc = SimpleNamespace(_shortcuts=shortcuts)
    keep = SyncRPCMixin._nonunifideck_unsigned_appids(svc)

    assert keep == {(-11936521) + 0x100000000}


@pytest.mark.asyncio
async def test_microsoft_tokens_legacy_migration(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    import json
    from unifideck.stores.microsoft.microsoft_config import MicrosoftConfig
    from unifideck.stores.microsoft.tokens.persistence import PersistenceMixin
    from unifideck.security import SecureTokenStore

    monkeypatch.setenv("HOME", str(tmp_path))

    legacy_file = tmp_path / ".local/share/unifideck/microsoft_tokens.json"
    new_file = tmp_path / ".config/unifideck/microsoft_tokens.json"
    legacy_file.parent.mkdir(parents=True, exist_ok=True)

    payload = {
        "access_token": "mock_access",
        "refresh_token": "mock_refresh",
        "saved_at": 12345.0,
    }
    legacy_file.write_text(json.dumps(payload))

    config = MicrosoftConfig(token_file=str(new_file))
    secure_store = SecureTokenStore()

    pm = PersistenceMixin()
    pm._config = config
    pm._secure_store = secure_store
    pm._bus = None
    pm._ms_access_token = None
    pm._ms_refresh_token = None
    pm._token_saved_at = 0.0

    loaded = await pm.load()

    assert loaded is True
    assert pm._ms_access_token == "mock_access"
    assert pm._ms_refresh_token == "mock_refresh"
    assert new_file.exists()
    assert not legacy_file.exists()
