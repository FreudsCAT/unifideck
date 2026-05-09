"""services/account_service.py — Steam account switch detector.

Polls Steam's ``loginusers.vdf`` and emits ``ACCOUNT_SWITCHED``
when the active user changes. Downstream services (cache, sync)
clear per-user state and force re-sync on the event.
Rationale: Steam Deck is commonly used with multiple accounts
(main + family sharing). Switching users invalidates cached
library/shortcuts since they belong to the previous account.
"""
from __future__ import annotations

import asyncio
import logging
import os
import re
from typing import TYPE_CHECKING, Any

from ..core.types.events import Events

if TYPE_CHECKING:
    from ..config import ConfigManager
    from ..event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

DEFAULT_POLL_INTERVAL = 5  # seconds — tunable via config


class AccountService:
    """Polls Steam's loginusers.vdf and emits ACCOUNT_SWITCHED."""

    def __init__(
        self,
        bus: EventBus,
        loginusers_path: str,
        config: ConfigManager | None = None,
    ) -> None:
        """Store refs, init ``_current_user=None`` + poll task slot."""
        self._bus = bus
        self._loginusers_path = loginusers_path
        self._config = config
        
        self._current_user: str | None = None
        self._poll_task: asyncio.Task[None] | None = None
        
        self._interval = DEFAULT_POLL_INTERVAL
        if self._config:
            self._interval = self._config.get("accounts.poll_interval_seconds", DEFAULT_POLL_INTERVAL)

    async def start(self) -> None:
        """Begin the polling loop."""
        if self._poll_task is not None:
            return  # Idempotent
            
        await self._check_once()
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Cancel the polling loop and await its exit."""
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
            self._poll_task = None

    def get_current_user(self) -> str | None:
        """Return the last observed active user ID."""
        return self._current_user

    async def force_check(self) -> bool:
        """Trigger an immediate check."""
        return await self._check_once()

    async def _poll_loop(self) -> None:
        """Main loop — sleeps then calls ``_check_once``."""
        while True:
            try:
                await asyncio.sleep(self._interval)
                await self._check_once()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.warning("[AccountService] Error in poll loop: %s", e)

    async def _check_once(self) -> bool:
        """Read loginusers, compare to ``_current_user``, emit on change."""
        try:
            active_user = await self._read_active_user()
            
            if active_user is None:
                return False
                
            if self._current_user is None:
                self._current_user = active_user
                return False
                
            if active_user != self._current_user:
                logger.info("[AccountService] Account switched: %s -> %s", self._current_user, active_user)
                self._current_user = active_user
                
                self._bus.emit(
                    Events.ACCOUNT_SWITCHED,
                    new_user=active_user
                )
                return True
                
        except Exception as e:
            logger.warning("[AccountService] Check once failed: %s", e)
            
        return False

    async def _read_active_user(self) -> str | None:
        """Parse ``loginusers.vdf`` and return the most recent user ID."""
        if not os.path.exists(self._loginusers_path):
            return None
            
        try:
            def read_file() -> str:
                with open(self._loginusers_path, "r", encoding="utf-8") as f:
                    return f.read()
                    
            content = await asyncio.to_thread(read_file)
            return self._extract_most_recent(content)
        except Exception as e:
            logger.debug("[AccountService] Failed to read loginusers: %s", e)
            return None

    @staticmethod
    def _extract_most_recent(vdf_text: str) -> str | None:
        """Pure helper: extract the ``MostRecent=1`` user ID from VDF."""
        # Find all blocks looking like: "123456789" { ... "MostRecent" "1" ... }
        # Simple regex matching
        blocks = re.split(r'"(\d+)"\s*\{', vdf_text)
        
        # blocks[0] is preamble. Then alternating pairs: id, content, id, content...
        for i in range(1, len(blocks) - 1, 2):
            user_id = blocks[i]
            content = blocks[i+1]
            
            if '"mostrecent"\t\t"1"' in content.lower() or '"mostrecent"\t"1"' in content.lower():
                return user_id
                
        return None
