"""services/launcher/error_toasts.py — Post-failure user reporting.

2 functions handling the aftermath of a ``LauncherError`` raised
during launch. ``emit_launcher_error_toast`` renders the UI
toast; ``handle_launcher_error`` classifies the error (record
in circuit breaker unless it's a user cancel) and fires the toast.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.core.types import Result

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext

    from .service import LauncherService

logger = logging.getLogger(__name__)


async def emit_launcher_error_toast(
    svc: LauncherService,
    ctx: LaunchContext,
    err_code: str,
) -> None:
    """Emit a user-facing error toast for a LauncherError."""
    from unifideck.core.types.events import Events

    from .circuit_breaker import get_launch_id_or_none

    store = ctx.store
    game_id = ctx.game_id
    game_key = f"{store}:{game_id}"

    launch_id = await get_launch_id_or_none(svc)

    actions = []
    if launch_id:
        actions.append({
            "label": "Show logs",
            "url": f"unifideck://show-logs/{launch_id}"
        })

    try:
        await svc._bus.emit(
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
            store = ctx.store
            game_id = ctx.game_id
            game_key = f"{store}:{game_id}"

            svc._launch_history.record_failure(
                game_key,
                "launcher_error",
                err_code
            )
        except Exception as e:
            logger.debug("[ErrorToasts] Failed to record failure: %s", e)

    await emit_launcher_error_toast(svc, ctx, err_code)

    # ``Result`` has no ``message`` field — its public surface is
    # ``success``, ``error``, ``error_code``, ``store``, and
    # ``metadata``. The human-readable text belongs in ``metadata``
    # so the toast helper can pick it up while the canonical
    # ``error`` slot holds the machine code. An earlier version
    # passed ``message=err_msg`` and raised
    # ``TypeError: Result.__init__() got an unexpected keyword
    # argument 'message'`` on every classified launch failure.
    return Result(
        success=False,
        error=err_code,
        metadata={"message": err_msg},
    )
