"""Temporary gogdl credentials directory — used by subprocess calls.

OP-52d | py_modules/unifideck/stores/gog/tokens/gogdl_credentials.py

``gogdl`` (the CLI used by the installer pipeline) reads tokens from
its own config directory, in clear text. We don't want to point gogdl
at our encrypted store, and we don't want to leave plaintext credentials
on disk permanently.

``_GogdlCreds.acquire`` creates a unique tmpdir, writes
``gog_credentials.json`` (with ``mode=0o600``) holding the current
tokens, and returns an ``env`` dict with ``GOGDL_CONFIG_PATH`` pointing
at the tmpdir plus a cleanup coroutine that wipes the tmpdir.

Used by ``install/progress.py`` (OP-51f) and ``install/marker.py``
(OP-51g) when they spawn gogdl subprocesses.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from unifideck.stores.gog.config import GOGConfig

    CleanupFn = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


class _GogdlCreds:
    """Gogdl creds."""

    def __init__(self, *, config: GOGConfig) -> None:
        """Initialize the instance."""
        self._config = config

    async def acquire(
        self,
        access_token: str,
        refresh_token: str,
    ) -> tuple[dict[str, str], CleanupFn]:
        """Acquire."""
        tmpdir = await asyncio.to_thread(
            tempfile.mkdtemp,
            "unifideck-gogdl-",
        )
        creds_path = str(Path(tmpdir) / "gog_credentials.json")
        gogdl_data = self._build_gogdl_data(
            access_token,
            refresh_token,
        )
        await asyncio.to_thread(
            self._write_creds_sync,
            creds_path,
            gogdl_data,
        )
        env = os.environ.copy()
        env["GOGDL_CONFIG_PATH"] = tmpdir
        # CRITICAL: Force unbuffered Python output in gogdl.
        # Without this, gogdl (a Python script) buffers output when
        # stdout is piped, causing the asyncio output reading loop to
        # hang/timeout and downloads to fail.
        env["PYTHONUNBUFFERED"] = "1"
        cleanup = self._make_cleanup(creds_path, tmpdir)
        return env, cleanup

    def _build_gogdl_data(
        self,
        access_token: str,
        refresh_token: str,
    ) -> dict[str, dict[str, object]]:
        """Build GOGDL data."""
        now = time.time()
        return {
            self._config.client_id: {
                "access_token": access_token,
                "refresh_token": refresh_token,
                "token_type": "Bearer",
                "expires_in": 3600,
                "scope": "openid",
                "created_at": now,
                "loginTime": now,
            },
        }

    @staticmethod
    def _write_creds_sync(
        creds_path: str,
        gogdl_data: dict[str, dict[str, object]],
    ) -> None:
        """Write creds sync."""
        fd = os.open(
            creds_path,
            os.O_WRONLY | os.O_CREAT | os.O_TRUNC,
            0o600,
        )
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(gogdl_data, f)

    @staticmethod
    def _make_cleanup(creds_path: str, tmpdir: str) -> CleanupFn:
        """Make cleanup."""

        async def _cleanup() -> None:
            """Cleanup."""

            def _remove() -> None:
                """Remove."""
                try:
                    if Path(creds_path).is_file():
                        Path(creds_path).unlink()
                    if Path(tmpdir).is_dir():
                        Path(tmpdir).rmdir()
                except OSError as e:
                    logger.warning(
                        "[GOGTokens] gogdl temp cleanup failed: %s",
                        e,
                    )

            await asyncio.to_thread(_remove)

        return _cleanup
