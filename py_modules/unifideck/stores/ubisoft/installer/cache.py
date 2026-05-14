"""
UPC installer cache — disk-backed store for the downloaded UPC installer.

OP-56b | py_modules/unifideck/stores/ubisoft/installer/cache.py

``UbisoftInstallerCache`` manages the local cache of the
``UbisoftConnectInstaller.exe`` binary downloaded from Ubisoft's CDN.
The cache lives under ``UbisoftConfig.installer_cache_dir_expanded`` and
exposes:

* ``has_valid_installer()`` — checks file existence + minimum size;
* ``get_installer_path()`` — returns the cached path (raises if missing);
* ``download_installer()`` — fetches from the CDN with retry/progress.

Cache invalidation is implicit: the file is overwritten on each
``download_installer`` call, so a corrupted or partial download is
recovered on the next run.
"""

from __future__ import annotations

import asyncio
import logging
import os
import urllib.request
from typing import Any

from unifideck.core.net import ssl_ctx_strict
from unifideck.stores.ubisoft.config import UbisoftConfig

logger = logging.getLogger(__name__)
_INSTALLER_MIN_SIZE_BYTES = 1000
_PE_MAGIC = b"MZ"
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
        filename = self._config.installer_filename
        cached_path = os.path.join(cache_dir, filename)
        if self._is_cached_valid(cached_path):
            logger.info(
                "[UbisoftInstallerCache] using cached installer",
            )
            return cached_path
        logger.info(
            "[UbisoftInstallerCache] downloading installer from %s",
            self._config.installer_url,
        )
        try:
            os.makedirs(cache_dir, exist_ok=True)
        except OSError:
            logger.exception("[UbisoftInstallerCache] cache dir creation failed")
            return None
        success = await asyncio.to_thread(
            self._download_sync,
            self._config.installer_url,
            cached_path,
        )
        if not success:
            return None
        return cached_path

    @staticmethod
    def _is_cached_valid(cached_path: str) -> bool:
        """Is cached valid."""
        if not os.path.isfile(cached_path):
            return False
        try:
            if os.path.getsize(cached_path) < _INSTALLER_MIN_SIZE_BYTES:
                return False
            with open(cached_path, "rb") as f:
                header = f.read(2)
            return header == _PE_MAGIC
        except OSError:
            return False

    @staticmethod
    def _download_sync(url: str, dest_path: str) -> bool:
        """Download sync."""
        tmp_path = dest_path + ".tmp"
        try:
            ctx = ssl_ctx_strict()
            req = urllib.request.Request(
                url,
                headers={"User-Agent": "Unifideck/1.0"},
            )
            with urllib.request.urlopen(
                req,
                timeout=_INSTALLER_DOWNLOAD_TIMEOUT_S,
                context=ctx,
            ) as response:
                if response.status not in (200, 206):
                    logger.error(
                        "[UbisoftInstallerCache] HTTP %d",
                        response.status,
                    )
                    return False
                total = _stream_to_file(response, tmp_path)
            os.replace(tmp_path, dest_path)
            logger.info(
                "[UbisoftInstallerCache] downloaded %.1f MB",
                total / (1024 * 1024),
            )
            return True
        except Exception:
            logger.exception("[UbisoftInstallerCache] download failed")
            if os.path.isfile(tmp_path):
                try:
                    os.remove(tmp_path)
                except OSError:
                    pass
            return False


def _stream_to_file(response: Any, path: str) -> int:
    """Stream to file."""
    total = 0
    with open(path, "wb") as f:
        while True:
            chunk = response.read(_DOWNLOAD_CHUNK_SIZE)
            if not chunk:
                break
            f.write(chunk)
            total += len(chunk)
    return total
