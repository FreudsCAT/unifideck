import asyncio
import json
import logging
import os
import re
import shutil
import time
from pathlib import Path
from typing import Any

from unifideck.launcher.proton.infrastructure.prefix_layout import resolve_drive_c
from unifideck.security.secure_token_store import SecureTokenStore
from unifideck.services.cloud_save import safety
from unifideck.services.cloud_save.gog_cloud_api import (
    exchange_game_token,
    fetch_gog_client_creds,
    fetch_gog_client_id,
    list_cloud_objects,
    pick_gog_save_dir,
    summarize_cloud_objects,
)
from unifideck.services.cloud_save.strategy_base import CloudSaveStrategy

logger = logging.getLogger(__name__)


class GOGCloudSaveStrategy(CloudSaveStrategy):
    """Cloud save strategy for GOG games using gogdl."""

    store_id = "gog"

    def __init__(
        self, local_save_root: str, config: Any = None, cache: Any = None,
    ) -> None:
        super().__init__(local_save_root, config, cache)
        # GOG-private in-memory cache of the *metadata*-resolved save dir (the
        # base owns the top-level resolved-dir memo). Backed by an on-disk
        # cache (``gog_save_dirs``) so the network round-trip survives restarts.
        self._cached_metadata_dir: dict[str, str] = {}

        # Resolve path to the bundled gogdl binary. Use the canonical
        # plugin-root resolver (same one the launch path uses) — bin/ is a
        # SIBLING of py_modules, so naive dirname-walking from this file
        # lands on py_modules and misses bin/, falling back to a bare
        # "gogdl" that isn't on the launcher's PATH.
        from unifideck.core.paths import resolve_plugin_dir
        plugin_dir = str(resolve_plugin_dir(start=Path(__file__)))
        self.gogdl_bin = os.path.join(plugin_dir, "bin", "gogdl")
        if not os.path.exists(self.gogdl_bin):
            self.gogdl_bin = "gogdl"

    def _resolve_store_save_dir(self, game_id: str) -> str | None:
        """GOG-specific save-dir resolution (config override + memo in base).

        Resolution order (first hit wins):
          1. **GOG cloud-save metadata** (authoritative): the location
             template from ``remote-config.gog.com`` resolved against the
             game's Wine prefix — i.e. where the game actually reads/writes
             saves. This is what Galaxy/Heroic use.
          2. Enriched save-location metadata (unifiDB/PCGamingWiki via Ludusavi).
          3. Heuristic title-match of an existing folder in the prefix.
        Returns ``None`` when no prefix/real location exists yet.
        """
        drive_c = resolve_drive_c(self._prefix_root(game_id))

        if drive_c:
            # 1. Authoritative: resolve from GOG's cloud-save config.
            meta_dir = self._resolve_save_dir_from_metadata(game_id, drive_c)
            if meta_dir:
                return meta_dir

            # 2. Enriched save-location metadata (unifiDB / PCGamingWiki via
            # Ludusavi). More reliable than the title-folder scan below, but
            # below GOG's own authoritative config above so it can't regress.
            # ``prefix_path`` is drive_c's parent (the registry-prefix root).
            enriched = self._resolve_enriched(
                game_id, prefix_path=str(drive_c.parent),
            )
            if enriched:
                return enriched

            # 3. Heuristic: match an existing folder by game title.
            title_dir = self._resolve_by_title(game_id, drive_c)
            if title_dir:
                return title_dir

        # No real save location found — e.g. the game was never launched so no
        # Wine prefix exists yet. Return None (NOT a staging dir the game never
        # reads); callers treat None as "unresolved" and skip syncing instead
        # of stranding saves in a folder nothing uses.
        logger.info("[GOGSync] No save dir resolved for %s (no prefix yet)", game_id)
        return None

    def _resolve_by_title(self, game_id: str, drive_c: Path) -> str | None:
        """Heuristic: match an existing prefix subfolder by game title."""
        game_title = (
            self.config.get(f"games.{game_id}.title") or ""
        ) if self.config else ""
        if not game_title:
            return None
        safe_title = re.sub(r"[^a-zA-Z0-9]", "", game_title).lower()
        for candidate in (
            drive_c / "users" / "steamuser" / "Saved Games",
            drive_c / "users" / "steamuser" / "Documents",
            drive_c / "users" / "steamuser" / "AppData" / "Local",
            drive_c / "users" / "steamuser" / "AppData" / "Roaming",
        ):
            match = self._match_child_by_title(candidate, safe_title)
            if match:
                return match
        return None

    @staticmethod
    def _match_child_by_title(candidate: Path, safe_title: str) -> str | None:
        """Find a child dir of ``candidate`` whose name matches ``safe_title``."""
        if not candidate.is_dir():
            return None
        for child in candidate.iterdir():
            if not child.is_dir():
                continue
            child_name = re.sub(r"[^a-zA-Z0-9]", "", child.name).lower()
            if safe_title in child_name or child_name in safe_title:
                logger.info(
                    "[GOGSync] Auto-detected save dir via title match: %s", child
                )
                return str(child)
        return None

    # ── GOG cloud-save location resolution (from GOG metadata) ───────
    def _resolve_save_dir_from_metadata(
        self, game_id: str, drive_c: Path,
    ) -> str | None:
        """Resolve the in-prefix save dir from GOG's cloud-save config.

        Fetches the game's ``clientId`` then its cloudStorage location
        template (e.g. ``<?DOCUMENTS?>\\The Witcher 3``) and resolves the
        path variable against the prefix. The result is created and cached
        (in-process + on disk) so the network round-trip happens once per
        game. Returns None on any failure so the caller can fall back.
        """
        cached = self._cached_metadata_dir.get(game_id) or self._read_cached_save_dir(game_id)
        if cached:
            self._cached_metadata_dir[game_id] = cached
            return cached
        try:
            client_id = fetch_gog_client_id(game_id)
            if not client_id:
                return None
            chosen = pick_gog_save_dir(client_id, drive_c)
            if chosen is None:
                return None
            chosen.mkdir(parents=True, exist_ok=True)
            path = str(chosen)
            self._cached_metadata_dir[game_id] = path
            self._write_cached_save_dir(game_id, path)
            logger.info(
                "[GOGSync] Resolved save dir from GOG metadata: %s", path,
            )
            return path
        except Exception as e:
            logger.warning(
                "[GOGSync] GOG metadata save-dir resolution failed for %s: %s",
                game_id, e,
            )
            return None

    # ── Real GOG cloud-save info (cloudstorage.gog.com LIST) ─────────
    async def _fetch_cloud_info(self, game_id: str) -> dict[str, Any] | None:
        """Real GOG-cloud save info: has_saves, newest timestamp, file_count.

        Queries GOG's cloud-storage LIST endpoint (the same one gogdl uses
        internally) so the manual cloud-save UI shows the ACTUAL cloud state
        instead of the local backup mirror. Needs a per-game Galaxy-client
        token exchange (GOG scopes cloud storage per clientId). The base
        memoises this 300s and invalidates it after an upload. Returns ``None``
        on any failure so the caller falls back to the mirror. ``total_bytes``
        is 0 (not in the LIST response) — backfilled by the caller.
        """
        return await asyncio.to_thread(self._query_cloud_info_blocking, game_id)

    def _query_cloud_info_blocking(self, game_id: str) -> dict[str, Any] | None:
        try:
            creds = self._read_gog_credentials() or {}
            user_id = creds.get("user_id")
            refresh_token = creds.get("refresh_token")
            if not user_id or not refresh_token:
                return None
            client_id, client_secret = fetch_gog_client_creds(game_id)
            if not client_id or not client_secret:
                return None
            token = exchange_game_token(client_id, client_secret, refresh_token)
            if not token:
                return None
            objects = list_cloud_objects(user_id, client_id, token)
            if objects is None:
                return None
            return summarize_cloud_objects(objects)
        except Exception as e:
            logger.debug("[GOGSync] cloud-info query failed for %s: %s", game_id, e)
            return None

    def _read_gog_credentials(self) -> dict[str, Any] | None:
        """Return ``{access_token, refresh_token, user_id}`` from the auth file."""
        auth_path = self._convert_gog_token()
        if not auth_path:
            return None
        try:
            with open(auth_path) as f:
                data: dict[str, Any] = json.load(f)
        except Exception:
            return None
        account = data.get("46899977096215655")
        return account if isinstance(account, dict) else None

    def _read_cached_save_dir(self, game_id: str) -> str | None:
        state_file = self._get_state_file()
        if not state_file.exists():
            return None
        try:
            with open(state_file) as f:
                data: dict[str, Any] = json.load(f)
        except Exception:
            return None
        cached = data.get("gog_save_dirs", {}).get(game_id)
        return str(cached) if cached else None

    def _write_cached_save_dir(self, game_id: str, path: str) -> None:
        state_file = self._get_state_file()
        state: dict[str, Any] = {}
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
            except Exception:
                state = {}
        state.setdefault("gog_save_dirs", {})[game_id] = path
        try:
            with open(state_file, "w") as f:
                json.dump(state, f)
        except Exception:
            logger.exception("[GOGSync] Failed to cache resolved save dir")

    def _convert_gog_token(self) -> str | None:
        """Decrypt GOG OAuth token and write gogdl credentials config. Returns path on success."""
        token_path = Path("~/.config/unifideck/gog_token.json").expanduser()
        if not token_path.exists():
            logger.error("[GOGSync] GOG OAuth token file not found at %s", token_path)
            return None

        try:
            store = SecureTokenStore()
            with open(token_path, "rb") as f:
                blob = f.read()
            token = store.decrypt_payload(blob)

            gogdl_auth = {
                "46899977096215655": {
                    "access_token": token.get("access_token"),
                    "expires_in": 3600,
                    "token_type": "bearer",
                    "scope": "",
                    "refresh_token": token.get("refresh_token"),
                    "user_id": token.get("user_id", ""),
                    "session_id": "",
                    "loginTime": time.time()
                }
            }

            auth_file = Path("~/.config/unifideck/gogdl_auth.json").expanduser()
            auth_file.parent.mkdir(parents=True, exist_ok=True)
            with open(auth_file, "w") as f:
                json.dump(gogdl_auth, f)
            return str(auth_file)
        except Exception:
            logger.exception("[GOGSync] Failed to convert/decrypt GOG OAuth token")
            return None

    def _get_state_file(self) -> Path:
        return Path(self.local_save_root).parent / "cloud_sync_state.json"

    def _get_saved_timestamp(self, game_id: str) -> str:
        state_file = self._get_state_file()
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
                ts = state.get("gog", {}).get(game_id)
                if ts is not None:
                    return str(ts)
            except Exception:
                logger.exception("[GOGSync] Failed to read cloud_sync_state")
        return "0"

    def _save_timestamp(self, game_id: str, timestamp: str) -> None:
        state_file = self._get_state_file()
        state: dict[str, Any] = {}
        if state_file.exists():
            try:
                with open(state_file) as f:
                    state = json.load(f)
            except Exception as e:
                logger.debug("[GOGSync] state read failed (will recreate): %s", e)

        state.setdefault("gog", {})[game_id] = timestamp
        try:
            with open(state_file, "w") as f:
                json.dump(state, f)
        except Exception:
            logger.exception("[GOGSync] Failed to write cloud_sync_state")

    @staticmethod
    def _clear_save_dir(local_dir: str) -> None:
        """Empty a save dir (keep the dir itself) for a clean gogdl download.

        gogdl save-sync has no ``--force-download``; with a non-empty local dir
        it flags a "conflict" and skips cloud-only files. Clearing first makes
        it see an empty dir and pull everything. Caller MUST snapshot_backup
        first — this is destructive to the local copy.
        """
        try:
            for child in Path(local_dir).iterdir():
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
        except Exception as e:
            logger.warning("[GOGSync] failed to clear save dir %s: %s", local_dir, e)

    async def _do_sync_down(
        self, game_id: str, local_dir: str, force: bool,
    ) -> bool:
        """Pull GOG cloud saves into ``local_dir`` (base did save-dir+snapshot).

        With ``force`` (explicit "Use Cloud"), pull a full copy (ts=0) even
        when local saves exist — gogdl otherwise treats a recent last-sync
        timestamp as "already synced" and downloads nothing.
        """
        auth_file = self._convert_gog_token()
        if not auth_file:
            logger.error("[GOGSync] Cannot sync down: GOG credentials conversion failed")
            return False

        ts = self._get_saved_timestamp(game_id)
        # A CLEAN full pull is needed when the user explicitly chose "Use
        # Cloud" (force), OR when the local dir holds no REAL saves yet — only
        # the settings/empty ``gamesaves`` the game writes on first launch, or a
        # freshly-created prefix. Two things matter here:
        #   1. ts=0 so gogdl doesn't assume "already synced" and skip.
        #   2. CLEARING the local dir — gogdl save-sync has no --force-download;
        #      with a non-empty local it flags a "conflict" and SKIPS cloud-only
        #      files (e.g. checkpoint saves), so the actual saves never arrive
        #      (Load Game stays empty). Clearing makes gogdl see an empty dir
        #      and pull everything. Recoverable: we snapshot_backup'd above.
        clean_pull = force or not safety.has_save_data(local_dir)
        if clean_pull:
            if ts != "0":
                logger.info("[GOGSync] Clean pull (ts=0) for %s", game_id)
                ts = "0"
            self._clear_save_dir(local_dir)
        cmd = [
            self.gogdl_bin,
            "--auth-config-path", auth_file,
            "save-sync",
            local_dir,
            game_id,
            "--os", "windows",
            "--ts", ts,
            "--skip-upload"
        ]
        logger.info("[GOGSync] Running sync_down: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    "[GOGSync] sync_down failed with code %d: %s",
                    proc.returncode, stderr.decode()
                )
                return False

            # Parse timestamp from stdout
            stdout_str = stdout.decode().strip()
            match = re.search(r"(\d+\.\d+)", stdout_str)
            if match:
                new_ts = match.group(1)
                self._save_timestamp(game_id, new_ts)
                logger.info("[GOGSync] Updated GOG timestamp to %s", new_ts)

            logger.info("[GOGSync] sync_down completed successfully for %s", game_id)
            return True
        except Exception:
            logger.exception("[GOGSync] Error during sync_down for %s", game_id)
            return False

    async def _do_sync_up(self, game_id: str, local_dir: str) -> bool:
        """Push local saves to GOG cloud (base did save-dir+guard+assert)."""
        auth_file = self._convert_gog_token()
        if not auth_file:
            logger.error("[GOGSync] Cannot sync up: GOG credentials conversion failed")
            return False

        ts = self._get_saved_timestamp(game_id)
        cmd = [
            self.gogdl_bin,
            "--auth-config-path", auth_file,
            "save-sync",
            local_dir,
            game_id,
            "--os", "windows",
            "--ts", ts,
            "--skip-download"
        ]
        logger.info("[GOGSync] Running sync_up: %s", " ".join(cmd))

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    "[GOGSync] sync_up failed with code %d: %s",
                    proc.returncode, stderr.decode()
                )
                return False

            # Parse timestamp from stdout
            stdout_str = stdout.decode().strip()
            match = re.search(r"(\d+\.\d+)", stdout_str)
            if match:
                new_ts = match.group(1)
                self._save_timestamp(game_id, new_ts)
                logger.info("[GOGSync] Updated GOG timestamp to %s", new_ts)

            logger.info("[GOGSync] sync_up completed successfully for %s", game_id)
            return True
        except Exception:
            logger.exception("[GOGSync] Error during sync_up for %s", game_id)
            return False
