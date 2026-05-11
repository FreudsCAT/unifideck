"""gogdl_credentials.py — Write the on-disk credentials file ``gogdl`` reads.

# OP-52d | py_modules/unifideck/stores/gog/tokens/gogdl_credentials.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import tempfile
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

    from ..config import GOGConfig
    CleanupFn = Callable[[], Awaitable[None]]

logger = logging.getLogger(__name__)


class _GogdlCreds:
    """Gogdl creds."""

    def __init__(self, *, config: GOGConfig) -> None:
        """Initialize the instance."""
        self._config = config

    async def acquire(
        self, access_token: str, refresh_token: str,
    ) -> tuple[dict[str, str], CleanupFn]:
        """Acquire."""
        data = self._build_gogdl_data(access_token, refresh_token)
        tmpdir = tempfile.mkdtemp(prefix='unifideck-gogdl-')
        creds_path = os.path.join(tmpdir, 'credentials.json')
        await asyncio.to_thread(
            self._write_creds_sync, creds_path, data,
        )
        env = {'GOGDL_CONFIG_PATH': tmpdir}
        return env, self._make_cleanup(creds_path, tmpdir)

    def _build_gogdl_data(
        self, access_token: str, refresh_token: str,
    ) -> dict[str, dict[str, object]]:
        """Build GOGDL data."""
        return {
            'galaxy': {
                'access_token': access_token,
                'refresh_token': refresh_token,
                'expires_in': 3600,
                'token_type': 'bearer',
                'user_id': '',
                'session_id': '',
                'scope': '',
                'loginTime': int(time.time()),
                'client_id': self._config.client_id,
                'client_secret': self._config.client_secret,
            },
        }

    @staticmethod
    def _write_creds_sync(
        creds_path: str, gogdl_data: dict[str, dict[str, object]],
    ) -> None:
        """Write creds sync."""
        os.makedirs(os.path.dirname(creds_path), exist_ok=True)
        with open(creds_path, 'w', encoding='utf-8') as f:
            json.dump(gogdl_data, f)
        os.chmod(creds_path, 0o600)

    @staticmethod
    def _make_cleanup(creds_path: str, tmpdir: str) -> CleanupFn:
        """Make cleanup."""

        async def _cleanup() -> None:
            def _do() -> None:
                for p in (creds_path, tmpdir):
                    try:
                        if os.path.isfile(p):
                            os.unlink(p)
                        elif os.path.isdir(p):
                            os.rmdir(p)
                    except OSError as e:
                        logger.debug(
                            '[GOGGogdlCreds] cleanup %s: %s', p, e,
                        )

            await asyncio.to_thread(_do)

        return _cleanup
