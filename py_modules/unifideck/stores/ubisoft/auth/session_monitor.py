"""session_monitor.py — Background poll that detects UPC auth captures.

# OP-58d | py_modules/unifideck/stores/ubisoft/auth/session_monitor.py | Depends: (none)
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections.abc import Callable
from typing import Any

from ....core.types import Result

_AUTH_MONITOR_TIMEOUT_S = 30 * 60
_AUTH_MONITOR_POLL_INTERVAL_S = 2.0
logger = logging.getLogger(__name__)


class _AuthSessionMonitor:
    """Auth session monitor."""

    def __init__(
        self,
        *,
        config: Any,
        session: Any,
        queue_auth_assets_ensure: Callable[[str], None],
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._session = session
        self._queue = queue_auth_assets_ensure
        self._task: asyncio.Task[None] | None = None
        self._started_at: float = 0.0
        self._captured: bool = False
        self._error: str | None = None

    async def start(self) -> Result:
        """Start."""
        if self._task is not None and not self._task.done():
            return Result(success=True)
        self._started_at = time.monotonic()
        self._captured = False
        self._error = None
        self._task = asyncio.create_task(
            self._loop(), name='ubisoft_auth_session_monitor',
        )
        return Result(success=True)

    async def _loop(self) -> None:
        """Loop."""
        deadline = self._started_at + _AUTH_MONITOR_TIMEOUT_S
        while time.monotonic() < deadline:
            try:
                source = self._session.find_best_credential_source()
                if source:
                    self._captured = True
                    self._queue('auth_session_monitor')
                    logger.info(
                        '[Ubisoft.auth] session captured at %s', source,
                    )
                    return
            except Exception as e:
                self._error = str(e)
                logger.debug('[Ubisoft.auth] monitor poll error: %s', e)
            await asyncio.sleep(_AUTH_MONITOR_POLL_INTERVAL_S)
        self._error = self._error or 'timeout'

    def status(self) -> dict[str, Any]:
        """Status."""
        running = self._task is not None and not self._task.done()
        return {
            'running': running,
            'captured': self._captured,
            'error': self._error,
            'elapsed_s': (
                round(time.monotonic() - self._started_at, 1)
                if self._started_at else 0.0
            ),
        }
