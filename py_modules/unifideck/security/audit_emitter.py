"""security/audit_emitter.py — Centralised SECURITY_* event emitters.

provides two complementary tools to instrument
the security-relevant code paths without scattering raw
bus.emit() calls everywhere:

1. **Helper functions** (emit_auth_started, emit_token_file_migrated,
   etc.) — short one-line calls used inside method bodies where the
   event is conditional or carries dynamic kwargs.

2. **Decorator** (@audit_auth_flow) — wraps store start_auth()
   methods to emit STARTED before, COMPLETED on AuthResult(success
   =True), FAILED on AuthResult(success=False) or any raised
   exception. Reads the bus from `self._bus` by convention.
   Measures wall-clock duration. Used at the method level so the
   instrumentation is purely declarative — the decorated method's
   body is unchanged.

Design rules
------------
- Every helper is best-effort: a None bus, a bus crash, or a
  handler raising must NEVER prevent the originating operation
  from completing. All emit calls are wrapped in try/except.
- Helpers do NOT log on success (the SecurityService handler
  is responsible for logging). They only log on the rare case
  of an emit failure, at debug level, to keep operator output
  clean.
- The Events enum is imported lazily inside each helper to
  avoid circular imports between security/ and core/types/.

Reference: design discussion 2026-04-13 (audit decorator pattern
to limit instrumentation volume).
"""
from __future__ import annotations

import functools
import logging
import time
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import asyncio as _asyncio_typ

    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)


# Strong references to fire-and-forget audit-emit tasks so they aren't
# GC'd before the bus delivers the event. Each task removes itself
# via the discard callback below.
_background_tasks: set[_asyncio_typ.Task[Any]] = set()


# ─── Helper functions for inline emission ──────────────────────

def _safe_emit(bus: EventBus, event_name: str, **kwargs: Any) -> None:
    """Emit an event on the bus, swallowing every failure.

    Centralises the lazy Events import + try/except so each
    public helper below stays a one-liner. Never raises.

    Fire-and-forget: ``bus.emit`` is async. We schedule it as a
    task on the running loop so callers (sync decorators) don't
    need to be async themselves. If no loop is running we skip
    the emission — an audit miss is preferable to blocking.

    Every kwarg payload is run through ``redact_for_audit``
    before reaching the bus, so even if a future caller
    accidentally passes ``access_token=...`` through one of the
    helpers, the value is replaced with ``<redacted>`` before
    any subscriber (including the AuditLog) sees it. Defence
    in depth: the helpers' docstrings already forbid passing
    secrets, this is the safety net for human error.
    """
    if bus is None:
        return  # type: ignore[unreachable]  # defensive guard on optional bus
    try:
        import asyncio

        from unifideck.core.types.events import Events

        from .redaction import redact_for_audit
        event = getattr(Events, event_name)
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            # No running loop — purely sync context (unlikely in
            # plugin, possible in tests). Drop the event.
            return
        sanitized = redact_for_audit(kwargs)
        task = loop.create_task(
            bus.emit(event, **sanitized),
            name=f"audit-emit-{event_name}",
        )
        _background_tasks.add(task)
        task.add_done_callback(_background_tasks.discard)
    except Exception as e:
        logger.debug(
            "[audit_emitter] failed to emit %s: %s",
            event_name, e,
        )


def emit_auth_started(
    bus: EventBus, store: str, method: str | None = None,
) -> None:
    """Emit SECURITY_AUTH_FLOW_STARTED for the given store."""
    kwargs = {"store": store}
    if method is not None:
        kwargs["method"] = method
    _safe_emit(bus, "SECURITY_AUTH_FLOW_STARTED", **kwargs)


def emit_auth_completed(
    bus: EventBus,
    store: str,
    duration_seconds: float | None = None,
    method: str | None = None,
) -> None:
    """Emit SECURITY_AUTH_FLOW_COMPLETED with optional duration."""
    kwargs: dict[str, Any] = {"store": store}
    if duration_seconds is not None:
        kwargs["duration_seconds"] = round(duration_seconds, 3)
    if method is not None:
        kwargs["method"] = method
    _safe_emit(bus, "SECURITY_AUTH_FLOW_COMPLETED", **kwargs)


def emit_auth_failed(
    bus: EventBus,
    store: str,
    reason: str,
    duration_seconds: float | None = None,
    method: str | None = None,
) -> None:
    """Emit SECURITY_AUTH_FLOW_FAILED with reason + optional duration.

    The reason is a short category string (e.g. "user_cancelled",
    "network_error", "TimeoutError"). It MUST NOT contain user data,
    tokens, or stack traces — those belong in regular logs, not in
    audit events that may be exposed via RPC.
    """
    kwargs: dict[str, Any] = {"store": store, "reason": reason}
    if duration_seconds is not None:
        kwargs["duration_seconds"] = round(duration_seconds, 3)
    if method is not None:
        kwargs["method"] = method
    _safe_emit(bus, "SECURITY_AUTH_FLOW_FAILED", **kwargs)


def emit_token_file_migrated(
    bus: EventBus, store: str, from_path: str, to_path: str,
) -> None:
    """Emit SECURITY_TOKEN_FILE_MIGRATED on legacy→current move.

    Used by token managers when they detect a token file at a
    deprecated path (e.g. ~/.local/share/) and successfully move
    it to the current canonical location.
    """
    _safe_emit(
        bus, "SECURITY_TOKEN_FILE_MIGRATED",
        store=store, from_path=from_path, to_path=to_path,
    )


def emit_legacy_plaintext_detected(
    bus: EventBus, store: str, path: str,
) -> None:
    """Emit SECURITY_LEGACY_PLAINTEXT_DETECTED on legacy file read.

    Informational only — it means the user is upgrading from a
    pre- install. The next save() will encrypt the file.
    """
    _safe_emit(
        bus, "SECURITY_LEGACY_PLAINTEXT_DETECTED",
        store=store, path=path,
    )


def emit_token_age_exceeded(
    bus: EventBus,
    store: str,
    age_seconds: float,
    max_age_seconds: float,
) -> None:
    """Emit SECURITY_TOKEN_AGE_EXCEEDED on rotation policy hit.

    Called by token managers when ``load()`` finds an encrypted
    payload whose ``_unifideck_encrypted_at`` metadata is older
    than the manager's ``max_token_age_seconds`` policy. The
    audit log records the event so an operator reviewing
    "why did I get logged out?" can see the exact age vs threshold
    rather than guessing at server-side revocation.

    The age values are rounded to 1-second precision since
    sub-second is meaningless when discussing policies measured
    in days.

    Args:
        store: canonical store id ("gog", "microsoft").
        age_seconds: actual age of the payload, in seconds. Must
            be a non-negative float (the helper does not clamp;
            callers should pass the value from
            ``SecureTokenStore.payload_age_seconds`` which
            already clamps to 0.0).
        max_age_seconds: the policy threshold that was crossed.
            Recorded so the audit log entry is self-contained
            and doesn't require cross-referencing config to
            interpret.

    """
    _safe_emit(
        bus, "SECURITY_TOKEN_AGE_EXCEEDED",
        store=store,
        age_seconds=round(age_seconds, 1),
        max_age_seconds=round(max_age_seconds, 1),
    )


def emit_permissions_check(
    bus: EventBus, store: str, path: str, mode: int,
) -> None:
    """Emit SECURITY_PERMISSIONS_CHECK after a token file write.

    called by token managers right after save()
    completes successfully. SecurityService's policy handler
    observes this and triggers auto-repair if mode != 0o600.

    Args:
        store: canonical store id
        path: expanded absolute path of the token file
        mode: current permission bits as returned by os.stat
            (already masked to low 12 bits by the emitter)

    """
    _safe_emit(
        bus, "SECURITY_PERMISSIONS_CHECK",
        store=store, path=path, mode=mode,
    )


def emit_external_auth_check_failed(
    bus: EventBus, store: str, reason: str, detail: str = "",
) -> None:
    """Emit SECURITY_EXTERNAL_AUTH_CHECK_FAILED on status anomaly.

    called by stores whose credentials are managed
    by external tools (legendary for Epic, nile for Amazon,
    Ubisoft Connect for Ubisoft) when their credential status
    reader hits an unexpected failure.

    IMPORTANT: callers must NOT emit this for the routine
    "user isn't logged in yet" case. That's not an anomaly,
    it's normal state. Only emit when something is genuinely
    wrong: missing CLI binary, corrupt credentials file,
    unreadable prefix path, etc.

    Args:
        store: canonical store id ("epic", "amazon", "ubisoft")
        reason: short category string. Suggested values:
            "cli_not_found" — the external CLI binary is missing
            "parse_error" — credentials file exists but corrupt
            "prefix_missing" — Wine prefix path not accessible
            "upc_not_found" — Ubisoft Connect exe missing from
                              prefix (install incomplete)
        detail: optional short free-form context (≤ 64 chars).
            Never contains file contents or user data.

    """
    kwargs = {"store": store, "reason": reason}
    if detail:
        kwargs["detail"] = str(detail)[:64]
    _safe_emit(
        bus, "SECURITY_EXTERNAL_AUTH_CHECK_FAILED", **kwargs,
    )


# ─── Decorator for auth flow instrumentation ───────────────────

def audit_auth_flow(store: str, method: str = "oauth") -> Callable[..., Any]:
    """Decorator wrapping a store's async start_auth() method.

    Emits the full SECURITY_AUTH_FLOW_* lifecycle around the
    method call without modifying its body. Conventions assumed:

    - The decorated method is a coroutine on a class instance
    - The instance has `self._bus` (may be None, that's fine)
    - The method returns an object with a `success: bool` field
      (typically AuthResult). Anything else is treated as failure.
    - If the method raises, the exception is re-raised after
      emitting SECURITY_AUTH_FLOW_FAILED with the exception type
      as the reason.

    Args:
        store: canonical store id ("gog", "epic", etc.)
        method: short label for the auth protocol used by this
            store ("oauth_browser", "oauth_cli", "wine_installer").

    Usage:
        class GOGBrowserAuth:
            @audit_auth_flow(store="gog", method="oauth_browser")
            async def start_auth(self) -> AuthResult:
                # ... existing code unchanged ...
                return result

    Failure to emit is silent (best-effort). Duration is measured
    via time.monotonic to be immune to wall-clock adjustments.

    """
    def decorator(target: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(target)
        async def wrapper(self: Any, *args: Any, **kwargs: Any) -> Any:
            bus = getattr(self, "_bus", None)
            if bus is None:
                # Silent best-effort: no bus attached, nothing to audit.
                return await target(self, *args, **kwargs)
            emit_auth_started(bus, store, method=method)
            t0 = time.monotonic()
            try:
                result = await target(self, *args, **kwargs)
            except Exception as e:
                _emit_flow_outcome(
                    bus, store, method, False,
                    time.monotonic() - t0,
                    type(e).__name__,
                )
                raise
            _emit_flow_outcome(
                bus, store, method,
                bool(getattr(result, "success", False)),
                time.monotonic() - t0,
                _extract_failure_reason(result),
            )
            return result
        return wrapper
    return decorator


def _emit_flow_outcome(
    bus: EventBus,
    store: str,
    method: str,
    success: bool,
    duration: float,
    failure_reason: str,
) -> None:
    """Emit COMPLETED on success, FAILED otherwise.

    Extracted from audit_auth_flow to keep the decorator wrapper
    small enough to fit the Phase 1 function size budget.
    """
    if success:
        emit_auth_completed(bus, store, duration, method=method)
    else:
        emit_auth_failed(
            bus, store, failure_reason, duration, method=method,
        )


def _extract_failure_reason(result: Any) -> str:
    """Pull a short reason string from an AuthResult-like object.

    Looks for common attribute names used across stores
    (error, error_code, reason, message). Falls back to
    "unknown" if none are present, ensuring the audit event
    always has a non-empty reason field.
    """
    for attr in ("error", "error_code", "reason", "message"):
        val = getattr(result, attr, None)
        if val:
            return str(val)[:64] # cap length to avoid log bloat
    return "unknown"
