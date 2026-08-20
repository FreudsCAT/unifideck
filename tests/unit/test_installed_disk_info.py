"""Tests for services/installed_disk_info.py — the QAM "Installed" meta line.

Covers the two things the Downloads tab shows next to a game title:

* the internal/external label, whose whole definition is "same
  filesystem device as ``~``" (so a wrong ``st_dev`` reading must degrade
  to "unknown", never to a guess);
* the bulk collector, whose contract is that it keys on
  ``store_game_id``, skips not-installed games, memoises the expensive
  directory walk, and omits — rather than zero-fills — anything it
  cannot resolve.

The size walk itself is covered by the shared installed-size helper; here
it is stubbed so the tests never touch a real multi-gigabyte tree.
"""
from __future__ import annotations

import os
from typing import Any

import pytest

from unifideck.core.types.domain import Game
from unifideck.services import installed_disk_info as mod
from unifideck.utils import mounts

HOME_DEV = 42
OTHER_DEV = 99


@pytest.fixture(autouse=True)
def _clear_memo() -> Any:
    """The memo is process-wide by design — isolate every test from it."""
    mod.clear_memo()
    yield
    mod.clear_memo()


def _game(
    store: str = "gog",
    game_id: str = "123",
    *,
    installed: bool = True,
    install_path: str | None = "/home/deck/Games/Bastion",
) -> Game:
    return Game(
        app_id=1,
        store=store,
        store_game_id=game_id,
        title="Bastion",
        installed=installed,
        install_path=install_path,
    )


def _stat_dev_map(
    monkeypatch: pytest.MonkeyPatch, mapping: dict[str, int],
) -> None:
    """Stub ``mounts.stat_dev`` with a path → device-id table (0 = failure)."""
    home = os.path.expanduser("~")
    table = {home: HOME_DEV, **mapping}
    monkeypatch.setattr(mod.mounts, "stat_dev", lambda p: table.get(p, 0))


# ── classify_location ────────────────────────────────────────────


def test_same_device_as_home_is_internal(monkeypatch: pytest.MonkeyPatch) -> None:
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})

    assert mod.classify_location("/home/deck/Games/Bastion") == "internal"


def test_different_device_is_external(monkeypatch: pytest.MonkeyPatch) -> None:
    _stat_dev_map(monkeypatch, {"/run/media/deck/SD/Games/Bastion": OTHER_DEV})

    assert mod.classify_location("/run/media/deck/SD/Games/Bastion") == "external"


def test_unstattable_path_is_unknown_not_internal(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """``stat_dev`` returns 0 on failure — that must not collide with home."""
    _stat_dev_map(monkeypatch, {})

    assert mod.classify_location("/gone/Games/Bastion") is None


def test_unstattable_home_is_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        mod.mounts, "stat_dev", lambda p: 0 if p == os.path.expanduser("~") else OTHER_DEV,
    )

    assert mod.classify_location("/run/media/deck/SD/Bastion") is None


@pytest.mark.parametrize("path", ["", None])
def test_empty_path_is_unknown(path: str | None) -> None:
    assert mod.classify_location(path) is None


def test_classify_uses_the_same_home_definition_as_scan_mounts() -> None:
    """Guard the invariant, not an implementation detail.

    ``scan_mounts`` decides what counts as *external* by excluding the
    device hosting ``$HOME``. If the classifier ever drifted to a
    different notion of home, a drive could be enumerated as external
    while its installs read "Internal".
    """
    assert mounts.stat_dev(os.path.expanduser("~")) != 0


# ── collect_installed_disk_info ──────────────────────────────────


@pytest.mark.asyncio
async def test_collects_size_and_location_keyed_by_store_game_id(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())
    monkeypatch.setattr(mod, "dir_size_bytes", lambda p: 2048)

    result = await mod.collect_installed_disk_info(
        [_game(store="gog", game_id="abc")], registry=None,
    )

    assert result == {"gog:abc": {"size_bytes": 2048, "location": "internal"}}


@pytest.mark.asyncio
async def test_not_installed_games_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())
    monkeypatch.setattr(mod, "dir_size_bytes", lambda p: 2048)

    result = await mod.collect_installed_disk_info(
        [
            _game(game_id="yes"),
            _game(game_id="no", installed=False),
        ],
        registry=None,
    )

    assert set(result) == {"gog:yes"}


@pytest.mark.asyncio
async def test_games_missing_identity_are_skipped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No store or no store_game_id → nothing the frontend could key on."""
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())
    monkeypatch.setattr(mod, "dir_size_bytes", lambda p: 2048)

    result = await mod.collect_installed_disk_info(
        [_game(store="", game_id="a"), _game(game_id="")], registry=None,
    )

    assert result == {}


@pytest.mark.asyncio
async def test_gone_install_dir_is_omitted(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Neither a size nor a location → no entry, so the row shows no meta."""
    _stat_dev_map(monkeypatch, {})

    async def _unresolvable(
        adapter: Any, path: Any, game_id: Any, store: Any = None,
    ) -> str | None:
        return None

    monkeypatch.setattr(mod, "resolve_size_root", _unresolvable)

    result = await mod.collect_installed_disk_info([_game()], registry=None)

    assert result == {}


@pytest.mark.asyncio
async def test_walk_failure_omits_the_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())

    def _boom(path: str) -> int:
        raise OSError("permission denied")

    monkeypatch.setattr(mod, "dir_size_bytes", _boom)

    result = await mod.collect_installed_disk_info([_game()], registry=None)

    assert result == {}


@pytest.mark.asyncio
async def test_location_describes_the_directory_that_was_sized(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A stale cached path must not label a size taken from elsewhere.

    Sync-detected Epic installs land with ``install_path=None`` and a
    moved game leaves a dead path; both resolve through the store's own
    records. The label has to follow that resolution or the row reads
    "Internal" for a game living on the SD card.
    """
    real_dir = "/run/media/deck/SD/Games/Bastion"
    _stat_dev_map(monkeypatch, {real_dir: OTHER_DEV, "/stale/path": HOME_DEV})

    async def _resolve_elsewhere(
        adapter: Any, path: Any, game_id: Any, store: Any = None,
    ) -> str:
        return real_dir

    monkeypatch.setattr(mod, "resolve_size_root", _resolve_elsewhere)
    monkeypatch.setattr(mod, "dir_size_bytes", lambda p: 4096)

    result = await mod.collect_installed_disk_info(
        [_game(install_path="/stale/path")], registry=None,
    )

    assert result["gog:123"]["location"] == "external"


@pytest.mark.asyncio
async def test_second_call_is_served_from_the_memo(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The walk is the expensive part — a repeat panel open must not pay it."""
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())
    walks: list[str] = []

    def _counting(path: str) -> int:
        walks.append(path)
        return 1024

    monkeypatch.setattr(mod, "dir_size_bytes", _counting)

    first = await mod.collect_installed_disk_info([_game()], registry=None)
    second = await mod.collect_installed_disk_info([_game()], registry=None)

    assert first == second
    assert len(walks) == 1


@pytest.mark.asyncio
async def test_expired_memo_entry_is_recomputed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())
    sizes = iter([1024, 8192])
    monkeypatch.setattr(mod, "dir_size_bytes", lambda p: next(sizes))
    monkeypatch.setattr(mod, "MEMO_TTL_S", -1.0)

    first = await mod.collect_installed_disk_info([_game()], registry=None)
    second = await mod.collect_installed_disk_info([_game()], registry=None)

    assert first["gog:123"]["size_bytes"] == 1024
    assert second["gog:123"]["size_bytes"] == 8192


@pytest.mark.asyncio
async def test_registry_failure_still_yields_an_entry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A broken store adapter must not cost the cached-path size lookup."""
    _stat_dev_map(monkeypatch, {"/home/deck/Games/Bastion": HOME_DEV})
    monkeypatch.setattr(mod, "resolve_size_root", _resolver())
    monkeypatch.setattr(mod, "dir_size_bytes", lambda p: 512)

    class _Broken:
        def get_store(self, store: str) -> Any:
            raise RuntimeError("registry not ready")

    result = await mod.collect_installed_disk_info([_game()], registry=_Broken())

    assert result["gog:123"]["size_bytes"] == 512


@pytest.mark.asyncio
async def test_empty_input_short_circuits() -> None:
    assert await mod.collect_installed_disk_info([], registry=None) == {}
    assert await mod.collect_installed_disk_info(None, registry=None) == {}


def _resolver() -> Any:
    """Stub ``resolve_size_root`` that echoes the cached path back."""

    async def _resolve(
        adapter: Any, path: Any, game_id: Any, store: Any = None,
    ) -> str | None:
        return path or None

    return _resolve
