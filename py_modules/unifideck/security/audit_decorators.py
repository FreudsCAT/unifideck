from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import Any

logger = logging.getLogger(__name__)


def audit_auth_flow(
    store: str,
    method: str = "oauth",
) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            bus = getattr(self, "_bus", None)
            started_at = time.time()

            _emit_audit(
                bus, "SECURITY_AUTH_FLOW_STARTED",
                store=store, method=method,
            )

            try:
                result = await func(self, *args, **kwargs)
            except Exception as exc:
                duration_ms = int((time.time() - started_at) * 1000)
                _emit_audit(
                    bus, "SECURITY_AUTH_FLOW_FAILED",
                    store=store, method=method,
                    reason=type(exc).__name__,
                    duration_ms=duration_ms,
                )
                raise

            duration_ms = int((time.time() - started_at) * 1000)
            _emit_audit(
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
) -> Callable:
    def decorator(func: Callable) -> Callable:
        @functools.wraps(func)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            bus = getattr(self, "_bus", None)
            result = await func(self, *args, **kwargs)

            if operation == "migrate" and isinstance(result, str):
                _maybe_emit_migration(bus, self, store, result)

            return result
        return wrapper
    return decorator


def _emit_audit(bus: Any, event_name: str, **kwargs: Any) -> None:
    if bus is None:
        return

    try:
        from ..core.types.events import Events
        event = getattr(Events, event_name)
        bus.emit(event, **kwargs)
    except Exception as e:
        logger.debug(
            "[audit_decorators] failed to emit %s: %s",
            event_name, e,
        )


def _maybe_emit_migration(
    bus: Any, instance: Any, store: str, result_path: str,
) -> None:
    if bus is None:
        return

    flag = getattr(instance, "_migration_occurred", False)
    if not flag:
        return

    _emit_audit(
        bus, "SECURITY_TOKEN_FILE_MIGRATED",
        store=store, new_path=result_path,
    )

    try:
        instance._migration_occurred = False
    except AttributeError:
        pass
