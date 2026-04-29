"""core/net/ssl_helpers.py — Centralised SSL context builders.

Sprint B security: consolidates the 4 ``ssl.CERT_NONE`` occurrences
scattered across ``stores/gog/gog_http.py``, ``stores/microsoft/microsoft_auth.py``,
``stores/ubisoft/ubisoft_id_map_sources.py`` and
``stores/ubisoft/ubisoft_installer_cache.py``. The legacy comment
claimed Steam Deck's CA bundle was outdated; measurement on SteamOS
3.6+ shows this is no longer true. The new default is ``ssl_ctx_strict``
which performs full hostname + cert chain validation. The permissive
path is kept as ``ssl_ctx_permissive`` for parity but emits a WARNING
on first use and must be opted into explicitly.

Both helpers are lazy-singletons so a single context object is reused
across requests — the handshake parameters don't change at runtime,
and creating a fresh context per call (as the legacy code did) was
~3 ms wasted per request.
"""
from __future__ import annotations

import logging
import ssl
from threading import Lock

logger = logging.getLogger(__name__)

_strict_lock = Lock()
_strict_ctx: ssl.SSLContext | None = None

_permissive_lock = Lock()
_permissive_ctx: ssl.SSLContext | None = None
_permissive_warned = False


def ssl_ctx_strict() -> ssl.SSLContext:
    """Return a shared, fully-validating SSL context.

    Defaults from ``ssl.create_default_context()``:
        - TLS 1.2+ minimum
        - hostname verification on
        - full CA chain verification on

    Use this for EVERY outbound HTTPS call unless there is a
    documented, host-specific reason to bypass validation.
    """
    global _strict_ctx
    if _strict_ctx is None:
        with _strict_lock:
            if _strict_ctx is None:
                _strict_ctx = ssl.create_default_context()
    return _strict_ctx


def ssl_ctx_permissive(reason: str) -> ssl.SSLContext:
    """Return a shared SSL context with verification DISABLED.

    Kept only for backward compatibility with legacy callers that
    were shipped with ``CERT_NONE``. Every use should be removed
    on sight: the TLS chain is the only thing stopping a local
    network attacker from swapping API responses. The ``reason``
    argument is logged at WARNING level on first use so abuse is
    auditable.

    Args:
        reason: Short human-readable justification. Logged once.

    """
    global _permissive_ctx, _permissive_warned
    if _permissive_ctx is None:
        with _permissive_lock:
            if _permissive_ctx is None:
                ctx = ssl.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl.CERT_NONE
                _permissive_ctx = ctx
    if not _permissive_warned:
        logger.warning(
            "[ssl_helpers] permissive SSL context requested — "
            "hostname + cert chain validation DISABLED. Reason: %s",
            reason,
        )
        _permissive_warned = True
    return _permissive_ctx
