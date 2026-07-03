"""
Monitor the auth prefix for credential-file appearance — signals sign-in completion.

OP-58d | py_modules/unifideck/stores/ubisoft/auth/session_monitor.py

After the user is redirected to UPC for sign-in, we have no callback to
know when they've finished — UPC just writes credentials to disk and
exits. ``_AuthSessionMonitor`` polls the auth prefix for the appearance
of the canonical credential files (``ConnectSecureStorage.dat``,
``user.dat``) and signals completion through an ``asyncio.Event``.

Polling rate is moderate (~1 Hz) to avoid burning CPU during long
sign-in flows.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events, Result

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

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
        bus: EventBus | None = None,
    ) -> None:
        """Initialize the instance."""
        self._config = config
        self._session = session
        self._queue_auth_assets_ensure = queue_auth_assets_ensure
        self._bus = bus
        self._monitor_task: asyncio.Task[None] | None = None
        self._session_captured = False

    async def start(self) -> Result:
        """Start."""
        if self._monitor_task is not None and not self._monitor_task.done():
            self._monitor_task.cancel()
            try:
                await self._monitor_task
            except asyncio.CancelledError:
                pass
            except Exception as e:
                logger.debug(
                    "[UbisoftAuth] old monitor task error on cancel: %s",
                    e,
                )
        self._session_captured = False
        self._monitor_task = asyncio.create_task(self._loop())
        logger.info(
            "[UbisoftAuth] started auth session monitor",
        )
        return Result(success=True)

    async def _loop(self) -> None:
        """Loop."""
        auth_dir = self._config.auth_prefix_dir_expanded
        elapsed = 0.0
        while elapsed < _AUTH_MONITOR_TIMEOUT_S:
            await asyncio.sleep(_AUTH_MONITOR_POLL_INTERVAL_S)
            elapsed += _AUTH_MONITOR_POLL_INTERVAL_S
            captured = self._session.capture(auth_dir)
            if captured:
                logger.info(
                    "[UbisoftAuth] auth session monitor: token captured",
                )
                self._session.propagate_all_to_all()
                self._queue_auth_assets_ensure(
                    "post-auth-session-capture",
                )
                self._session_captured = True
                # Notify the frontend that sign-in finished so the auth
                # button flips to "log out" without a restart. Without this
                # the UI only re-detects auth at startup. Other stores emit
                # this after auth (epic/amazon/orchestrator); the Ubisoft
                # GUI-capture path never did.
                await self._emit_auth_complete()
                return
        logger.warning(
            "[UbisoftAuth] auth session monitor timed out after %ds",
            _AUTH_MONITOR_TIMEOUT_S,
        )

    async def _emit_auth_complete(self) -> None:
        """Emit STORE_AUTH_COMPLETE so the frontend refreshes auth state."""
        if self._bus is None:
            return
        try:
            await self._bus.emit(
                Events.STORE_AUTH_COMPLETE,
                store="ubisoft",
            )
        except Exception as e:
            logger.warning(
                "[UbisoftAuth] failed to emit STORE_AUTH_COMPLETE: %s",
                e,
            )

    def status(self) -> dict[str, Any]:
        """Status."""
        monitoring = self._monitor_task is not None and not self._monitor_task.done()
        return {
            "captured": self._session_captured,
            "monitoring": monitoring,
        }
