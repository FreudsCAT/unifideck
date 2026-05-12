"""Launch error toast helpers.

OP-20c | py_modules/unifideck/services/launcher/error_toasts.py

Two helpers that turn raw launch errors into typed UI toasts :

* ``emit_launcher_error_toast`` — emit a categorised toast on the bus;
* ``handle_launcher_error`` — classifies the raw error and routes
  it to ``emit_launcher_error_toast`` with the right severity.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from ...core.types import Result
from ...core.types.events import Events
from .circuit_breaker import get_launch_id_or_none

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
    """Emit launcher error toast."""
    lid = await get_launch_id_or_none(svc)
    toast: dict[str, object] = {
        "severity": "error",
        "i18n_key": "toasts.launcher.launcherError",
        "i18n_params": {
            "game_key": ctx.game_key,
            "error_code": err_code,
            "error_i18n_key": f"launcher.error.{err_code}",
        },
        "duration_ms": 10000,
        "store": ctx.store,
        "game_id": ctx.game_id,
    }
    if lid:
        toast["action"] = {
            "i18n_label_key": "toasts.actions.showLogs",
            "target_url": f"unifideck://show-logs/{lid}",
        }
    try:
        await svc._bus.emit(Events.LAUNCHER_STAGE, **toast)
    except Exception:
        logger.exception(
            "[LauncherService] launcher_error toast emit failed",
        )


async def handle_launcher_error(
    svc: LauncherService,
    ctx: LaunchContext,
    err: LauncherError,
) -> Result:
    """Handle launcher error."""
    logger.error(
        "[LauncherService] launch failed: %s",
        err.to_log_dict,
    )
    err_code = getattr(err, "code", None) or type(err).__name__
    is_cancel = "cancel" in err_code.lower() or "cancel" in type(err).__name__.lower()
    if ctx.is_launch_action and svc._launch_history is not None and not is_cancel:
        from ..launch_history import (
            FAILURE_KIND_LAUNCHER_ERROR,
        )

        svc._launch_history.record_failure(
            ctx.game_key,
            FAILURE_KIND_LAUNCHER_ERROR,
        )
        await emit_launcher_error_toast(svc, ctx, err_code)
    return Result(
        success=False,
        error=str(err),
        error_code=err_code,
        store=ctx.store,
    )
