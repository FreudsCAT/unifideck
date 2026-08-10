"""The download worker hands every wrapper store the picked install path.

This is the seam the Battle.net disk-space bug actually lived at. The worker
resolved the user's storage choice into ``DownloadItem.install_path``, passed
it to Ubisoft, and dropped it for Battle.net — whose games install *inside*
the prefix, so the client kept building on the internal drive and refused an
83 GB download for lack of space while the picked SD card had 164 GB free.

The dispatch is asserted by iterating ``WRAPPER_STORES`` rather than naming
the two current members. Two hand-written branches are what let them drift
apart the first time; EA App is next, and this fails loudly if it is wired
up without the path.
"""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from unifideck.core.types import InstallResult
from unifideck.launcher.wrapper_stores import WRAPPER_STORES
from unifideck.services.download.models import DownloadItem
from unifideck.services.download.worker import _WorkerMixin
from unifideck.services.download.wrapper_signals import takes_on_ready

PICKED = "/run/media/deck/microSTEAMDECK/Games"


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[Any, dict]] = []

    async def emit(self, event: Any, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


class _Store:
    """Records how the worker called it."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any]]] = []

    async def install_game(self, game_id: str, *args: Any, **kwargs: Any) -> InstallResult:
        self.calls.append((game_id, kwargs))
        on_ready = kwargs.get("on_ready")
        if on_ready is not None:
            await on_ready()
        return InstallResult(success=True, game_id=game_id, store="s")


class _Worker(_WorkerMixin):
    def __init__(self, bus: _Bus) -> None:
        self._bus = bus  # type: ignore[assignment]


def _item(store: str) -> DownloadItem:
    return DownloadItem(
        store=store, game_id="42", install_path=PICKED, title="The Outer Worlds 2",
    )


def _dispatch(store_impl: _Store, item: DownloadItem, bus: _Bus) -> InstallResult:
    return asyncio.run(
        _Worker(bus)._dispatch_install(item, store_impl, None, "key"),  # type: ignore[arg-type]
    )


@pytest.mark.parametrize("store", sorted(WRAPPER_STORES))
def test_every_wrapper_store_receives_the_picked_install_path(store: str) -> None:
    impl = _Store()

    result = _dispatch(impl, _item(store), _Bus())

    assert result.success
    (_game_id, kwargs), = impl.calls
    assert kwargs["install_path"] == PICKED


@pytest.mark.parametrize("store", sorted(WRAPPER_STORES))
def test_the_frontend_is_asked_to_open_the_client_exactly_once(store: str) -> None:
    """Either shape signals — one from inside ``install_game``, one after."""
    bus = _Bus()
    impl = _Store()

    _dispatch(impl, _item(store), bus)

    assert len(bus.events) == 1
    assert bus.events[0][1]["store_game_id"] == f"{store}:42"
    # The table, not a store-name branch, decides which shape is used.
    assert ("on_ready" in impl.calls[0][1]) is takes_on_ready(store)


@pytest.mark.parametrize("store", sorted(WRAPPER_STORES))
def test_no_pick_passes_none_rather_than_an_empty_string(store: str) -> None:
    """``resolve_prefix_target`` treats None as "use the internal default"."""
    impl = _Store()
    item = _item(store)
    item.install_path = ""

    _dispatch(impl, item, _Bus())

    assert impl.calls[0][1]["install_path"] is None


def test_a_failed_wrapper_install_does_not_ask_for_the_client() -> None:
    """A prefix that was never placed has nothing for the client to open."""

    class _Failing(_Store):
        async def install_game(self, game_id: str, *a: Any, **k: Any) -> InstallResult:
            self.calls.append((game_id, k))
            return InstallResult(success=False, game_id=game_id, store="s")

    bus = _Bus()
    store = next(s for s in sorted(WRAPPER_STORES) if not takes_on_ready(s))

    _dispatch(_Failing(), _item(store), bus)

    assert bus.events == []


def test_dispatch_runs_before_the_success_hook_rewrites_install_path() -> None:
    """``_on_install_success`` overwrites ``item.install_path`` with the prefix.

    Harmless only because it runs strictly after dispatch. If the order ever
    flips, a retry would nest the next prefix inside the previous one.
    """
    impl = _Store()
    item = _item("battlenet")
    bus = _Bus()

    _dispatch(impl, item, bus)

    assert impl.calls[0][1]["install_path"] == PICKED
    assert item.install_path == PICKED, "dispatch must not mutate the pick"
