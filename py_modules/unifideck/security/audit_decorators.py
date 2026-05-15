from __future__ import annotations

import contextlib
import functools
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def audit_auth_flow(
    store: str,
    method: str = "oauth",
) -> Callable[..., Any]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            bus = getattr(self, "_bus", None)
            started_at = time.time()

            await _emit_audit(
                bus, "SECURITY_AUTH_FLOW_STARTED",
                store=store, method=method,
            )

            try:
                result = await func(self, *args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.time() - started_at) * 1000)
                await _emit_audit(
                    bus, "SECURITY_AUTH_FLOW_FAILED",
                    store=store, method=method,
                    reason=type(exc).__name__,
                    duration_ms=duration_ms,
                )
                raise

            duration_ms = int((time.time() - started_at) * 1000)
            await _emit_audit(
                bus, "SECURITY_AUTH_FLOW_COMPLETED",
                store=store, method=method,
                duration_ms=duration_ms,
            )

            return result
        return wrapper
    return decorator


def audit_token_op(
    operation: str,
    store: str,
) -> Callable[..., Any]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            bus = getattr(self, "_bus", None)
            result = await func(self, *args, **kwargs)

            if operation == "migrate" and isinstance(result, str):
                await _maybe_emit_migration(bus, self, store, result)

            return result
        return wrapper
    return decorator


async def _emit_audit(bus: Any, event_name: str, **kwargs: Any) -> None:
    """Emit a SECURITY_* audit event on ``bus`` if one is set.

    ``EventBus.emit`` is ``async``; an earlier version of this
    helper was sync and returned the coroutine to the caller's
    GC, so none of the audit events actually fired. Every caller
    here lives inside an ``async def`` wrapper, so making this
    helper ``async`` and ``await``-ing it at each site is the
    minimal correctness fix.
    """
    if bus is None:
        return

    try:
        from unifideck.core.types.events import Events
        event = getattr(Events, event_name)
        await bus.emit(event, **kwargs)
    except Exception as e:
        logger.debug(
            "[audit_decorators] failed to emit %s: %s",
            event_name, e,
        )


async def _maybe_emit_migration(
    bus: Any, instance: Any, store: str, result_path: str,
) -> None:
    """Emit the migration audit event if the instance flagged it.

    Async because :func:`_emit_audit` is async (see its docstring
    for why). The pair must stay coherent — making one async
    without the other re-introduces the dropped-coroutine bug.
    """
    if bus is None:
        return

    flag = getattr(instance, "_migration_occurred", False)
    if not flag:
        return

    await _emit_audit(
        bus, "SECURITY_TOKEN_FILE_MIGRATED",
        store=store, new_path=result_path,
    )

    with contextlib.suppress(AttributeError):
        instance._migration_occurred = False
