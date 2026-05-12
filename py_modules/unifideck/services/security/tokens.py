"""Token audit mixin — log token operations to the audit log.

OP-19g | py_modules/unifideck/services/security/tokens.py

``TokenAuditMixin`` provides the helpers to log token-related
events (created, refreshed, expired, revoked, leaked) into the
audit log. Used by every store's token manager.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from ...core.types.events import Events
from ...event_bus.event_bus_devex import subscribe

if TYPE_CHECKING:
    from .audit_log import AuditLog
    from .bruteforce import BruteForceDetector
logger = logging.getLogger(__name__)


class TokenAuditMixin:
    """Token audit mixin."""

    _audit: AuditLog
    _bf: BruteForceDetector

    @subscribe(Events.SECURITY_TOKEN_ENCRYPTED)
    async def _on_token_encrypted(self, **kwargs: Any) -> None:
        """On token encrypted."""
        self._audit.record("SECURITY_TOKEN_ENCRYPTED", kwargs)

    @subscribe(Events.SECURITY_TOKEN_DECRYPTED)
    async def _on_token_decrypted(self, **kwargs: Any) -> None:
        """On token decrypted."""
        self._audit.record("SECURITY_TOKEN_DECRYPTED", kwargs)

    @subscribe(Events.SECURITY_DECRYPT_FAILED)
    async def _on_decrypt_failed(self, **kwargs: Any) -> None:
        """On decrypt failed."""
        self._audit.record("SECURITY_DECRYPT_FAILED", kwargs)
        reason = kwargs.get("reason", "unknown")
        logger.warning(
            "[SecurityService] decrypt failure: %s",
            reason,
        )
        self._bf.check()

    @subscribe(Events.SECURITY_TOKEN_FILE_MIGRATED)
    async def _on_token_file_migrated(self, **kwargs: Any) -> None:
        """On token file migrated."""
        self._audit.record("SECURITY_TOKEN_FILE_MIGRATED", kwargs)

    @subscribe(Events.SECURITY_LEGACY_PLAINTEXT_DETECTED)
    async def _on_legacy_plaintext(self, **kwargs: Any) -> None:
        """On legacy plaintext."""
        self._audit.record("SECURITY_LEGACY_PLAINTEXT_DETECTED", kwargs)
