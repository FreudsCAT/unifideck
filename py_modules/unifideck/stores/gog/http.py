"""http.py — Async wrappers over GOG REST endpoints.

# OP-50i | py_modules/unifideck/stores/gog/http.py | Depends: OP-08a

Pure-stdlib HTTP helpers used by every GOG module that doesn't go
through ``gogdl``. We deliberately bypass aiohttp here to keep the
GOG package import-light and to share the project-wide strict SSL
context (see :mod:`unifideck.core.net`).
"""
from __future__ import annotations

import asyncio
import json
import logging
import ssl
import urllib.request
from collections.abc import Mapping
from typing import Any

from ...core.net import ssl_ctx_strict

_logger = logging.getLogger(__name__)


def build_ssl_context() -> ssl.SSLContext:
    """Build SSL context."""
    return ssl_ctx_strict()


async def fetch_json_get(
    url: str,
    *,
    bearer: str | None = None,
    user_agent: str = '',
    timeout: float = 15.0,
    extra_headers: Mapping[str, str] | None = None,
    log_prefix: str = '[GOGHttp]',
) -> Any | None:
    """Fetch JSON get."""
    headers: dict[str, str] = {'Accept': 'application/json'}
    if bearer:
        headers['Authorization'] = f'Bearer {bearer}'
    if user_agent:
        headers['User-Agent'] = user_agent
    if extra_headers:
        headers.update(extra_headers)
    req = urllib.request.Request(url, headers=headers)
    ctx = build_ssl_context()

    def _do_request() -> Any | None:
        try:
            with urllib.request.urlopen(req, context=ctx, timeout=timeout) as resp:
                raw = resp.read()
        except Exception as e:
            _logger.warning('%s GET %s failed: %s', log_prefix, url, e)
            return None
        try:
            return json.loads(raw.decode('utf-8'))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            _logger.warning('%s JSON decode %s: %s', log_prefix, url, e)
            return None

    return await asyncio.to_thread(_do_request)
