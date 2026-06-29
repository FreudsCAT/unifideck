from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events

if TYPE_CHECKING:
    from unifideck.event_bus import EventBus
logger = logging.getLogger(__name__)


# NOTE: ``emit_game_launched`` / ``emit_game_stopped`` were removed — they
# were never called, and the launcher runs out-of-process (dispatcher.py)
# on an isolated bus that only forwards LAUNCHER_STAGE, so they could never
# reach the plugin's PlaytimeService. Playtime is recorded on the plugin
# bus via the frontend lifetime listener → ``notify_game_launched`` RPC.
async def emit_stage(
 bus: EventBus,
 *,
 i18n_key: str,
 game_title: str,
 priority: str = "low",
 i18n_title_key: str | None = None,
 i18n_params: dict[str, Any] | None = None,
 severity: str | None = None,
) -> None:
    """Emit a LAUNCHER_STAGE toast event.

    ``i18n_key`` is the toast *body* (or the whole message when no
    title key is given). ``i18n_title_key`` optionally supplies a
    bold title rendered above it (the toast bridge falls back to a
    single-line toast when it is absent). ``i18n_params`` fills
    placeholders like ``{{version}}`` in either key, and ``severity``
    (``info``/``warning``/``error``) selects the toast styling.
    """
    logger.debug(
    "[launcher.rpc] stage: key=%s title=%s game=%s prio=%s",
    i18n_key, i18n_title_key, game_title, priority,
   )
    payload: dict[str, Any] = {
        "i18n_key": i18n_key,
        "game_title": game_title,
        "priority": priority,
    }
    if i18n_title_key is not None:
        payload["i18n_title_key"] = i18n_title_key
    if i18n_params is not None:
        payload["i18n_params"] = i18n_params
    if severity is not None:
        payload["severity"] = severity
    await bus.emit(Events.LAUNCHER_STAGE, **payload)
