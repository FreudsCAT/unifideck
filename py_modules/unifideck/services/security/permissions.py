"""services.security.mixins.permissions — Token file permissions repair.

Single @subscribe handler implementing Policy 2: watch for the
SECURITY_PERMISSIONS_CHECK event emitted by token managers
right after every ``save()`` call, verify the observed file mode
is exactly ``0o600``, and chmod it back if not.

Mixed into ``SecurityService`` via multiple inheritance so the
@subscribe decorator is picked up by ``auto_wire`` at service
construction time.
"""
from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import subscribe

from .bus_emitter import emit_security_event

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

    from .audit_log import AuditLog

logger = logging.getLogger(__name__)


class PermissionsMixin:
    """React to SECURITY_PERMISSIONS_CHECK with auto-repair.

    Expects the host class to provide:

      - ``self._audit`` : ``AuditLog`` instance
      - ``self._bus``   : ``EventBus`` instance
    """

    _audit: AuditLog
    _bus: EventBus

    @subscribe(Events.SECURITY_PERMISSIONS_CHECK)
    async def _on_permissions_check(self, **kwargs: Any) -> None:
        """Verify token file permissions and auto-repair if needed.

        Policy 2 — the token manager emits this after every save()
        with the observed mode. If it's not exactly 0o600 we chmod
        it back and emit SECURITY_PERMISSIONS_REPAIRED.
        """
        self._audit.record("SECURITY_PERMISSIONS_CHECK", kwargs)
        path = kwargs.get("path")
        mode = kwargs.get("mode")
        if not path or mode is None:
            return
        if mode == 0o600:
            return
        try:
            await asyncio.to_thread(lambda: Path(path).chmod(0o600))
        except OSError as e:
            logger.warning(
                "[SecurityService] chmod 0o600 failed on %s: %s",
                path, e,
            )
            return
        logger.warning(
            "[SecurityService] repaired permissions on %s "
            "(was %o, now 0o600)", path, mode,
        )
        emit_security_event(
            self._bus, "SECURITY_PERMISSIONS_REPAIRED",
            path=path, previous_mode=mode,
        )
        self._audit.record(
            "SECURITY_PERMISSIONS_REPAIRED",
            {"path": path, "previous_mode": mode},
        )
