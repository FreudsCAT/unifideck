"""Regression: one hanging store must not stall the whole sync.

A wedged ``get_library`` (e.g. a stuck Wine/UPC catalog parse) used to
freeze the sequential sync at "store N/N" forever — the user saw it
stuck and never got the other stores' games. ``_fetch_one`` now bounds
each store with ``PER_STORE_FETCH_TIMEOUT_SECONDS`` and reports a
``timeout`` instead of hanging.
"""
from __future__ import annotations

import asyncio
import types

import pytest

from unifideck.core import sync_run_mixin as m


class _Svc(m._SyncRunMixin):
    def __init__(self, delay: float, games: list | None = None) -> None:
        self._current_store_task = None
        self._delay = delay
        self._games = games or []

    async def _sync_one_store(self, _store: object, _is_force: bool = False):
        await asyncio.sleep(self._delay)
        return self._games, None


def _store():
    return types.SimpleNamespace(store_name="ubisoft")


@pytest.mark.asyncio
async def test_hanging_store_times_out(monkeypatch):
    monkeypatch.setattr(m, "PER_STORE_FETCH_TIMEOUT_SECONDS", 0.1)
    svc = _Svc(delay=10.0)
    games, err = await svc._fetch_one(_store())
    assert games == []
    assert err == "timeout"
    # the underlying task must be cancelled, not left running
    assert svc._current_store_task is None


@pytest.mark.asyncio
async def test_fast_store_returns_games(monkeypatch):
    monkeypatch.setattr(m, "PER_STORE_FETCH_TIMEOUT_SECONDS", 5)
    sentinel = [object()]
    svc = _Svc(delay=0.0, games=sentinel)
    games, err = await svc._fetch_one(_store())
    assert games == sentinel
    assert err is None
