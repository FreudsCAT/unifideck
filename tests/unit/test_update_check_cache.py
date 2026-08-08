"""The update scan runs at most once per TTL, per store.

``check_game_update(store, game_id)`` answers a single boolean by running
the store's BULK ``check_for_updates()`` scan. The Play section fires it
on every App-Details mount, so uncached it meant a fresh Epic login (or
one HTTPS request per installed GOG game) each time a user opened a game
page — the cost was paid and thrown away even while the button was
broken and could never show the result.

These tests pin the properties that make the cache safe to rely on:
one scan per TTL, no duplicate scan under concurrency, per-store
isolation, explicit invalidation, and no caching of a crashed scan.
"""
from __future__ import annotations

import asyncio

import pytest

from unifideck.services import update_check_cache


@pytest.fixture(autouse=True)
def _clean_cache() -> None:
    """Never let one test's scan answer another's question."""
    update_check_cache.clear()


def _counting_fetch(value: list[str]) -> tuple[object, list[int]]:
    """A fetch that records how many times it actually ran."""
    calls = [0]

    async def fetch() -> list[str]:
        calls[0] += 1
        return list(value)

    return fetch, calls


@pytest.mark.asyncio
async def test_second_call_within_ttl_does_not_rescan() -> None:
    fetch, calls = _counting_fetch(["Sugar"])

    first = await update_check_cache.get_or_fetch("epic", fetch)  # type: ignore[arg-type]
    second = await update_check_cache.get_or_fetch("epic", fetch)  # type: ignore[arg-type]

    assert first == second == ["Sugar"]
    assert calls[0] == 1


@pytest.mark.asyncio
async def test_expired_ttl_rescans() -> None:
    """A zero TTL makes every entry stale on read."""
    fetch, calls = _counting_fetch(["Sugar"])

    await update_check_cache.get_or_fetch("epic", fetch, ttl=0)  # type: ignore[arg-type]
    await update_check_cache.get_or_fetch("epic", fetch, ttl=0)  # type: ignore[arg-type]

    assert calls[0] == 2


@pytest.mark.asyncio
async def test_invalidate_forces_a_rescan() -> None:
    """Queueing an update must not keep serving "update available"."""
    fetch, calls = _counting_fetch(["Sugar"])

    await update_check_cache.get_or_fetch("epic", fetch)  # type: ignore[arg-type]
    update_check_cache.invalidate("epic")
    await update_check_cache.get_or_fetch("epic", fetch)  # type: ignore[arg-type]

    assert calls[0] == 2


@pytest.mark.asyncio
async def test_stores_are_isolated() -> None:
    """A GOG scan must never answer for Epic."""
    epic_fetch, epic_calls = _counting_fetch(["Sugar"])
    gog_fetch, gog_calls = _counting_fetch(["1549126051"])

    assert await update_check_cache.get_or_fetch("epic", epic_fetch) == ["Sugar"]  # type: ignore[arg-type]
    assert await update_check_cache.get_or_fetch("gog", gog_fetch) == ["1549126051"]  # type: ignore[arg-type]
    update_check_cache.invalidate("epic")
    await update_check_cache.get_or_fetch("gog", gog_fetch)  # type: ignore[arg-type]

    assert epic_calls[0] == 1
    assert gog_calls[0] == 1


@pytest.mark.asyncio
async def test_concurrent_callers_share_one_scan() -> None:
    """Two page opens a millisecond apart must not both spawn legendary."""
    calls = [0]
    release = asyncio.Event()

    async def slow_fetch() -> list[str]:
        calls[0] += 1
        await release.wait()
        return ["Sugar"]

    waiters = [
        asyncio.create_task(update_check_cache.get_or_fetch("epic", slow_fetch))
        for _ in range(4)
    ]
    # Let all four reach the cache before the first scan finishes.
    await asyncio.sleep(0)
    release.set()

    assert await asyncio.gather(*waiters) == [["Sugar"]] * 4
    assert calls[0] == 1


@pytest.mark.asyncio
async def test_a_failed_scan_is_not_cached() -> None:
    """A crashed scan must be retried, not pinned for the whole TTL."""
    calls = [0]

    async def boom() -> list[str]:
        calls[0] += 1
        raise RuntimeError("legendary died")

    for _ in range(2):
        with pytest.raises(RuntimeError):
            await update_check_cache.get_or_fetch("epic", boom)

    assert calls[0] == 2


@pytest.mark.asyncio
async def test_callers_cannot_mutate_the_cache() -> None:
    """The returned list is a copy — a caller's ``.clear()`` is theirs alone."""
    fetch, _ = _counting_fetch(["Sugar"])

    returned = await update_check_cache.get_or_fetch("epic", fetch)  # type: ignore[arg-type]
    returned.clear()

    assert await update_check_cache.get_or_fetch("epic", fetch) == ["Sugar"]  # type: ignore[arg-type]
