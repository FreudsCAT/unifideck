"""services/launcher/error_toasts.py — Post-failure user reporting.

2 functions handling the aftermath of a ``LauncherError`` raised
during launch. ``emit_launcher_error_toast`` renders the UI
toast; ``handle_launcher_error`` classifies the error (record
in circuit breaker unless it's a user cancel) and fires the toast.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from ...core.types import Result

if TYPE_CHECKING:
    from ...launcher.types.context import LaunchContext
    from ...launcher.types.errors import LauncherError
    from .service import LauncherService

logger = logging.getLogger(__name__)


async def emit_launcher_error_toast(
    svc: LauncherService,
    ctx: LaunchContext,
    err_code: str,
) -> None:
    """Emit a user-facing error toast for a LauncherError."""
    from ...core.types.events import Events
    from .circuit_breaker import get_launch_id_or_none

    store = ctx.game.get("store", "unknown")
    game_id = ctx.game.get("game_id", "unknown")
    game_key = f"{store}:{game_id}"

    launch_id = await get_launch_id_or_none(svc)

    actions = []
    if launch_id:
        actions.append({
            "label": "Show logs",
            "url": f"unifideck://show-logs/{launch_id}"
        })

    try:
        svc._bus.emit(
            Events.TOAST_NOTIFICATION,
            severity="error",
            duration_ms=10000,
            i18n_key="toasts.launcher.launcherError",
            params={"game_key": game_key, "error_code": err_code},
            actions=actions,
        )
    except Exception as e:
        logger.warning("[ErrorToasts] Failed to emit error toast: %s", e)


async def handle_launcher_error(
    svc: LauncherService,
    ctx: LaunchContext,
    err: Exception,
) -> Result:
    """Convert a LauncherError into a failure Result."""
    err_code = getattr(err, "code", type(err).__name__)
    err_msg = str(err)
    
    is_cancel = "cancel" in err_code.lower() or "cancel" in err_msg.lower()
    
    if not is_cancel and svc._launch_history:
        try:
            # Record failure via FAILURE_KIND_LAUNCHER_ERROR
            store = ctx.game.get("store", "unknown")
            game_id = ctx.game.get("game_id", "unknown")
            game_key = f"{store}:{game_id}"
            
            svc._launch_history.record_failure(
                game_key, 
                "launcher_error", 
                err_code
            )
        except Exception as e:
            logger.debug("[ErrorToasts] Failed to record failure: %s", e)
            
    await emit_launcher_error_toast(svc, ctx, err_code)
    
    return Result(success=False, error=err_code, message=err_msg)
