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
    """Auth-related bus subscriptions for the audit log."""

    _audit: AuditLog

    @subscribe(Events.SECURITY_AUTH_FLOW_STARTED)
    async def _on_auth_started(self, **kwargs: Any) -> None:
        """Record an auth-flow start to the audit log.

        Captures the moment the user clicks "sign in" — paired
        with the ``_completed`` / ``_failed`` event to compute
        per-flow durations and success rates in diagnostics.
        """
        self._audit.record("SECURITY_AUTH_FLOW_STARTED", kwargs)

    @subscribe(Events.SECURITY_AUTH_FLOW_COMPLETED)
    async def _on_auth_completed(self, **kwargs: Any) -> None:
        """Record a successful auth-flow completion.

        Logged at the audit level only — no console log, because
        successful auths are the common case and shouldn't
        clutter the plugin's normal logs.
        """
        self._audit.record("SECURITY_AUTH_FLOW_COMPLETED", kwargs)

    @subscribe(Events.SECURITY_AUTH_FLOW_FAILED)
    async def _on_auth_failed(self, **kwargs: Any) -> None:
        """Record an auth-flow failure and emit a WARN-level log.

        The console log mirrors the audit entry so a developer
        watching plugin logs sees auth failures in real time
        without needing to open the QAM. The reason field is
        extracted for the log line specifically — full payload
        goes to the audit log.
        """
        self._audit.record("SECURITY_AUTH_FLOW_FAILED", kwargs)
        reason = kwargs.get("reason", "unknown")
        logger.warning(
            "[SecurityService] auth flow failed: %s",
            reason,
        )

    @subscribe(Events.SECURITY_EXTERNAL_AUTH_CHECK_FAILED)
    async def _on_external_auth_check_failed(self, **kwargs: Any) -> None:
        """Record a failed sanity-check on an external auth artefact.

        Different from a plain auth failure: this fires when an
        existing token/cookie/credential fails a post-hoc
        validation (e.g. the credential format is wrong, or
        signature verification fails). Logged at WARN with both
        the store and the reason in the log line.
        """
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
