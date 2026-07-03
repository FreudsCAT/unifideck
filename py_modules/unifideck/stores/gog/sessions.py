"""stores/gog/sessions.py — report play sessions to GOG (+ read total).

Mirror of ``gog/achievements.py`` for the *playtime* channel. The plugin tracks
sessions locally (``services/playtime``); this pushes each finalized session up
to GOG so GOG Galaxy / the user's other devices reflect time played here, and
reads GOG's authoritative total back for display.

Endpoint (``gameplay.gog.com/games/{id}/users/{id}/sessions``) + ACCOUNT-token
auth are identical to the achievements read — verified live (June 2026). gogdl
has no playtime command, so we POST directly, exactly like Heroic does.

Sessions are ADDITIVE on GOG's side, so the caller (``PlaytimeSyncService``)
owns de-duplication (the ``play_sessions.reported_at`` watermark). This class is
stateless beyond the token manager.
"""
from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING
from urllib.error import HTTPError, URLError

from .galaxy_api import fetch_gog_playtime_minutes, post_gog_session

if TYPE_CHECKING:
    from .tokens import GOGTokenManager

logger = logging.getLogger(__name__)


class GOGSessions:
    """Push play sessions to GOG and read back the total (account-token auth)."""

    def __init__(self, tokens: GOGTokenManager) -> None:
        """Initialize the instance."""
        self._tokens = tokens

    async def report_session(
        self, game_id: str, started_at_unix: int, duration_secs: int,
    ) -> bool:
        """Report one session to GOG. ``True`` on success, ``False`` otherwise.

        ``False`` (not an exception) on any auth/network failure so the caller
        leaves the session unmarked and retries on the next drain.
        """
        minutes = round(duration_secs / 60)
        if minutes < 1:
            # Sub-30s rounds to 0 — nothing meaningful to report. Treat as
            # success so the caller marks it done and doesn't requeue forever.
            return True
        creds = await self._creds()
        if creds is None:
            return False
        user_id, access_token = creds
        try:
            return await asyncio.to_thread(
                post_gog_session,
                user_id, str(game_id), access_token,
                int(started_at_unix), minutes,
            )
        except (HTTPError, URLError, OSError, TimeoutError) as e:
            logger.info("[gog.sessions] report failed for %s: %s", game_id, e)
            return False

    async def get_total_secs(self, game_id: str) -> int | None:
        """GOG's authoritative total time played for this game, in seconds.

        ``None`` on auth/network failure or a game GOG has no sessions for.
        """
        creds = await self._creds()
        if creds is None:
            return None
        user_id, access_token = creds
        try:
            minutes = await asyncio.to_thread(
                fetch_gog_playtime_minutes, user_id, str(game_id), access_token,
            )
        except (HTTPError, URLError, OSError, TimeoutError) as e:
            logger.info("[gog.sessions] total fetch failed for %s: %s", game_id, e)
            return None
        return minutes * 60 if minutes is not None else None

    async def _creds(self) -> tuple[str, str] | None:
        """``(galaxy_user_id, fresh access_token)`` or ``None`` if unavailable.

        Always refreshes via the token manager first — the access token in the
        gogdl mirror goes stale even when it looks fresh, so the *only* safe
        source is ``refresh_if_stale`` (which persists GOG's rotated token).
        """
        if not self._tokens.has_tokens:
            await self._tokens.load()
        await self._tokens.refresh_if_stale()
        user_id = self._tokens.user_info.galaxy_user_id
        access_token = self._tokens.access_token
        if not user_id or not access_token:
            return None
        return user_id, access_token
