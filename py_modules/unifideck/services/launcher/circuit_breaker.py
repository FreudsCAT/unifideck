"""services/launcher/circuit_breaker.py — Pre-launch failure protection.

3 functions protecting a launch from being attempted when the
game has repeatedly failed recently. Circuit breaker state
lives in ``LaunchHistoryService``; this module consults it and
surfaces the refusal to the user.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from unifideck.core.types import Result

if TYPE_CHECKING:
    from unifideck.launcher.types.context import LaunchContext

    from .service import LauncherService

logger = logging.getLogger(__name__)


async def get_launch_id_or_none(svc: LauncherService) -> str | None:
    """Return the current launch correlation ID, or None."""
    if not svc._launch_history:
        return None
    try:
        lid = svc._launch_history.get_launch_id()
        if lid == "-":
            return None
        return lid  # type: ignore[no-any-return]  # config.get returns Any
    except Exception:
        return None


async def emit_circuit_open_toast(
    svc: LauncherService,
    ctx: LaunchContext,
    failure_count: int,
) -> None:
    """Emit an error toast when the circuit breaker refuses launch."""
    from unifideck.core.types.events import Events

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
            i18n_key="toasts.launcher.errorCircuitBreakerOpen",
            params={"game_key": game_key, "count": failure_count},
            actions=actions,
        )
    except Exception as e:
        logger.warning("[CircuitBreaker] Failed to emit toast: %s", e)


async def check_circuit_breaker(
    svc: LauncherService,
    ctx: LaunchContext,
) -> Result | None:
    """Return a refusal Result if the breaker is open."""
    if not svc._launch_history:
        return None

    store = ctx.store
    game_id = ctx.game_id
    game_key = f"{store}:{game_id}"

    try:
        # Assuming LaunchHistoryService has a method to check if circuit is open
        is_open, failure_count = svc._launch_history.is_circuit_open(game_key)

        if is_open:
            logger.warning("[CircuitBreaker] Circuit open for %s (failures: %d)", game_key, failure_count)
            await emit_circuit_open_toast(svc, ctx, failure_count)
            # ``Result`` has no ``message`` field — its public surface
            # is ``success``, ``error``, ``error_code``, ``store``,
            # ``metadata``. Same fix as ``error_toasts.py``: route
            # the human-readable text through ``metadata`` so the
            # toast helper can pick it up while the canonical
            # ``error`` slot holds the machine code. The earlier
            # ``message=`` form raised
            # ``TypeError: Result.__init__() got an unexpected
            # keyword argument 'message'`` every time the circuit
            # breaker engaged, swallowing the actual "circuit open"
            # signal under a TypeError noise.
            return Result(
                success=False,
                error="circuit_open",
                metadata={
                    "message": (
                        f"Launch refused. Game failed "
                        f"{failure_count} times recently."
                    ),
                },
            )

    except Exception as e:
        logger.debug("[CircuitBreaker] Failed to check circuit state: %s", e)

    return None
