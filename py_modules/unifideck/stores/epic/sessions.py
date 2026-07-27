"""stores/epic/sessions.py — report play sessions to Epic (+ read totals).

Mirror of ``gog/sessions.py`` for Epic. Pushes finalized local sessions to Epic
so the launcher's "Time Played" / other devices reflect them, and reads the
account's totals back for display.

Auth resolution is identical to ``epic/achievements.py``: the legendary launcher
OAuth token from ``user.json``, refreshed via ``legendary status`` when stale.
(Kept self-contained rather than shared with achievements to avoid disturbing
that working path; the resolver is small and stable.)
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from unifideck.core.binaries import clean_cli_env

from .playtime_api import fetch_epic_playtime_all, put_epic_session

logger = logging.getLogger(__name__)

# Refresh the launcher token this many seconds before it actually expires.
_TOKEN_SKEW_SECONDS = 120
# Totals are pulled per-game during a drain; cache the whole-account map briefly
# so one drain only hits ``/all`` once.
_TOTALS_TTL_SECONDS = 60.0


def _parse_ts(value: Any) -> float | None:
    """ISO-8601 (``2024-09-09T17:25:39.535Z``) → epoch float, or None."""
    if not value:
        return None
    if isinstance(value, (int, float)):
        return float(value)
    try:
        return datetime.fromisoformat(
            str(value).replace("Z", "+00:00"),
        ).timestamp()
    except (ValueError, TypeError):
        return None


class EpicSessions:
    """Push play sessions to Epic and read back totals (launcher-token auth)."""

    def __init__(
        self,
        cli_path: str | None,
        user_file: str,
        machine_id: str,
        info_timeout: float = 30.0,
    ) -> None:
        """Initialize the instance."""
        self._cli_path = cli_path
        self._user_file = Path(user_file).expanduser()
        self._machine_id = machine_id
        self._info_timeout = info_timeout
        self._totals: tuple[float, dict[str, int]] | None = None

    async def report_session(
        self, game_id: str, started_at_unix: int, duration_secs: int,
    ) -> bool:
        """Report one session to Epic. ``True`` on success, else ``False``.

        ``game_id`` is the Epic ``artifactId`` (== legendary app_name). On a
        rejected (401) token, force a refresh and retry once.
        """
        token, account_id = await self._resolve_auth()
        if not token or not account_id:
            return False
        end = datetime.fromtimestamp(
            started_at_unix + duration_secs, tz=UTC,
        )
        start = datetime.fromtimestamp(started_at_unix, tz=UTC)
        start_iso = start.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        end_iso = end.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        token_type = self._read_user().get("token_type", "bearer")

        code = await asyncio.to_thread(
            put_epic_session, account_id, str(game_id), token_type, token,
            start_iso, end_iso, self._machine_id,
        )
        if code == 401:
            token, account_id = await self._resolve_auth(force_refresh=True)
            if not token or not account_id:
                return False
            code = await asyncio.to_thread(
                put_epic_session, account_id, str(game_id), token_type, token,
                start_iso, end_iso, self._machine_id,
            )
        ok = code is not None and 200 <= code < 300
        if not ok:
            logger.info("[epic.sessions] report %s → HTTP %s", game_id, code)
        return ok

    async def get_total_secs(self, game_id: str) -> int | None:
        """Epic's total time played for ``game_id`` (artifactId), in seconds."""
        totals = await self._fetch_totals()
        if totals is None:
            return None
        return totals.get(str(game_id))

    # -- auth (legendary launcher token; mirrors epic/achievements.py) ------

    async def _fetch_totals(self) -> dict[str, int] | None:
        """The account's ``{artifactId: secs}`` map, TTL-cached. 401 → refresh."""
        if self._totals and (time.monotonic() - self._totals[0]) < _TOTALS_TTL_SECONDS:
            return self._totals[1]
        token, account_id = await self._resolve_auth()
        if not token or not account_id:
            return None
        token_type = self._read_user().get("token_type", "bearer")
        code, mapping = await asyncio.to_thread(
            fetch_epic_playtime_all, account_id, token_type, token,
        )
        if code == 401:
            token, account_id = await self._resolve_auth(force_refresh=True)
            if not token or not account_id:
                return None
            code, mapping = await asyncio.to_thread(
                fetch_epic_playtime_all, account_id, token_type, token,
            )
        if code != 200:
            return None
        self._totals = (time.monotonic(), mapping)
        return mapping

    async def _resolve_auth(
        self, force_refresh: bool = False,
    ) -> tuple[str | None, str | None]:
        """``(access_token, account_id)``, refreshing via legendary if stale."""
        data = self._read_user()
        if (force_refresh or self._is_expired(data)) and self._cli_path:
            await self._refresh_token()
            data = self._read_user()
        return data.get("access_token"), data.get("account_id")

    def _read_user(self) -> dict[str, Any]:
        """Read legendary ``user.json`` (``{}`` on any failure)."""
        try:
            if self._user_file.is_file():
                data = json.loads(self._user_file.read_text(encoding="utf-8"))
                return data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            logger.debug("[epic.sessions] user.json read failed", exc_info=True)
        return {}

    @staticmethod
    def _is_expired(data: dict[str, Any]) -> bool:
        """Expired (or unknown → force a refresh attempt)."""
        exp = _parse_ts(data.get("expires_at"))
        if exp is None:
            return True
        return time.time() >= (exp - _TOKEN_SKEW_SECONDS)

    async def _refresh_token(self) -> None:
        """Best-effort token refresh: ``legendary status`` rewrites user.json."""
        if not self._cli_path:
            return
        logger.info("[epic.sessions] refreshing Epic token via legendary")
        proc = None
        try:
            proc = await asyncio.create_subprocess_exec(
                self._cli_path, "status",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
                env=clean_cli_env(),
            )
            await asyncio.wait_for(proc.communicate(), timeout=self._info_timeout)
        except (TimeoutError, OSError) as e:
            logger.warning("[epic.sessions] token refresh failed: %s", e)
            if proc is not None:
                with contextlib.suppress(ProcessLookupError):
                    proc.kill()
