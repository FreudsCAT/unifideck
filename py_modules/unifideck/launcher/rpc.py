from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from ..core.types import Events
if TYPE_CHECKING:
    from ..event_bus import EventBus
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
) -> None:
    """Emit stage."""
    logger.debug(
    "[launcher.rpc] stage: key=%s game=%s prio=%s",
    i18n_key, game_title, priority,
   )
    await bus.emit(
    Events.LAUNCHER_STAGE,
    i18n_key=i18n_key,
    game_title=game_title,
    priority=priority,
   )