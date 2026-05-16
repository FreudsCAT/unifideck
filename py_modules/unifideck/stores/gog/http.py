"""HTTP helpers — SSL context builder + JSON GET wrapper.

OP-50i | py_modules/unifideck/stores/gog/http.py

Two small module-level helpers shared by ``library.py``, ``dlc.py``,
``updates.py`` and ``tokens/oauth.py``:

* ``build_ssl_context()`` — returns an ``ssl.SSLContext`` with the
  bundled CA cert chain (required because some Steam Deck OS versions
  ship with an outdated cert store that rejects GOG.com).
* ``fetch_json_get(url, headers)`` — async JSON GET with retry,
  timeout and structured error reporting.

Kept module-level (no class) because there's no state to encapsulate.
"""

from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from collections.abc import Mapping
from typing import Any

from unifideck.core.net import ssl_ctx_permissive

_logger = logging.getLogger(__name__)


def build_ssl_context() -> ssl.SSLContext:
    """Build ssl context.

    Uses permissive verification because some Steam Deck OS
    versions ship with an outdated CA cert store that rejects
    ``auth.gog.com`` despite the cert being valid. Without
    this, every GOG auth attempt fails at the token-exchange
    step with ``CERTIFICATE_VERIFY_FAILED``.
    """
    return ssl_ctx_permissive("GOG OAuth — outdated Deck cert store")


async def fetch_json_get(
    url: str,
    *,
    bearer: str | None = None,
    user_agent: str,
    timeout: float = 15.0,  # noqa: ASYNC109 — timeout is API value passed to underlying lib (urllib/aiohttp/subprocess), not an asyncio.timeout() wrapper
    extra_headers: Mapping[str, str] | None = None,
    log_prefix: str = "[GOGHttp]",
) -> Any | None:
    """Fetch JSON get."""
    headers: dict[str, str] = {"User-Agent": user_agent}
    if bearer:
        headers["Authorization"] = f"Bearer {bearer}"
    if extra_headers:
        headers.update(extra_headers)

    def _sync() -> Any | None:
        """Sync."""
        try:
            ctx = build_ssl_context()
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(
                req,
                timeout=timeout,
                context=ctx,
            ) as response:
                if response.status != 200:
                    _logger.warning(
                        "%s GET %s → HTTP %d",
                        log_prefix,
                        url,
                        response.status,
                    )
                    return None
                return json.loads(response.read().decode())
        except Exception as e:
            _logger.warning(
                "%s GET %s failed: %s",
                log_prefix,
                url,
                e,
            )
            return None

    return await asyncio.to_thread(_sync)
