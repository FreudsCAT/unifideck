from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events

if TYPE_CHECKING:
    from unifideck.event_bus import EventBus
logger = logging.getLogger(__name__)
async def emit_game_launched(
 bus: EventBus,
 *,
 store: str,
 game_id: str,
) -> None:
    """Emit game launched."""
    logger.info(
    "[launcher.rpc] emit GAME_LAUNCHED store=%s game=%s", store, game_id,
   )
    await bus.emit(
    Events.GAME_LAUNCHED,
    store=store,
    game_id=game_id,
   )
async def emit_game_stopped(
 bus: EventBus,
 *,
 store: str,
 game_id: str,
 exit_code: int,
 elapsed_seconds: float = 0.0,
 terminated_by_signal: bool = False,
) -> None:
    """Emit game stopped."""
    logger.info(
    "[launcher.rpc] emit GAME_STOPPED store=%s game=%s rc=%d elapsed=%.1fs signal=%s",
    store, game_id, exit_code, elapsed_seconds, terminated_by_signal,
   )
    await bus.emit(
    Events.GAME_STOPPED,
    store=store,
    game_id=game_id,
    exit_code=exit_code,
    elapsed_seconds=elapsed_seconds,
    terminated_by_signal=terminated_by_signal,
   )
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
