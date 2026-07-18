"""Regression: SYNC_PROGRESS must report the *current* run's fetch
progress, not a stale prior run's totals.

``_emit_progress`` used to compute ``total_games``/``synced_games`` from
``self._all_games`` — the previous completed run's full library, which
``_finalize_sync`` only overwrites at the very end of the *next* run. A
sync that had only fetched a couple of stores so far therefore reported
the old run's entire game count as already "synced" (e.g. a library of
1197 games from a prior sync, with the new run only 1 store and a
handful of games in). It now sums ``libraries`` — the dict the caller's
per-store loop builds up as it goes — so the reported total reflects
only what this run has actually fetched so far.
"""
from __future__ import annotations

import asyncio

import pytest

from unifideck.core import sync_run_mixin as m
from unifideck.core.sync_progress import SyncProgress
from unifideck.core.types import Events, Game
from unifideck.event_bus import EventBus


def _games(store: str, count: int) -> list[Game]:
    return [
        Game(app_id=0, store=store, store_game_id=f"{store}-{i}", title=f"{store} {i}")
        for i in range(count)
    ]


class _Svc(m._SyncRunMixin):
    def __init__(self, bus: EventBus, all_games: dict[str, list[Game]]) -> None:
        self._bus = bus
        self._progress = SyncProgress()
        # Stale data from a prior, already-completed sync run — must
        # NOT leak into this run's progress totals.
        self._all_games = all_games


@pytest.mark.asyncio
async def test_progress_reflects_current_run_not_stale_all_games():
    bus = EventBus()
    # Prior run: a large library across several stores (1197 games
    # total), already sitting in `_all_games` from before this new
    # sync started.
    stale_all_games = {
        "epic": _games("epic", 400),
        "gog": _games("gog", 397),
        "ubisoft": _games("ubisoft", 400),
    }
    assert sum(len(g) for g in stale_all_games.values()) == 1197
    svc = _Svc(bus, stale_all_games)

    captured: list[dict[str, object]] = []

    async def _capture(**payload: object) -> None:
        captured.append(payload)

    bus.on(Events.SYNC_PROGRESS, _capture)

    # New run in progress: only one store fetched so far, with a
    # small number of games — this is what `libraries` looks like
    # mid-loop in `_run_sync`, well before `_finalize_sync` replaces
    # `_all_games`.
    current_run_libraries: dict[str, list[Game]] = {"amazon": _games("amazon", 3)}

    await svc._emit_progress("gog", 1, 4, current_run_libraries)

    assert len(captured) == 1
    payload = captured[0]
    assert payload["total_games"] == 3
    assert payload["synced_games"] == 3
    assert payload["total_games"] != 1197


@pytest.mark.asyncio
async def test_progress_grows_as_libraries_accumulates_across_stores():
    """Each `_emit_progress` call reflects the running total at that
    point in the per-store loop, not a fixed snapshot."""
    bus = EventBus()
    svc = _Svc(bus, all_games={"epic": _games("epic", 1197)})

    captured: list[dict[str, object]] = []

    async def _capture(**payload: object) -> None:
        captured.append(payload)

    bus.on(Events.SYNC_PROGRESS, _capture)

    libraries: dict[str, list[Game]] = {}
    await svc._emit_progress("gog", 0, 2, libraries)
    assert captured[-1]["total_games"] == 0

    libraries["gog"] = _games("gog", 5)
    await svc._emit_progress("ubisoft", 1, 2, libraries)
    assert captured[-1]["total_games"] == 5

    libraries["ubisoft"] = _games("ubisoft", 2)
    # Simulates the emit right before the next store's fetch begins —
    # still just this run's 7 games, never the stale 1197.
    await svc._emit_progress("amazon", 2, 3, libraries)
    assert captured[-1]["total_games"] == 7


@pytest.mark.asyncio
async def test_emit_progress_updates_phase_tracker_current_game():
    """Sanity: the phase-tracker side effect (`start_store_sync`) still
    fires alongside the event, keyed off the store name passed in."""
    bus = EventBus()
    svc = _Svc(bus, all_games={})
    await svc._emit_progress("epic", 2, 5, {"epic": _games("epic", 10)})
    assert svc._progress.status == "syncing"
    assert svc._progress.current_game["values"]["store"] == "epic"
    assert svc._progress.current_game["values"]["current"] == 3
    assert svc._progress.current_game["values"]["total"] == 5
