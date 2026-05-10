"""cache.py — Cache the Ubisoft Connect installer ``.exe``.

# OP-56b | py_modules/unifideck/stores/ubisoft/installer/cache.py | Depends: OP-04a
"""
from __future__ import annotations

import asyncio
import logging
import os
import urllib.request
from typing import Any

from ....core.net import ssl_ctx_strict
from ..config import UbisoftConfig

logger = logging.getLogger(__name__)
_INSTALLER_MIN_SIZE_BYTES = 1000
_PE_MAGIC = b'MZ'
_INSTALLER_DOWNLOAD_TIMEOUT_S = 600.0
_DOWNLOAD_CHUNK_SIZE = 1024 * 1024


class UbisoftInstallerCache:
    """Ubisoft installer cache."""

    def __init__(self, config: UbisoftConfig) -> None:
        """Initialize the instance."""
        self._config = config

    async def ensure_cached(self) -> str | None:
        """Ensure cached."""
        cache_dir = self._config.installer_cache_dir_expanded
        os.makedirs(cache_dir, exist_ok=True)
        cached_path = os.path.join(cache_dir, self._config.installer_filename)
        if self._is_cached_valid(cached_path):
            return cached_path
        ok = await asyncio.to_thread(
            self._download_sync, self._config.installer_url, cached_path,
        )
        return cached_path if ok else None

    @staticmethod
    def _is_cached_valid(cached_path: str) -> bool:
        """Is cached valid."""
        if not os.path.isfile(cached_path):
            return False
        try:
            size = os.path.getsize(cached_path)
        except OSError:
            return False
        if size < _INSTALLER_MIN_SIZE_BYTES:
            return False
        try:
            with open(cached_path, 'rb') as f:
                magic = f.read(2)
        except OSError:
            return False
        return magic == _PE_MAGIC

    @staticmethod
    def _download_sync(url: str, dest_path: str) -> bool:
        """Download sync."""
        tmp = dest_path + '.tmp'
        try:
            ctx = ssl_ctx_strict()
            with urllib.request.urlopen(
                url, context=ctx, timeout=_INSTALLER_DOWNLOAD_TIMEOUT_S,
            ) as resp:
                _stream_to_file(resp, tmp)
        except Exception as e:
            logger.warning('[Ubisoft.cache] download failed: %s', e)
            try:
                os.unlink(tmp)
            except OSError:
                pass
            return False
        try:
            if os.path.getsize(tmp) < _INSTALLER_MIN_SIZE_BYTES:
                os.unlink(tmp)
                return False
            os.replace(tmp, dest_path)
        except OSError as e:
            logger.warning('[Ubisoft.cache] rename failed: %s', e)
            return False
        return True


def _stream_to_file(response: Any, path: str) -> int:
    """Stream to file."""
    written = 0
    with open(path, 'wb') as f:
        while True:
            chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)
            written += len(chunk)
    return written
