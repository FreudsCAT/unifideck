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
    """Token-lifecycle bus subscriptions for the audit log.

    Notably, decrypt failures also feed the brute-force detector
    — repeated decrypt failures can indicate an attacker trying
    to brute-force the encryption key.
    """

    _audit: AuditLog
    _bf: BruteForceDetector

    @subscribe(Events.SECURITY_TOKEN_ENCRYPTED)
    async def _on_token_encrypted(self, **kwargs: Any) -> None:
        """Record that a token was successfully encrypted to disk.

        Emitted by the token store after writing the ciphertext.
        Audit-log only — no operational warning needed.
        """
        self._audit.record("SECURITY_TOKEN_ENCRYPTED", kwargs)

    @subscribe(Events.SECURITY_TOKEN_DECRYPTED)
    async def _on_token_decrypted(self, **kwargs: Any) -> None:
        """Record that a token was successfully decrypted on read.

        Paired with the encrypted event; together they form the
        full lifecycle trace of a credential.
        """
        self._audit.record("SECURITY_TOKEN_DECRYPTED", kwargs)

    @subscribe(Events.SECURITY_DECRYPT_FAILED)
    async def _on_decrypt_failed(self, **kwargs: Any) -> None:
        """Record a token decrypt failure and notify the brute-force detector.

        Decrypt failures are unusual in normal operation
        (correct key, intact ciphertext → success). Repeated
        failures suggest either:

        * key rotation pending (legitimate, transient);
        * encryption key change (e.g. device-reset scenario);
        * a brute-force attempt (rare on a single-user Steam
          Deck but possible if the device is stolen).

        Logged at WARN and routed to ``_bf.check`` which evaluates
        the rolling-window thresholds.
        """
        self._audit.record("SECURITY_DECRYPT_FAILED", kwargs)
        reason = kwargs.get("reason", "unknown")
        logger.warning(
            "[SecurityService] decrypt failure: %s",
            reason,
        )
        self._bf.check()

    @subscribe(Events.SECURITY_TOKEN_FILE_MIGRATED)
    async def _on_token_file_migrated(self, **kwargs: Any) -> None:
        """Record a token-file migration (path or format change).

        Fires when the token store migrates a token file (e.g.
        v1 plaintext → v2 encrypted, or moves a file to its new
        canonical location). Audit-log-only — these are
        intentional transitions, not security events.
        """
        self._audit.record("SECURITY_TOKEN_FILE_MIGRATED", kwargs)

    @subscribe(Events.SECURITY_LEGACY_PLAINTEXT_DETECTED)
    async def _on_legacy_plaintext(self, **kwargs: Any) -> None:
        """Record that an old plaintext token file was found.

        Older plugin versions stored tokens in plaintext. When
        the current version finds such a file, it migrates it to
        encrypted form and emits this event so the user (via the
        QAM audit panel) is aware the migration happened.
        """
        self._audit.record("SECURITY_LEGACY_PLAINTEXT_DETECTED", kwargs)
