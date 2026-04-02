"""Import play time data from Steam's local data files."""

import logging
import os
import time as _time
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional, Tuple

from .database import ActivityDatabase

logger = logging.getLogger("unifideck")

# Cache parsed VDF data for 5 minutes (avoid re-parsing on every session start)
_VDF_CACHE_TTL = 300


class SteamDataImporter:
    """Import historical play time from Steam's localconfig.vdf.

    Steam tracks play time for non-Steam shortcuts in:
    ~/.steam/steam/userdata/{user_id}/config/localconfig.vdf

    Structure:
    UserLocalConfigStore.Software.Valve.Steam.apps.{appid}.Playtime (minutes)
    UserLocalConfigStore.Software.Valve.Steam.apps.{appid}.LastPlayed (unix ts)

    Only imports data for games already in our database (Unifideck-managed).
    Creates synthetic sessions with is_manual=1 for any time gap.
    All imports are additive-only — never reduces existing tracked time.
    """

    def __init__(self, db: ActivityDatabase, steam_path: str, user_id: str):
        self.db = db
        self.steam_path = steam_path
        self.user_id = user_id
        self.vdf_path = os.path.join(
            steam_path, "userdata", user_id, "config", "localconfig.vdf"
        )
        self._apps_cache: Optional[Dict] = None
        self._apps_cache_time: float = 0

    def _get_apps_section(self) -> Optional[Dict]:
        """Parse and cache the apps section from localconfig.vdf."""
        now = _time.monotonic()
        if self._apps_cache is not None and (now - self._apps_cache_time) < _VDF_CACHE_TTL:
            return self._apps_cache

        if not os.path.exists(self.vdf_path):
            logger.warning(f"[PLAYTIME] localconfig.vdf not found: {self.vdf_path}")
            return None

        try:
            vdf_data = self._parse_vdf(self.vdf_path)
        except Exception as e:
            logger.error(f"[PLAYTIME] Failed to parse localconfig.vdf: {e}")
            return None

        apps = self._get_nested(
            vdf_data,
            ["UserLocalConfigStore", "Software", "Valve", "Steam", "apps"],
        )
        if not apps:
            apps = self._get_nested(
                vdf_data,
                ["userlocalconfigstore", "software", "valve", "steam", "apps"],
            )

        self._apps_cache = apps
        self._apps_cache_time = now
        return apps

    def _get_app_playtime(self, apps: Dict, steam_app_id: int) -> Tuple[int, int]:
        """Get playtime and last_played for a specific app from parsed VDF data.

        Args:
            apps: The parsed apps section from localconfig.vdf.
            steam_app_id: The signed steam app ID.

        Returns:
            (playtime_minutes, last_played_unix_ts) — both 0 if not found.
        """
        # Convert signed back to unsigned for VDF lookup
        if steam_app_id < 0:
            unsigned_id = steam_app_id + 2**32
        else:
            unsigned_id = steam_app_id

        app_data = apps.get(str(unsigned_id))
        if not isinstance(app_data, dict):
            return (0, 0)

        playtime_minutes = 0
        last_played_ts = 0

        for key, value in app_data.items():
            key_lower = key.lower()
            if key_lower == "playtime":
                try:
                    playtime_minutes = int(value)
                except (ValueError, TypeError):
                    pass
            elif key_lower == "lastplayed":
                try:
                    last_played_ts = int(value)
                except (ValueError, TypeError):
                    pass

        return (playtime_minutes, last_played_ts)

    def _import_gap_for_game(
        self, game_id: int, title: str, steam_minutes: int,
        last_played_ts: int
    ) -> Optional[int]:
        """Import the time gap for a single game. Additive only.

        Compares Steam's total against ALL our tracked time (manual + automatic)
        to ensure idempotency — re-running never creates duplicate imports.

        Returns:
            Gap in seconds that was imported, or None if no import needed.
        """
        # Count ALL tracked time (both real sessions and previous imports)
        existing = self.db.query_one(
            """SELECT COALESCE(SUM(duration_secs), 0) as tracked_secs
               FROM play_sessions
               WHERE game_id = ?""",
            (game_id,),
        )
        tracked_secs = existing["tracked_secs"] if existing else 0
        steam_total_secs = steam_minutes * 60

        # Only import the gap (additive only — never subtract)
        gap_secs = steam_total_secs - tracked_secs
        if gap_secs < 60:  # Skip if gap is less than 1 minute
            return None

        if last_played_ts > 0:
            ended_at = datetime.fromtimestamp(
                last_played_ts, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            started_at = datetime.fromtimestamp(
                last_played_ts - gap_secs, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%S.%fZ")
        else:
            now = datetime.now(timezone.utc)
            ended_at = now.strftime("%Y-%m-%dT%H:%M:%S.%fZ")
            started_at = (now - timedelta(seconds=gap_secs)).strftime(
                "%Y-%m-%dT%H:%M:%S.%fZ"
            )

        self.db.execute(
            """INSERT INTO play_sessions
               (game_id, steam_user_id, started_at, ended_at, duration_secs,
                end_reason, is_manual, session_note)
               VALUES (?, ?, ?, ?, ?, 'normal', 1,
                       'Imported from Steam localconfig.vdf')""",
            (game_id, self.user_id, started_at, ended_at, gap_secs),
        )

        logger.info(
            f"[PLAYTIME] Imported {gap_secs // 60}m for {title} "
            f"(Steam had {steam_minutes}m, we had {tracked_secs // 60}m)"
        )
        return gap_secs

    def import_game_playtime(self, steam_app_id: int, game_id: int, title: str) -> bool:
        """One-time additive sync of Steam's playtime for a single game.

        Called automatically on first session start for a game. Fast path:
        uses cached VDF data, single DB query to check gap.

        Args:
            steam_app_id: The signed Steam shortcut appId.
            game_id: Our database game ID.
            title: Game title for logging.

        Returns:
            True if time was imported, False otherwise.
        """
        try:
            apps = self._get_apps_section()
            if not apps:
                return False

            playtime_minutes, last_played_ts = self._get_app_playtime(apps, steam_app_id)
            if playtime_minutes <= 0:
                return False

            gap = self._import_gap_for_game(game_id, title, playtime_minutes, last_played_ts)
            return gap is not None
        except Exception as e:
            logger.error(f"[PLAYTIME] Single-game import failed for {title}: {e}")
            return False

    def import_localconfig_playtime(self) -> Dict[str, Any]:
        """Parse localconfig.vdf and import play time for all known games.

        Returns:
            Dict with import results: games_imported, total_minutes_imported, skipped, errors.
        """
        apps = self._get_apps_section()
        if apps is None:
            return {
                "games_imported": 0,
                "total_minutes_imported": 0,
                "skipped": 0,
                "errors": ["Could not read localconfig.vdf"],
            }

        games_imported = 0
        total_minutes = 0
        skipped = 0
        errors: List[str] = []

        for app_id_str, app_data in apps.items():
            if not isinstance(app_data, dict):
                continue

            try:
                app_id = int(app_id_str)
            except ValueError:
                continue

            # Only process non-Steam shortcuts (appId > 2 billion)
            if app_id < 2_000_000_000:
                continue

            playtime_minutes = 0
            last_played_ts = 0

            for key, value in app_data.items():
                key_lower = key.lower()
                if key_lower == "playtime":
                    try:
                        playtime_minutes = int(value)
                    except (ValueError, TypeError):
                        pass
                elif key_lower == "lastplayed":
                    try:
                        last_played_ts = int(value)
                    except (ValueError, TypeError):
                        pass

            if playtime_minutes <= 0:
                continue

            # Convert unsigned Steam appId to signed (same as Unifideck does)
            if app_id > 2**31:
                signed_app_id = app_id - 2**32
            else:
                signed_app_id = app_id

            # Check if this game is in our database
            game = self.db.find_game_by_steam_app_id(signed_app_id)
            if not game:
                skipped += 1
                continue

            try:
                gap = self._import_gap_for_game(
                    game["id"], game["title"], playtime_minutes, last_played_ts
                )
                if gap is not None:
                    games_imported += 1
                    total_minutes += gap // 60
                else:
                    skipped += 1
            except Exception as e:
                errors.append(f"Failed to import {game.get('title', app_id_str)}: {str(e)}")
                logger.error(f"[PLAYTIME] Import error for {app_id_str}: {e}")

        logger.info(
            f"[PLAYTIME] Steam import complete: {games_imported} games, "
            f"{total_minutes} minutes imported, {skipped} skipped"
        )
        return {
            "games_imported": games_imported,
            "total_minutes_imported": total_minutes,
            "skipped": skipped,
            "errors": errors,
        }

    def _parse_vdf(self, path: str) -> Dict:
        """Parse a Valve Data Format (.vdf) file into a nested dict."""
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            content = f.read()

        result: Dict = {}
        self._parse_vdf_block(content, 0, result)
        return result

    def _parse_vdf_block(
        self, content: str, pos: int, target: Dict
    ) -> int:
        """Parse a VDF block recursively. Returns position after closing brace."""
        length = len(content)
        while pos < length:
            while pos < length and content[pos] in " \t\r\n":
                pos += 1
            if pos >= length:
                break

            if content[pos] == "}":
                return pos + 1

            if content[pos] != '"':
                pos += 1
                continue

            key, pos = self._read_quoted_string(content, pos)
            if key is None:
                break

            while pos < length and content[pos] in " \t\r\n":
                pos += 1
            if pos >= length:
                break

            if content[pos] == '"':
                value, pos = self._read_quoted_string(content, pos)
                if value is not None:
                    target[key] = value
            elif content[pos] == "{":
                pos += 1
                sub: Dict = {}
                pos = self._parse_vdf_block(content, pos, sub)
                target[key] = sub
            else:
                pos += 1

        return pos

    def _read_quoted_string(self, content: str, pos: int) -> tuple:
        """Read a quoted string starting at pos. Returns (string, new_pos)."""
        if pos >= len(content) or content[pos] != '"':
            return (None, pos)

        pos += 1
        start = pos
        while pos < len(content):
            if content[pos] == "\\":
                pos += 2
                continue
            if content[pos] == '"':
                return (content[start:pos], pos + 1)
            pos += 1

        return (content[start:pos], pos)

    def _get_nested(self, data: Dict, keys: list) -> Optional[Dict]:
        """Navigate nested dict by key path (case-insensitive)."""
        current = data
        for key in keys:
            if not isinstance(current, dict):
                return None
            if key in current:
                current = current[key]
            else:
                found = False
                for k in current:
                    if k.lower() == key.lower():
                        current = current[k]
                        found = True
                        break
                if not found:
                    return None
        return current if isinstance(current, dict) else None
