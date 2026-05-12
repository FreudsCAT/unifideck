"""Permissions mixin — verify mode bits on sensitive paths.

OP-19h | py_modules/unifideck/services/security/permissions.py

``PermissionsMixin`` checks file permissions on paths storing
credentials (token files, key material, audit logs). Modes
expected to be ``0o600`` (owner-only read/write); anything more
permissive triggers an audit-log warning and a bus event the UI
can render as a security notification.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any
from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe
from .bus_emitter import emit_security_event

if TYPE_CHECKING:
    from ...event_bus.event_bus import EventBus
    from .audit_log import AuditLog
logger = logging.getLogger(__name__)


class PermissionsMixin:
    """Verify + auto-repair filesystem permissions on sensitive files.

    Differs from the module docstring's description: the mixin
    doesn't just warn — it actively ``chmod 0o600`` the offending
    file and emits a ``SECURITY_PERMISSIONS_REPAIRED`` event so
    the user is informed.
    """

    _audit: AuditLog
    _bus: EventBus

    @subscribe(Events.SECURITY_PERMISSIONS_CHECK)
    async def _on_permissions_check(self, **kwargs: Any) -> None:
        """Audit + repair a too-permissive sensitive file.

        Workflow:

        1. Audit the check itself.
        2. Skip if ``path`` or ``mode`` is missing (malformed
           event).
        3. Skip if the mode is already ``0o600`` (correct).
        4. Try to ``chmod 0o600`` the file. If chmod fails (read-
           only mount, foreign filesystem), log a warning and
           return — we did what we could.
        5. On successful repair, log + emit
           ``SECURITY_PERMISSIONS_REPAIRED`` + audit-record the
           repair so the user sees both the original mismatch
           and the repair in the audit log.
        """
        self._audit.record("SECURITY_PERMISSIONS_CHECK", kwargs)
        path = kwargs.get("path")
        mode = kwargs.get("mode")
        if not path or mode is None:
            return
        if mode == 0o600:
            return
        try:
            Path(path).chmod(0o600)
        except OSError as e:
            logger.warning(
                "[SecurityService] chmod 0o600 failed on %s: %s",
                path,
                e,
            )
            return
        logger.warning(
            "[SecurityService] repaired permissions on %s (was %o, now 0o600)",
            path,
            mode,
        )
        emit_security_event(
            self._bus,
            "SECURITY_PERMISSIONS_REPAIRED",
            path=path,
            previous_mode=mode,
        )
        self._audit.record(
            "SECURITY_PERMISSIONS_REPAIRED",
            {"path": path, "previous_mode": mode},
        )
