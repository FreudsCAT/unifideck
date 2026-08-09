"""GOG's bulk update scan runs concurrently, and keeps a stable order.

GOG has no bulk "what's out of date" endpoint, so ``check_for_updates``
compares a local build id against ``content-system.gog.com`` **per
installed game**. That used to be a plain sequential loop, making the
scan's wall-clock ``installed_games x round_trip`` — with a 10 s timeout
each, a large library on a bad connection took minutes, and all of it sat
between the user and the Update button.

The loop is now bounded-concurrent. These tests pin the two properties
that matter: it genuinely overlaps, and the returned ids still follow the
installed order regardless of which request finishes first (the caller
compares this list by membership, but an unstable order makes the sweep's
change-detection announce phantom transitions).
"""
from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest

from unifideck.stores.gog import updates as gog_updates
from unifideck.stores.gog.updates import GOGUpdatesChecker


def _checker(installed: list[str]) -> GOGUpdatesChecker:
    """A checker whose per-game check is stubbed by each test."""
    return GOGUpdatesChecker(
        config=None,  # type: ignore[arg-type]  # unused once the check is stubbed
        tokens=None,  # type: ignore[arg-type]
        gogdl_bin="/nonexistent/gogdl",
        get_installed_ids=lambda: list(installed),
        resolve_install_info=lambda _gid: None,
    )


@pytest.mark.asyncio
async def test_empty_library_makes_no_requests() -> None:
    checker = _checker([])
    checker.check_for_game_update = AsyncMock()  # type: ignore[method-assign]

    assert await checker.check_for_updates() == []
    checker.check_for_game_update.assert_not_awaited()


@pytest.mark.asyncio
async def test_result_order_follows_installed_order() -> None:
    """Slowest-first must not reorder the output."""
    installed = ["a", "b", "c", "d"]
    delays = {"a": 0.03, "b": 0.0, "c": 0.02, "d": 0.0}
    updatable = {"a", "c", "d"}

    async def check(game_id: str) -> bool:
        await asyncio.sleep(delays[game_id])
        return game_id in updatable

    checker = _checker(installed)
    checker.check_for_game_update = check  # type: ignore[method-assign,assignment]

    assert await checker.check_for_updates() == ["a", "c", "d"]


@pytest.mark.asyncio
async def test_checks_overlap_rather_than_queue() -> None:
    """The regression this replaced: N sequential round-trips."""
    live = 0
    peak = 0

    async def check(_game_id: str) -> bool:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return False

    checker = _checker([str(i) for i in range(12)])
    checker.check_for_game_update = check  # type: ignore[method-assign,assignment]

    await checker.check_for_updates()

    assert peak > 1


@pytest.mark.asyncio
async def test_concurrency_is_bounded() -> None:
    """Unbounded would burst 100+ requests at content-system.gog.com."""
    live = 0
    peak = 0

    async def check(_game_id: str) -> bool:
        nonlocal live, peak
        live += 1
        peak = max(peak, live)
        await asyncio.sleep(0.01)
        live -= 1
        return False

    checker = _checker([str(i) for i in range(40)])
    checker.check_for_game_update = check  # type: ignore[method-assign,assignment]

    await checker.check_for_updates()

    assert peak <= gog_updates._UPDATE_CHECK_CONCURRENCY


@pytest.mark.asyncio
async def test_one_raising_game_does_not_fail_the_scan() -> None:
    """A single bad id must not hide every other pending update."""

    async def check(game_id: str) -> Any:
        if game_id == "boom":
            raise RuntimeError("content-system said no")
        return game_id == "good"

    checker = _checker(["boom", "good", "quiet"])
    checker.check_for_game_update = check  # type: ignore[method-assign,assignment]

    assert await checker.check_for_updates() == ["good"]


@pytest.mark.asyncio
async def test_none_means_unknown_not_updatable() -> None:
    """``check_for_game_update`` returns None when it can't tell."""
    checker = _checker(["a", "b"])
    checker.check_for_game_update = AsyncMock(return_value=None)  # type: ignore[method-assign]

    assert await checker.check_for_updates() == []
