"""core/net/ssl_helpers.py — Centralised SSL context builders.

# OP-08a | core/net/ssl_helpers.py | Depends: (none)

Consolidates the 4 scattered ``ssl.CERT_NONE`` occurrences across
stores. New default is ``ssl_ctx_strict`` (full validation).
Permissive path kept for legacy parity but logs a WARNING on
first use. Both are lazy-singletons — handshake params never
change at runtime.
"""
from __future__ import annotations

import logging
import ssl

logger = logging.getLogger(__name__)

_strict_ctx: ssl.SSLContext | None = None
_permissive_ctx: ssl.SSLContext | None = None
_permissive_warned: bool = False


def ssl_ctx_strict() -> ssl.SSLContext:
    """Return a shared, fully-validating SSL context (TLS 1.2+,
    hostname + CA chain verification on). Use this for every
    outbound HTTPS call unless a host-specific reason forbids.
    """
    global _strict_ctx
    if _strict_ctx is None:
        _strict_ctx = ssl.create_default_context()
        _strict_ctx.minimum_version = ssl.TLSVersion.TLSv1_2
    return _strict_ctx


def ssl_ctx_permissive(reason: str) -> ssl.SSLContext:
    """Return a shared SSL context with verification DISABLED.
    Legacy compat only — every use should be removed on sight.
    Logs ``reason`` at WARNING on first call so abuse is auditable.
    """
    global _permissive_ctx, _permissive_warned
    if not _permissive_warned:
        logger.warning("Creating permissive SSL context (CERT_NONE): %s", reason)
        _permissive_warned = True
    if _permissive_ctx is None:
        _permissive_ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT)
        _permissive_ctx.check_hostname = False
        _permissive_ctx.verify_mode = ssl.CERT_NONE
    return _permissive_ctx
