"""Install-launch signals for the wrapper stores.

py_modules/unifideck/services/download/wrapper_signals.py

Wrapper stores (Ubisoft Connect, Battle.net, EA App next) all need the same
thing: **the backend must not spawn the vendor client itself.** In Gaming
Mode a bare subprocess has no gamescope session, so its window never
appears. The frontend has to ``RunGame`` a shortcut instead, and these
events are how it is asked to.

One emitter, driven by a per-store table. Adding a store is a row, not a
function.

There is exactly one call *shape*. Every wrapper store's ``install_game``
blocks for the whole vendor-client install and fires ``on_ready`` from inside,
once the prefix is bootstrapped — that is the wrapper-store contract, stated in
``stores/shared/wrapper_install``. This module used to carry a
``_ON_READY_STORES`` row recording that Battle.net signalled *after*
``install_game`` returned instead, and that was not a variation worth
supporting: it was the bug. Returning early is what marked a game installed
before a byte had downloaded.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.core.types import InstallResult
    from unifideck.stores.shared.store_base import StoreBase

    from .models import DownloadItem

logger = logging.getLogger(__name__)

# store id -> the Events member emitted to ask the frontend to launch it.
_LAUNCH_EVENTS: dict[str, str] = {
    "ubisoft": "UBISOFT_INSTALL_LAUNCH_REQUESTED",
    "battlenet": "BATTLENET_INSTALL_LAUNCH_REQUESTED",
}


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

    The installer invokes this once the per-game prefix is bootstrapped and
    before it starts watching, which is the only moment that is both "the
    client has somewhere to open into" and "we are ready to see what it does".
    """

    async def _signal() -> None:
        await signal_install_launch(bus, item.store, item.game_id)

    return _signal


async def dispatch_wrapper_install(
    bus: Any, item: DownloadItem, store: StoreBase, progress_cb: Any,
) -> InstallResult:
    """Install or update through a vendor client running inside the prefix.

    One shape for every wrapper store: the call blocks for the whole
    vendor-client install and fires ``on_ready`` from inside, once the prefix
    is ready for the client to open into. There used to be a second shape —
    signal after the call returns — and it was the bug, not a variation: the
    call returned at prefix-creation time, so the game was marked installed
    before anything had downloaded.

    ``install_path`` is not optional plumbing for these stores: their games
    live *inside* the prefix, so it is what decides which disk the game lands
    on and which volume's free space the vendor client reports. Battle.net
    shipped without it being passed and its installer refused an 83 GB download
    quoting the internal drive while the SD card the user picked had 164 GB
    free. An *update* passes no path — it reuses the prefix the game is already
    installed in, and re-placing it would delete that install.
    """
    kwargs: dict[str, Any] = {
        "progress_cb": progress_cb,
        "on_ready": make_launch_signal(bus, item),
    }
    if item.is_update:
        return await store.update_game(item.game_id, **kwargs)
    kwargs["install_path"] = item.install_path or None
    return await store.install_game(item.game_id, **kwargs)
