"""Account service — Steam account switch detection.

OP-12c | py_modules/unifideck/services/account_service.py

``AccountService`` watches Steam's ``loginusers.vdf`` file and emits
an ``ACCOUNT_SWITCHED`` event whenever the currently logged-in Steam
account changes (the "MostRecent" entry in that file).

This is essential for stores that bind credentials per Steam user
(notably Microsoft / xCloud): a user switching their Steam account
mid-session must trigger token invalidation and re-auth flows in
every consumer service.

The service polls ``loginusers.vdf`` at a configurable interval
(default 5 s) — file-watching APIs aren't a portable option here
because the file may live on a different filesystem than the plugin
on some Steam Deck OS variants.
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
    """Polls Steam's ``loginusers.vdf`` for account-switch events."""

    def __init__(
        self,
        bus: EventBus,
        loginusers_path: str,
        config: ConfigManager | None = None,
    ) -> None:
        """Wire the service to its dependencies.

        Args:
            bus: event bus on which ``ACCOUNT_SWITCHED`` events are
                emitted when a switch is detected.
            loginusers_path: absolute path to Steam's
                ``loginusers.vdf`` file (typically
                ``~/.local/share/Steam/config/loginusers.vdf``).
            config: optional ``ConfigManager`` used to read the
                ``accounts.poll_interval_seconds`` setting; if absent
                or malformed, the default 5 s interval is used.
        """
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
        """Take an initial reading and spawn the polling task.

        The initial reading establishes the baseline against which
        future polls compare; without it the first detected change
        after boot would always look like a switch from ``None``.
        """
        self._current_user = await self._read_active_user()
        logger.info("[AccountService] initial user=%s", self._current_user)
        self._poll_task = asyncio.create_task(self._poll_loop())

    async def stop(self) -> None:
        """Cancel the polling task cleanly.

        Suppresses the ``CancelledError`` that the task raises on
        its way out so the caller doesn't have to handle it.
        """
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass

    def get_current_user(self) -> str | None:
        """Return the SteamID64 currently flagged as MostRecent.

        Returns ``None`` if the file hasn't been read yet (service
        not started) or if the most recent read failed.
        """
        return self._current_user

    async def force_check(self) -> bool:
        """Trigger an immediate switch check outside the polling loop.

        Useful when an external trigger (e.g. a user pressing
        "refresh" in the QAM panel) suggests an account change may
        have just happened.

        Returns:
            ``True`` if a switch was detected on this call.
        """
        return await self._check_once()

    async def _poll_loop(self) -> None:
        """Run the background poll loop until cancelled.

        Each iteration catches its own exceptions so a transient
        filesystem error (network mount glitch, file briefly locked
        by Steam) doesn't kill the whole task. The ``CancelledError``
        is re-raised to let ``stop`` await the task's exit.
        """
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
        """Compare the current MostRecent user against the cached value.

        Emits ``ACCOUNT_SWITCHED`` with the previous and current
        user if a change is detected. Returns ``False`` when the
        read fails (treats a failed read as a non-event rather than
        a switch to ``None``).

        Returns:
            ``True`` if a switch was emitted on this call.
        """
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
        """Read ``loginusers.vdf`` and extract the MostRecent user id.

        Errors during read (missing file, permission denied, broken
        symlink) are swallowed and surfaced as ``None`` rather than
        propagated — the caller treats ``None`` as a non-event.

        Returns:
            The SteamID64 string of the most-recent user, or
            ``None`` on any failure.
        """
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
        """Find the SteamID64 of the user marked MostRecent.

        Steam's VDF format wraps each user block in a 17-digit
        SteamID64 quoted key, followed by a brace-delimited body
        containing a ``"MostRecent" "1"`` entry on the active user.
        This regex matches that pattern without using a real VDF
        parser (overkill for a single field).

        Args:
            vdf_text: raw textual content of ``loginusers.vdf``.

        Returns:
            The 17-digit SteamID64, or ``None`` if no user is
            marked MostRecent (rare: typically only on a freshly-
            installed Steam without any prior logins).
        """
        pattern = re.compile(
            r'"(\d{17})"\s*\{[^}]*"MostRecent"\s*"1"',
            re.DOTALL,
        )
        m = pattern.search(vdf_text)
        return m.group(1) if m else None
