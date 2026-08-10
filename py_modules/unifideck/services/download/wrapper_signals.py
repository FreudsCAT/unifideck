"""Install-launch signals for the wrapper stores.

py_modules/unifideck/services/download/wrapper_signals.py

Wrapper stores (Ubisoft Connect, Battle.net, EA App next) all need the same
thing: **the backend must not spawn the vendor client itself.** In Gaming
Mode a bare subprocess has no gamescope session, so its window never
appears. The frontend has to ``RunGame`` a shortcut instead, and these
events are how it is asked to.

One emitter, driven by a per-store table. Adding a store is a row, not a
function.

The two call *shapes* still differ and that is deliberate: Ubisoft's signal
is an ``on_ready`` callback threaded into ``install_game`` and fired once
the installer has bootstrapped the prefix, while Battle.net's is emitted by
the worker after ``install_game`` returns. ``make_launch_signal`` exists to
adapt the shared emitter to the callback shape, and :func:`takes_on_ready`
lets the worker pick between them from this table rather than by branching
on a store name — the difference is a row, like everything else here.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import DownloadItem

logger = logging.getLogger(__name__)

# store id -> the Events member emitted to ask the frontend to launch it.
_LAUNCH_EVENTS: dict[str, str] = {
    "ubisoft": "UBISOFT_INSTALL_LAUNCH_REQUESTED",
    "battlenet": "BATTLENET_INSTALL_LAUNCH_REQUESTED",
}

# Stores whose ``install_game`` accepts an ``on_ready`` callback. Ubisoft's
# installer blocks for the whole manual UI install, so it must signal from
# inside; Battle.net's returns as soon as the prefix is placed, so the
# worker signals after it.
_ON_READY_STORES: frozenset[str] = frozenset({"ubisoft"})


def has_launch_signal(store: str) -> bool:
    """Whether this store asks the frontend to open its client."""
    return store in _LAUNCH_EVENTS


def takes_on_ready(store: str) -> bool:
    """Whether ``install_game`` signals from inside via ``on_ready``."""
    return store in _ON_READY_STORES


async def signal_install_launch(bus: Any, store: str, game_id: str) -> None:
    """Ask the frontend to bring ``store``'s vendor client up."""
    event_name = _LAUNCH_EVENTS.get(store)
    if not bus or event_name is None:
        return
    from unifideck.core.types.events import Events

    await bus.emit(
        getattr(Events, event_name),
        store_game_id=f"{store}:{game_id}",
    )
    logger.info(
        "[DownloadWorker] requested %s client launch for %s:%s",
        store, store, game_id,
    )


def make_launch_signal(
    bus: Any, item: DownloadItem,
) -> Callable[[], Coroutine[Any, Any, None]]:
    """Adapt the emitter to the ``on_ready`` callback shape.

    Used by stores whose installer invokes a callback once the per-game
    prefix is bootstrapped, rather than returning and letting the worker
    emit.
    """

    async def _signal() -> None:
        await signal_install_launch(bus, item.store, item.game_id)

    return _signal
