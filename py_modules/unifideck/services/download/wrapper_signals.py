"""Install-launch signals for the wrapper stores.

py_modules/unifideck/services/download/wrapper_signals.py

Extracted from ``worker.py`` when Battle.net became the second wrapper
store and pushed that file past the 550-line cap.

Both signals exist for the same reason: **the backend must not spawn the
vendor client itself.** In Gaming Mode a bare subprocess has no gamescope
session, so its window never appears. The frontend has to ``RunGame`` a
shortcut instead, and these events are how it is asked to.

They stay two functions rather than one parametrised helper. The Ubisoft
signal is an ``on_ready`` callback threaded *into* ``install_game`` and
fired after the installer bootstraps the per-game prefix; the Battle.net
one is emitted by the worker once ``install_game`` returns. Merging them
would invent a shared shape neither store actually has — and would have to
be un-merged again when EA App arrives with a third.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .models import DownloadItem

logger = logging.getLogger(__name__)


async def signal_battlenet_install(bus: Any, item: DownloadItem) -> None:
    """Ask the frontend to bring the Battle.net client up.

    Emitted after the per-game prefix is ready. The user presses Install
    inside the client: ``--exec="install <FAMILY>"`` does not start a
    download, which was measured against the current client with a
    known-good family code.
    """
    if not bus:
        return
    from unifideck.core.types.events import Events

    await bus.emit(
        Events.BATTLENET_INSTALL_LAUNCH_REQUESTED,
        store_game_id=f"battlenet:{item.game_id}",
    )
    logger.info(
        "[DownloadWorker] requested Battle.net client launch for battlenet:%s",
        item.game_id,
    )


def make_ubisoft_launch_signal(
    bus: Any, item: DownloadItem,
) -> Callable[[], Coroutine[Any, Any, None]]:
    """Build the post-bootstrap callback that asks the frontend to open UPC.

    The installer bootstraps the per-game prefix and then invokes this;
    the frontend reacts by calling ``RunGame`` (which gives UPC its own
    session). The worker then keeps monitoring the prefix for the
    installed files.
    """

    async def _signal() -> None:
        if not bus:
            return
        from unifideck.core.types.events import Events

        await bus.emit(
            Events.UBISOFT_INSTALL_LAUNCH_REQUESTED,
            store_game_id=f"ubisoft:{item.game_id}",
        )
        logger.info(
            "[DownloadWorker] requested UPC launch for ubisoft:%s",
            item.game_id,
        )

    return _signal
