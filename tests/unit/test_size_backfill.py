"""Post-sync warm-up of the download-size cache.

"Space Required" is resolved lazily per game and each lookup is a live store
call (``legendary info`` / gogdl planner / ``nile install --info``), so a cold
cache means a multi-second gap on the App-Details page. Measured on a real
device: 51 of 611 owned Epic/GOG/Amazon games had a cached size, so ~92% of
page opens paid that wait.

This walk fills the same persistent cache in the background after a sync.
Its hard requirements: never re-fetch what is cached, never touch stores that
cannot answer, and never let a failure escape into the sync that spawned it.
"""
from __future__ import annotations

import asyncio
from types import SimpleNamespace
from typing import Any

import pytest

from unifideck.services import size_backfill


def _game(store: str, gid: str, *, installed: bool = False) -> Any:
    return SimpleNamespace(
        store=store, store_game_id=gid, title=gid, installed=installed,
    )


class _Cache:
    def __init__(self, seeded: dict[str, int] | None = None) -> None:
        self.data = dict(seeded or {})
        self.puts: list[tuple[str, str, int]] = []

    async def get(self, store: str, gid: str) -> int | None:
        return self.data.get(f"{store}:{gid}")

    async def put(self, store: str, gid: str, size: int) -> None:
        self.data[f"{store}:{gid}"] = size
        self.puts.append((store, gid, size))


class _Registry:
    """Registry whose adapters record every size lookup they receive."""

    def __init__(self, sizes: dict[str, int], *, missing: bool = False) -> None:
        self.sizes = sizes
        self.calls: list[str] = []
        self._missing = missing

    def get_store(self, store: str) -> Any:
        if self._missing:
            return None

        async def get_game_size(gid: str) -> int | None:
            self.calls.append(f"{store}:{gid}")
            return self.sizes.get(f"{store}:{gid}")
        return SimpleNamespace(get_game_size=get_game_size)


# ── selection ────────────────────────────────────────────────────────


def test_only_size_capable_stores_are_walked() -> None:
    games = [
        _game("epic", "a"), _game("gog", "b"), _game("amazon", "c"),
        _game("ubisoft", "d"), _game("microsoft", "e"),
    ]
    assert [g.store for g in size_backfill._pending(games)] == [
        "epic", "gog", "amazon",
    ]


def test_installed_games_are_skipped() -> None:
    """Their size comes from a local directory walk, not the store."""
    games = [_game("epic", "a", installed=True), _game("epic", "b")]
    assert [g.store_game_id for g in size_backfill._pending(games)] == ["b"]


def test_games_without_an_id_are_skipped() -> None:
    assert size_backfill._pending([_game("epic", "")]) == []


# ── the walk ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_resolves_and_persists_missing_sizes(monkeypatch) -> None:
    cache = _Cache()
    reg = _Registry({"epic:a": 1234, "gog:b": 5678})
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)

    await size_backfill._run(reg, [_game("epic", "a"), _game("gog", "b")], "/x")

    assert cache.data == {"epic:a": 1234, "gog:b": 5678}
    assert sorted(reg.calls) == ["epic:a", "gog:b"]


@pytest.mark.asyncio
async def test_already_cached_games_cost_no_lookup(monkeypatch) -> None:
    """The whole point: a warm library must not re-hit the storefronts."""
    cache = _Cache({"epic:a": 999})
    reg = _Registry({"epic:a": 1234})
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)

    await size_backfill._run(reg, [_game("epic", "a")], "/x")

    assert reg.calls == []
    assert cache.data["epic:a"] == 999


@pytest.mark.asyncio
async def test_walk_failures_do_not_write_an_unknown_stamp(monkeypatch) -> None:
    """The walk must never suppress a later on-demand lookup.

    Its misses are not always the store's fault: 63 GOG games came back
    empty during a parallel walk and every one resolved first try when
    called individually. Stamping those "unknown" would hide sizes that
    actually work, so only the user-facing RPC records a miss.
    """
    cache = _Cache()
    marks: list[tuple[str, str]] = []

    async def mark_unknown(store: str, gid: str) -> None:
        marks.append((store, gid))
    cache.mark_unknown = mark_unknown  # type: ignore[attr-defined]

    class _Failing(_Registry):
        def get_store(self, store: str) -> Any:
            async def get_game_size(_gid: str) -> int:
                raise RuntimeError("boom")
            return SimpleNamespace(get_game_size=get_game_size)

    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    await size_backfill._run(_Failing({}), [_game("gog", "a")], "/x")
    assert marks == []


@pytest.mark.asyncio
async def test_lookups_are_serialised_per_store(monkeypatch) -> None:
    """Two gogdl processes race on a shared config dir and both fail."""
    cache = _Cache()
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    live: dict[str, int] = {"gog": 0, "epic": 0}
    peak: dict[str, int] = {"gog": 0, "epic": 0}

    class _Tracking(_Registry):
        def get_store(self, store: str) -> Any:
            async def get_game_size(_gid: str) -> int:
                live[store] += 1
                peak[store] = max(peak[store], live[store])
                await asyncio.sleep(0.02)
                live[store] -= 1
                return 5
            return SimpleNamespace(get_game_size=get_game_size)

    games = [_game("gog", f"g{i}") for i in range(4)]
    games += [_game("epic", f"e{i}") for i in range(4)]
    await size_backfill._run(_Tracking({}), games, "/x")

    assert peak["gog"] == 1, "gogdl must never run concurrently with itself"
    assert peak["epic"] == 1
    assert len(cache.data) == 8


@pytest.mark.asyncio
async def test_zero_and_none_sizes_are_not_cached(monkeypatch) -> None:
    """Caching 0 would make a real size unreachable forever."""
    cache = _Cache()
    reg = _Registry({"epic:a": 0})
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)

    await size_backfill._run(reg, [_game("epic", "a"), _game("epic", "b")], "/x")

    assert cache.puts == []


@pytest.mark.asyncio
async def test_one_failure_does_not_stop_the_walk(monkeypatch) -> None:
    cache = _Cache()
    calls: list[str] = []

    class _Flaky(_Registry):
        def get_store(self, store: str) -> Any:
            async def get_game_size(gid: str) -> int:
                calls.append(gid)
                if gid == "bad":
                    raise RuntimeError("store exploded")
                return 42
            return SimpleNamespace(get_game_size=get_game_size)

    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    await size_backfill._run(
        _Flaky({}), [_game("epic", "bad"), _game("epic", "good")], "/x",
    )

    assert sorted(calls) == ["bad", "good"]
    assert cache.data == {"epic:good": 42}


@pytest.mark.asyncio
async def test_a_hung_store_cannot_park_a_worker(monkeypatch) -> None:
    cache = _Cache()

    class _Hanging(_Registry):
        def get_store(self, store: str) -> Any:
            async def get_game_size(_gid: str) -> int:
                await asyncio.sleep(60)
                return 1
            return SimpleNamespace(get_game_size=get_game_size)

    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    monkeypatch.setattr(size_backfill, "LOOKUP_TIMEOUT_S", 0.05)

    await asyncio.wait_for(
        size_backfill._run(_Hanging({}), [_game("epic", "a")], "/x"),
        timeout=5,
    )
    assert cache.puts == []


@pytest.mark.asyncio
async def test_adapter_without_get_game_size_is_ignored(monkeypatch) -> None:
    cache = _Cache()
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    await size_backfill._run(_Registry({}, missing=True), [_game("epic", "a")], "/x")
    assert cache.puts == []


# ── spawn guard ──────────────────────────────────────────────────────


def test_spawn_noops_without_games_or_registry() -> None:
    """Must be safe to call unconditionally at the end of every sync."""
    size_backfill.spawn(None, [_game("epic", "a")], "/x")
    size_backfill.spawn(object(), [], "/x")
    assert not size_backfill._BACKGROUND_TASKS


# ── restart resilience ───────────────────────────────────────────────


@pytest.mark.asyncio
async def test_overlapping_spawns_are_collapsed(monkeypatch) -> None:
    """Boot resume and post-sync completion can coincide.

    Two concurrent walks would double the store calls and race on the same
    not-yet-cached games; whichever is already running covers the library.
    """
    cache = _Cache()
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    started: list[int] = []

    class _Slow(_Registry):
        def get_store(self, store: str) -> Any:
            async def get_game_size(_gid: str) -> int:
                started.append(1)
                await asyncio.sleep(0.05)
                return 7
            return SimpleNamespace(get_game_size=get_game_size)

    games = [_game("epic", "a")]
    size_backfill.spawn(_Slow({}), games, "/x")
    size_backfill.spawn(_Slow({}), games, "/x")  # must be ignored
    assert len(size_backfill._BACKGROUND_TASKS) == 1

    await asyncio.gather(*list(size_backfill._BACKGROUND_TASKS))
    assert started == [1], "the game must be looked up exactly once"


@pytest.mark.asyncio
async def test_progress_survives_an_interrupted_walk(monkeypatch) -> None:
    """A restart mid-walk must not lose what already resolved.

    Sizes are written through per game, so an interrupted pass leaves the
    finished ones on disk and a later pass only picks up the remainder —
    which is what makes the warm-up eventually-complete across the Steam
    restart users do right after a sync.
    """
    cache = _Cache()
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)
    reg = _Registry({"epic:a": 111, "epic:b": 222})

    # First pass: only 'a' is walked, then the "process dies".
    await size_backfill._run(reg, [_game("epic", "a")], "/x")
    assert cache.data == {"epic:a": 111}

    # Second pass over the FULL library re-walks only the missing one.
    reg.calls.clear()
    await size_backfill._run(reg, [_game("epic", "a"), _game("epic", "b")], "/x")
    assert reg.calls == ["epic:b"]
    assert cache.data == {"epic:a": 111, "epic:b": 222}


@pytest.mark.asyncio
async def test_cancel_stops_an_in_flight_walk(monkeypatch) -> None:
    """A starting sync stands the walk down so it doesn't contend for the
    same store APIs as the metadata/artwork/compat phases."""
    cache = _Cache()
    monkeypatch.setattr(size_backfill, "get_size_cache", lambda _p: cache)

    class _Slow(_Registry):
        def get_store(self, store: str) -> Any:
            async def get_game_size(_gid: str) -> int:
                await asyncio.sleep(30)
                return 1
            return SimpleNamespace(get_game_size=get_game_size)

    size_backfill.spawn(_Slow({}), [_game("epic", "a")], "/x")
    assert size_backfill.is_running()

    size_backfill.cancel()
    await asyncio.gather(*list(size_backfill._BACKGROUND_TASKS),
                         return_exceptions=True)
    assert not size_backfill.is_running()


def test_cancel_is_safe_when_nothing_is_running() -> None:
    size_backfill.cancel()
    assert not size_backfill.is_running()
