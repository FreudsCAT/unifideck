"""core/net/ssl_helpers.py — Centralised SSL context builders.

# OP-08a | core/net/ssl_helpers.py | Depends: (none)

Consolidates the 4 scattered ``ssl.CERT_NONE`` occurrences across
stores. New default is ``ssl_ctx_strict`` (full validation).
Permissive path kept for legacy parity but logs a WARNING on
first use. Both are lazy-singletons — handshake params never
change at runtime.
"""
from __future__ import annotations

import ssl


def ssl_ctx_strict() -> ssl.SSLContext:
    """Return a shared, fully-validating SSL context (TLS 1.2+,
    hostname + CA chain verification on). Use this for every
    outbound HTTPS call unless a host-specific reason forbids.
    """
    raise NotImplementedError("OP-08a: implement using ssl.create_default_context()")


def ssl_ctx_permissive(reason: str) -> ssl.SSLContext:
    """Return a shared SSL context with verification DISABLED.
    Legacy compat only — every use should be removed on sight.
    Logs ``reason`` at WARNING on first call so abuse is auditable.
    """
    raise NotImplementedError("OP-08a: implement with ssl.CERT_NONE + WARNING log")
