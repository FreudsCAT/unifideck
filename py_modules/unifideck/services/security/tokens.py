"""services.security.mixins.tokens — Token lifecycle event handlers.

Five @subscribe handlers that observe the token manager's
lifecycle events:

  - SECURITY_TOKEN_ENCRYPTED         : successful save
  - SECURITY_TOKEN_DECRYPTED         : successful load
  - SECURITY_DECRYPT_FAILED          : decryption failure (feeds
                                       the brute-force detector)
  - SECURITY_TOKEN_FILE_MIGRATED     : legacy path → current path
  - SECURITY_LEGACY_PLAINTEXT_DETECTED : unencrypted token found
                                         on disk (auto-upgrade)

All handlers are best-effort: they record the event in the
audit log and, for DECRYPT_FAILED, feed the brute-force
detector. They never raise.

Mixed into ``SecurityService`` via multiple inheritance so the
@subscribe decorators are picked up by ``auto_wire`` at service
construction time.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types.events import Events
from unifideck.event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from .audit_log import AuditLog
    from .bruteforce import BruteForceDetector

logger = logging.getLogger(__name__)


class TokenAuditMixin:
    """Record + react to the 5 SECURITY_TOKEN_* / DECRYPT events.

    Expects the host class (``SecurityService``) to provide:

      - ``self._audit``: ``AuditLog`` instance
      - ``self._bf``: ``BruteForceDetector`` instance
    """

    # These attributes are provided by SecurityService; declaring
    # them here keeps mypy happy about the mixin body.
    _audit: AuditLog
    _bf: BruteForceDetector

    @subscribe(Events.SECURITY_TOKEN_ENCRYPTED)
    async def _on_token_encrypted(self, **kwargs: Any) -> None:
        """Record a successful encryption operation."""
        self._audit.record("SECURITY_TOKEN_ENCRYPTED", kwargs)

    @subscribe(Events.SECURITY_TOKEN_DECRYPTED)
    async def _on_token_decrypted(self, **kwargs: Any) -> None:
        """Record a successful decryption operation."""
        self._audit.record("SECURITY_TOKEN_DECRYPTED", kwargs)

    @subscribe(Events.SECURITY_DECRYPT_FAILED)
    async def _on_decrypt_failed(self, **kwargs: Any) -> None:
        """Record a decrypt failure and feed the brute-force detector."""
        self._audit.record("SECURITY_DECRYPT_FAILED", kwargs)
        reason = kwargs.get("reason", "unknown")
        logger.warning(
            "[SecurityService] decrypt failure: %s", reason,
        )
        self._bf.check()

    @subscribe(Events.SECURITY_TOKEN_FILE_MIGRATED)
    async def _on_token_file_migrated(self, **kwargs: Any) -> None:
        """Record a successful legacy -> current path migration."""
        self._audit.record("SECURITY_TOKEN_FILE_MIGRATED", kwargs)

    @subscribe(Events.SECURITY_LEGACY_PLAINTEXT_DETECTED)
    async def _on_legacy_plaintext(self, **kwargs: Any) -> None:
        """Record reading a legacy unencrypted token file."""
        self._audit.record("SECURITY_LEGACY_PLAINTEXT_DETECTED", kwargs)

    @subscribe(Events.SECURITY_TOKEN_AGE_EXCEEDED)
    async def _on_token_age_exceeded(self, **kwargs: Any) -> None:
        """Record a forced re-auth triggered by the rotation policy.

        Distinct from a normal expiry-on-the-server because we
        actively rejected a usable refresh token for hygiene.
        Operators reviewing "user got logged out" can compare
        the audit timestamp against the recorded ``age_seconds``
        and ``max_age_seconds`` to confirm the cause without
        cross-referencing config or store-side logs.
        """
        self._audit.record("SECURITY_TOKEN_AGE_EXCEEDED", kwargs)
        logger.info(
            "[SecurityService] token age exceeded (store=%s, "
            "age=%.0fs, max=%.0fs)",
            kwargs.get("store", "?"),
            float(kwargs.get("age_seconds", 0)),
            float(kwargs.get("max_age_seconds", 0)),
        )
