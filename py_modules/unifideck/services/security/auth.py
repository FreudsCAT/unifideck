"""Auth audit mixin — log authentication attempts to the audit log.

OP-19i | py_modules/unifideck/services/security/auth.py

``AuthAuditMixin`` exposes the helpers to log every authentication
attempt (success or failure) to the audit log with the relevant
metadata (store, outcome, error code if any).
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from .audit_log import AuditLog
logger = logging.getLogger(__name__)


class AuthAuditMixin:
    """Auth audit mixin."""

    _audit: AuditLog

    @subscribe(Events.SECURITY_AUTH_FLOW_STARTED)
    async def _on_auth_started(self, **kwargs: Any) -> None:
        """On auth started."""
        self._audit.record("SECURITY_AUTH_FLOW_STARTED", kwargs)

    @subscribe(Events.SECURITY_AUTH_FLOW_COMPLETED)
    async def _on_auth_completed(self, **kwargs: Any) -> None:
        """On auth completed."""
        self._audit.record("SECURITY_AUTH_FLOW_COMPLETED", kwargs)

    @subscribe(Events.SECURITY_AUTH_FLOW_FAILED)
    async def _on_auth_failed(self, **kwargs: Any) -> None:
        """On auth failed."""
        self._audit.record("SECURITY_AUTH_FLOW_FAILED", kwargs)
        reason = kwargs.get("reason", "unknown")
        logger.warning(
            "[SecurityService] auth flow failed: %s",
            reason,
        )

    @subscribe(Events.SECURITY_EXTERNAL_AUTH_CHECK_FAILED)
    async def _on_external_auth_check_failed(self, **kwargs: Any) -> None:
        """On external auth check failed."""
        self._audit.record(
            "SECURITY_EXTERNAL_AUTH_CHECK_FAILED",
            kwargs,
        )
        store = kwargs.get("store", "unknown")
        reason = kwargs.get("reason", "unknown")
        logger.warning(
            "[SecurityService] external auth check failed: %s / %s",
            store,
            reason,
        )
