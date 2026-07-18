"""Compat phase conditional sync: skip cached games, stop endless re-fetches.

Before the partition existed, ``CompatibilityService`` visited every
game every sync: titles that never resolve on Steam re-ran
``search_store`` forever (never negative-cached), and cache entries
with no published Deck-Verified test results re-hit the endpoint every
sync (the self-heal branch had no terminal marker).
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, patch

import pytest

from unifideck.compatibility.library import CompatLibrary, CompatRating
from unifideck.services.compatibility.service import CompatibilityService


class _Store:
    def __init__(self) -> None:
        self._data: dict[str, object] = {}

    def get(self, k: str, default: object = None) -> object:
        return self._data.get(k, default)

    def set(self, k: str, v: object) -> None:
        self._data[k] = v


class _Cache:
    def __init__(self) -> None:
        self._stores: dict[str, _Store] = {
            "compat": _Store(),
            "steam_real_appid": _Store(),
        }

    def register(self, *a: object, **k: object) -> None:
        pass

    def get(self, ns: str, k: str) -> object:
        return self._stores.setdefault(ns, _Store()).get(k)

    def set(
        self, ns: str, k: str, v: object, *, flush: bool = True,
    ) -> None:
        self._stores.setdefault(ns, _Store()).set(k, v)

    def flush(self, ns: str) -> None:
        pass


class _Bus:
    """Minimal EventBus stand-in: auto_wire only needs ``on``."""

    def on(self, *a: object, **k: object) -> None:
        pass


def _game(app_id: int, title: str) -> Any:
    return SimpleNamespace(app_id=app_id, title=title)


def _service(cache: _Cache) -> CompatibilityService:
    return CompatibilityService(bus=_Bus(), cache=cache)  # type: ignore[arg-type]


_SHORTCUT = -1514014196
_REAL = 945360


# ── service partition ─────────────────────────────────────────────


def test_partition_skips_fully_cached_game() -> None:
    cache = _Cache()
    cache.set("steam_real_appid", str(_SHORTCUT), _REAL)
    cache.set("compat", str(_REAL), {
        "deck_status": "playable",
        "deck_test_results": [{"text": "ok", "passed": True}],
    })
    svc = _service(cache)
    skipped, pending = svc._partition_games([_game(_SHORTCUT, "Among Us")])
    assert [g.title for g in skipped] == ["Among Us"]
    assert pending == []


def test_partition_skips_negative_mapping_without_searching() -> None:
    # A title metadata negative-cached (-1) must not re-run
    # search_store every sync — that was the endless re-fetch.
    cache = _Cache()
    cache.set("steam_real_appid", str(_SHORTCUT), -1)
    svc = _service(cache)
    skipped, pending = svc._partition_games([_game(_SHORTCUT, "Obscure Game")])
    assert len(skipped) == 1
    assert pending == []


def test_partition_keeps_unresolved_and_uncached_pending() -> None:
    cache = _Cache()
    # Game A: no mapping at all. Game B: mapping but no compat entry.
    cache.set("steam_real_appid", "222", 111)
    svc = _service(cache)
    skipped, pending = svc._partition_games(
        [_game(333, "Never Seen"), _game(222, "Mapped Only")],
    )
    assert skipped == []
    assert {g.title for g in pending} == {"Never Seen", "Mapped Only"}


def test_partition_retries_self_heal_exactly_once() -> None:
    cache = _Cache()
    cache.set("steam_real_appid", str(_SHORTCUT), _REAL)
    # Old-format entry: known status, no test results, no marker →
    # eligible for ONE upgrade fetch.
    entry: dict[str, Any] = {"deck_status": "playable", "deck_test_results": []}
    cache.set("compat", str(_REAL), entry)
    svc = _service(cache)
    _, pending = svc._partition_games([_game(_SHORTCUT, "Among Us")])
    assert len(pending) == 1
    # Once stamped, the same entry is terminal.
    entry["dtr_checked"] = True
    cache.set("compat", str(_REAL), entry)
    skipped, pending = svc._partition_games([_game(_SHORTCUT, "Among Us")])
    assert len(skipped) == 1
    assert pending == []


# ── library negative-cache + self-heal stamp ──────────────────────


@pytest.mark.asyncio
async def test_failed_search_writes_negative_mapping() -> None:
    cache = _Cache()
    lib = CompatLibrary(cache=cache)  # type: ignore[arg-type]
    with patch(
        "unifideck.steam.library.search_store",
        new=AsyncMock(return_value=None),
    ):
        rating = await lib.get_for_title("Nowhere Game", shortcut_app_id=_SHORTCUT)
    assert rating.error == "not_found_on_steam_store"
    assert cache._stores["steam_real_appid"].get(str(_SHORTCUT)) == -1


@pytest.mark.asyncio
async def test_self_heal_stamps_dtr_checked_and_runs_once() -> None:
    cache = _Cache()
    cache.set("compat", str(_REAL), {
        "deck_status": "playable", "deck_test_results": [],
    })
    lib = CompatLibrary(cache=cache)  # type: ignore[arg-type]
    fetch = AsyncMock(return_value=("unknown", []))  # upstream has nothing
    with patch.object(lib, "_fetch_deck_verified", new=fetch):
        first = await lib.get_for_appid(_REAL)
        second = await lib.get_for_appid(_REAL)
    fetch.assert_awaited_once()  # second read is terminal, no re-fetch
    # A transient/empty answer must not downgrade the cached status.
    assert first.deck_status == "playable"
    assert second.deck_status == "playable"
    stored = cache._stores["compat"].get(str(_REAL))
    assert isinstance(stored, dict) and stored["dtr_checked"] is True


def test_compat_rating_tolerates_marker_keys() -> None:
    # Cached dicts now carry ``dtr_checked`` alongside the dataclass
    # fields — reconstruction must filter it, not crash.
    from unifideck.compatibility.library import _rating_from_cached

    rating = _rating_from_cached({
        "appid": _REAL,
        "deck_status": "verified",
        "dtr_checked": True,
    })
    assert isinstance(rating, CompatRating)
    assert rating.deck_status == "verified"
