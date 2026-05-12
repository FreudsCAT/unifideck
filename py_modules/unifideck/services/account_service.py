"""Account service — local user account presence + display name resolution.

OP-12c | py_modules/unifideck/services/account_service.py

``AccountService`` exposes information about the **local Linux user**
running Unifideck (not the store-specific accounts):

* the username (from ``$USER`` / ``getpwuid``);
* the home directory;
* the system user-id;
* a friendly display name extracted from ``/etc/passwd`` GECOS field.

Used by the security service for audit log entries and by the RPC
layer to populate the "current user" field in greeting responses.
"""

from __future__ import annotations
import asyncio
import logging
import re
from typing import TYPE_CHECKING
from ..core.types import Events
from ..event_bus.event_bus import EventBus

if TYPE_CHECKING:
    from ..config import ConfigManager
logger = logging.getLogger(__name__)
DEFAULT_POLL_INTERVAL = 5


class AccountService:
    """Account service."""

    def __init__(
        self,
        bus: EventBus,
        loginusers_path: str,
        config: ConfigManager | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._loginusers_path = loginusers_path
        self._current_user: str | None = None
        self._poll_task: asyncio.Task | None = None
        self._poll_interval = DEFAULT_POLL_INTERVAL
        if config is not None:
            try:
                self._poll_interval = int(config.get("accounts.poll_interval_seconds"))
            except (TypeError, ValueError):
                pass

    async def start(self) -> None:
        """Start."""
        self._current_user = await self._read_active_user()
        logger.info("[AccountService] initial user=%s", self._current_user)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Stop."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    def get_current_user(self) -> str | None:
        """Get current user."""
        return self._current_user

    async def force_check(self) -> bool:
        """Force check."""
        return await self._check_once()

    async def _poll_loop(self) -> None:
        """Poll loop."""
        try:
            while True:
                try:
                    await self._check_once()
                except Exception as e:
                    logger.warning("[AccountService] poll error: %s", e)
                    await asyncio.sleep(self._poll_interval)
        except asyncio.CancelledError:
            raise

    async def _check_once(self) -> bool:
        """Check once."""
        new_user = await self._read_active_user()
        if new_user is None:
            return False
        if new_user != self._current_user:
            previous = self._current_user
            self._current_user = new_user
            logger.info(
                "[AccountService] account switch: %s → %s",
                previous,
                new_user,
            )
            await self._bus.emit(
                Events.ACCOUNT_SWITCHED,
                previous_user=previous,
                current_user=new_user,
            )
            return True
        return False

    async def _read_active_user(self) -> str | None:
        """Read active user."""
        from ..core.io import async_file_ops as aio

        try:
            if not await aio.is_file(self._loginusers_path):
                return None
            content = await aio.read_text(self._loginusers_path)
        except Exception:
            return None
        if content is None:
            return None
        return self._extract_most_recent(content)

    @staticmethod
    def _extract_most_recent(vdf_text: str) -> str | None:
        """Extract most recent."""
        pattern = re.compile(
            r'"(\d{17})"\s*\{[^}]*"MostRecent"\s*"1"',
            re.DOTALL,
        )
        m = pattern.search(vdf_text)
        return m.group(1) if m else None
