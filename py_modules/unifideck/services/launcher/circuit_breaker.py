"""Launch circuit breaker — refuse repeat-failing launches.

OP-20b | py_modules/unifideck/services/launcher/circuit_breaker.py

If a game has failed N consecutive launches within a window of W
seconds, the circuit breaker opens : further launch attempts are
refused with a toast explaining the situation. This prevents an
infinite "retry on crash" loop when a game is broken on the
current Proton version.

Three module-level helpers :

* ``get_launch_id_or_none`` — extract the launch id from context;
* ``emit_circuit_open_toast`` — UI notification when blocked;
* ``check_circuit_breaker`` — the actual decision function.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING
from ...core.types import Result
from ...core.types.events import Events

if TYPE_CHECKING:
    from ...launcher.types.context import LaunchContext
    from .service import LauncherService
logger = logging.getLogger(__name__)


async def get_launch_id_or_none(svc: LauncherService) -> str | None:
    """Return the current launch correlation id, or ``None`` if unavailable.

    The correlation id is a short token attached to each launch
    attempt and used to thread together log entries across
    services. ``get_launch_id`` returns ``"-"`` when no launch
    is in progress; we normalise that to ``None`` for cleaner
    callers.

    Failures (module import error, unexpected internal state)
    are caught and surfaced as ``None`` — correlation is a
    diagnostic aid, not a requirement.

    Args:
        svc: the launcher service (unused, kept for symmetry
            with other delegators).

    Returns:
        The correlation id string, or ``None``.
    """
    try:
        from ...launcher.diagnostics.correlation import get_launch_id

        lid = get_launch_id()
        return None if lid == "-" else lid
    except Exception:
        return None


async def emit_circuit_open_toast(
    svc: LauncherService,
    ctx: LaunchContext,
    failure_count: int,
) -> None:
    """Build and emit the "circuit open" toast on the bus.

    The toast carries:

    * i18n key + parameters (game name + failure count) so the
      frontend can render in the user's locale;
    * a 10 s duration (longer than the default — important
      enough that the user has time to read);
    * an optional "show logs" action when a launch id is
      available, deep-linking into the diagnostic view for the
      blocked attempt.

    Failures during emission are logged but swallowed — losing
    a toast is unfortunate but not worth bubbling up.

    Args:
        svc: the launcher service (provides the bus).
        ctx: the refused launch context.
        failure_count: number of recent failures included in
            the toast body.
    """
    lid = await get_launch_id_or_none(svc)
    toast_payload: dict[str, object] = {
        "severity": "error",
        "i18n_key": "toasts.launcher.errorCircuitBreakerOpen",
        "i18n_params": {
            "game_key": ctx.game_key,
            "count": failure_count,
        },
        "duration_ms": 10000,
        "store": ctx.store,
        "game_id": ctx.game_id,
    }
    if lid:
        toast_payload["action"] = {
            "i18n_label_key": "toasts.actions.showLogs",
            "target_url": f"unifideck://show-logs/{lid}",
        }
    try:
        await svc._bus.emit(
            Events.LAUNCHER_STAGE,
            **toast_payload,
        )
    except Exception:
        logger.exception(
            "[LauncherService] circuit_open toast emit failed",
        )


async def check_circuit_breaker(
    svc: LauncherService,
    ctx: LaunchContext,
) -> Result | None:
    """Consult ``LaunchHistoryService`` and refuse if the circuit is open.

    Conditions for the breaker to engage (all required):

    1. ``ctx.is_launch_action`` is True — auth shortcuts and
       similar non-launch contexts bypass the breaker.
    2. A launch-history service was injected.
    3. ``is_circuit_open(game_key)`` returns True.

    On engagement, emits the circuit-open toast and returns a
    structured ``Result`` with ``error_code="circuit_open"`` so
    the RPC layer can render the "Force launch" button to let
    the user override.

    Args:
        svc: the launcher service.
        ctx: the launch context being evaluated.

    Returns:
        ``None`` when the launch may proceed, or a non-success
        ``Result`` when the breaker is open.
    """
    if (
        not ctx.is_launch_action
        or svc._launch_history is None
        or not svc._launch_history.is_circuit_open(ctx.game_key)
    ):
        return None
    recent = svc._launch_history.get_recent_failures(ctx.game_key)
    logger.warning(
        "[LauncherService] circuit OPEN for %s (%d recent failures), refusing launch",
        ctx.game_key,
        len(recent),
    )
    await emit_circuit_open_toast(svc, ctx, len(recent))
    window_s = int(svc._launch_history.window_seconds())
    return Result(
        success=False,
        error=(
            f"Launch refused: {len(recent)} recent failures "
            f"in the last {window_s}s. "
            f"Use 'Force launch' to bypass."
        ),
        error_code="circuit_open",
        store=ctx.store,
    )
