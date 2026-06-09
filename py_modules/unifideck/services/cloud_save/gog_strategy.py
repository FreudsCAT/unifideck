import os
import re
import time
import json
import logging
import asyncio
import subprocess
from pathlib import Path

from unifideck.services.cloud_save import safety
from unifideck.services.cloud_save.strategy_base import CloudSaveStrategy
from unifideck.security.secure_token_store import SecureTokenStore
from unifideck.launcher.proton.infrastructure.prefix_layout import resolve_drive_c
from unifideck.core.net.ssl_helpers import ssl_ctx_permissive

logger = logging.getLogger(__name__)

# GOG cloud-save location templates use path variables like
# ``<?DOCUMENTS?>\\The Witcher 3``. Each variable maps to a folder under
# the Wine prefix's ``drive_c/users/steamuser``. Mirrors GOG Galaxy /
# Heroic so resolved paths match where the game actually reads saves.
_GOG_PATH_VARS: dict[str, tuple[str, ...]] = {
    "DOCUMENTS": ("Documents",),
    "SAVED_GAMES": ("Saved Games",),
    "APPLICATION_DATA_LOCAL": ("AppData", "Local"),
    "APPLICATION_DATA_LOCALLOW": ("AppData", "LocalLow"),
    "APPLICATION_DATA_ROAMING": ("AppData", "Roaming"),
}


def _resolve_gog_location(template: str, drive_c: Path) -> Path | None:
    """Resolve a GOG cloud-save location template against a Wine prefix.

    Maps the leading ``<?VAR?>`` to its folder under
    ``drive_c/users/steamuser`` and appends the remainder (with Windows
    backslashes normalised). Returns None for an unrecognised variable.
    """
    match = re.match(r"<\?([A-Z_]+)\?>(.*)", template.strip())
    if not match:
        return None
    var, rest = match.group(1), match.group(2)
    parts = _GOG_PATH_VARS.get(var)
    if parts is None:
        return None
    base = drive_c / "users" / "steamuser"
    for part in parts:
        base = base / part
    rest = rest.replace("\\", "/").strip("/")
    return base / rest if rest else base

class GOGCloudSaveStrategy(CloudSaveStrategy):
    """Cloud save strategy for GOG games using gogdl."""

    def __init__(self, local_save_root: str, config=None) -> None:
        self.local_save_root = local_save_root
        self.config = config
        # In-process cache of resolved save dirs (get_local_save_dir is
        # called several times per launch; the network resolution must
        # only happen once).
        self._cached_save_dir: dict[str, str] = {}

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

    def get_local_save_dir(self, game_id: str) -> str | None:
        """Resolve the GOG game's local save directory.

        Resolution order (first hit wins):
          1. Explicit ``games.<id>.save_path`` config override.
          2. **GOG cloud-save metadata** (authoritative): the location
             template from ``remote-config.gog.com`` resolved against the
             game's Wine prefix — i.e. where the game actually reads/writes
             saves. This is what Galaxy/Heroic use.
          3. Heuristic title-match of an existing folder in the prefix.
          4. A local fallback dir (last resort — the game won't read from
             here, but cloud files aren't lost).
        """
        if self.config:
            configured = self.config.get(f"games.{game_id}.save_path")
            if configured:
                return str(configured)

        prefix_root = Path(self.local_save_root).parent / "prefixes" / game_id
        drive_c = resolve_drive_c(prefix_root)

        if drive_c:
            # 2. Authoritative: resolve from GOG's cloud-save config.
            meta_dir = self._resolve_save_dir_from_metadata(game_id, drive_c)
            if meta_dir:
                return meta_dir

            # 3. Heuristic: match an existing folder by game title.
            game_title = (
                self.config.get(f"games.{game_id}.title") or ""
            ) if self.config else ""
            if game_title:
                safe_title = re.sub(r'[^a-zA-Z0-9]', '', game_title).lower()
                for candidate in (
                    drive_c / "users" / "steamuser" / "Saved Games",
                    drive_c / "users" / "steamuser" / "Documents",
                    drive_c / "users" / "steamuser" / "AppData" / "Local",
                    drive_c / "users" / "steamuser" / "AppData" / "Roaming",
                ):
                    if not candidate.is_dir():
                        continue
                    for child in candidate.iterdir():
                        if not child.is_dir():
                            continue
                        child_name = re.sub(r'[^a-zA-Z0-9]', '', child.name).lower()
                        if safe_title in child_name or child_name in safe_title:
                            logger.info("[GOGSync] Auto-detected save dir via title match: %s", child)
                            return str(child)

        # 4. Fallback to local save root subdirectory.
        default_dir = Path(self.local_save_root) / "gog" / game_id
        logger.info("[GOGSync] Using default fallback save dir: %s", default_dir)
        return str(default_dir)

    # ── GOG cloud-save location resolution (from GOG metadata) ───────
    _BUILDS_URL = (
        "https://content-system.gog.com/products/{game_id}"
        "/os/windows/builds?generation=2"
    )
    _REMOTE_CONFIG_URL = (
        "https://remote-config.gog.com/components/galaxy_client/"
        "clients/{client_id}?component_version=2.0.45"
    )

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
        cached = self._cached_save_dir.get(game_id) or self._read_cached_save_dir(game_id)
        if cached:
            self._cached_save_dir[game_id] = cached
            return cached
        try:
            client_id = self._fetch_gog_client_id(game_id)
            if not client_id:
                return None
            template = self._fetch_gog_save_location(client_id)
            if not template:
                return None
            resolved = _resolve_gog_location(template, drive_c)
            if resolved is None:
                logger.warning(
                    "[GOGSync] Unrecognised GOG save-location template: %s",
                    template,
                )
                return None
            resolved.mkdir(parents=True, exist_ok=True)
            path = str(resolved)
            self._cached_save_dir[game_id] = path
            self._write_cached_save_dir(game_id, path)
            logger.info(
                "[GOGSync] Resolved save dir from GOG metadata: %s "
                "(template=%s)", path, template,
            )
            return path
        except Exception as e:
            logger.warning(
                "[GOGSync] GOG metadata save-dir resolution failed for %s: %s",
                game_id, e,
            )
            return None

    def _fetch_gog_client_id(self, game_id: str) -> str | None:
        builds = self._http_json(self._BUILDS_URL.format(game_id=game_id))
        items = builds.get("items") if isinstance(builds, dict) else None
        if not items:
            return None
        link = items[0].get("link")
        if not link:
            return None
        manifest = self._http_json(link, decompress=True)
        cid = manifest.get("clientId") if isinstance(manifest, dict) else None
        return str(cid) if cid else None

    def _fetch_gog_save_location(self, client_id: str) -> str | None:
        cfg = self._http_json(self._REMOTE_CONFIG_URL.format(client_id=client_id))
        try:
            locations = cfg["content"]["Windows"]["cloudStorage"]["locations"]
        except (KeyError, TypeError):
            return None
        return locations[0].get("location") if locations else None

    @staticmethod
    def _http_json(url: str, decompress: bool = False) -> dict:
        """GET ``url`` and parse JSON, using the permissive SSL context.

        GOG's endpoints trip the Deck's outdated CA store, so we reuse the
        same permissive context the GOG store HTTP path uses. The
        content-system manifest is zlib/gzip-compressed — try the common
        decoders before parsing.
        """
        import urllib.request
        import gzip
        import zlib
        ctx = ssl_ctx_permissive("GOG cloud-save config — outdated Deck cert store")
        req = urllib.request.Request(
            url, headers={"User-Agent": "GalaxyClient/2.0.45"},
        )
        with urllib.request.urlopen(req, timeout=8, context=ctx) as resp:
            raw = resp.read()
        if decompress:
            for decoder in (
                zlib.decompress,
                lambda b: zlib.decompress(b, 16 + zlib.MAX_WBITS),
                gzip.decompress,
                lambda b: b,
            ):
                try:
                    raw = decoder(raw)
                    break
                except Exception:
                    continue
        return json.loads(raw)

    def _read_cached_save_dir(self, game_id: str) -> str | None:
        state_file = self._get_state_file()
        if not state_file.exists():
            return None
        try:
            with open(state_file) as f:
                return json.load(f).get("gog_save_dirs", {}).get(game_id)
        except Exception:
            return None

    def _write_cached_save_dir(self, game_id: str, path: str) -> None:
        state_file = self._get_state_file()
        state: dict = {}
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
        except Exception as e:
            logger.error("[GOGSync] Failed to cache resolved save dir: %s", e)

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
        except Exception as e:
            logger.exception("[GOGSync] Failed to convert/decrypt GOG OAuth token: %s", e)
            return None

    def _get_state_file(self) -> Path:
        return Path(self.local_save_root).parent / "cloud_sync_state.json"

    def _get_saved_timestamp(self, game_id: str) -> str:
        state_file = self._get_state_file()
        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
                ts = state.get("gog", {}).get(game_id)
                if ts is not None:
                    return str(ts)
            except Exception as e:
                logger.error("[GOGSync] Failed to read cloud_sync_state: %s", e)
        return "0"

    def _save_timestamp(self, game_id: str, timestamp: str) -> None:
        state_file = self._get_state_file()
        state = {}
        if state_file.exists():
            try:
                with open(state_file, "r") as f:
                    state = json.load(f)
            except Exception:
                pass
        
        state.setdefault("gog", {})[game_id] = timestamp
        try:
            with open(state_file, "w") as f:
                json.dump(state, f)
        except Exception as e:
            logger.error("[GOGSync] Failed to write cloud_sync_state: %s", e)

    async def sync_down(self, game_id: str) -> bool:
        """Pull GOG cloud saves to local save directory."""
        auth_file = self._convert_gog_token()
        if not auth_file:
            logger.error("[GOGSync] Cannot sync down: GOG credentials conversion failed")
            return False

        local_dir = self.get_local_save_dir(game_id)
        if not local_dir:
            logger.error("[GOGSync] Cannot sync down: Local save dir not resolved")
            return False

        os.makedirs(local_dir, exist_ok=True)
        # Snapshot whatever's there before we pull — a bad/destructive
        # download must always be recoverable from a local backup.
        safety.snapshot_backup(local_dir, "gog", game_id)

        ts = self._get_saved_timestamp(game_id)
        # Self-heal: if the local copy has no save files (fresh install,
        # or the Wine prefix was reset/recreated), a stale "last sync"
        # timestamp makes gogdl assume everything is already synced and it
        # downloads NOTHING — leaving the game with no saves (Load Game
        # empty). Force a full pull (ts=0) whenever there are no local saves
        # so cloud saves are always restored into a fresh prefix.
        if ts != "0" and not safety.has_save_data(local_dir):
            logger.info(
                "[GOGSync] Local save dir empty but ts=%s — forcing full "
                "re-download (ts=0) to restore cloud saves", ts,
            )
            ts = "0"
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
            match = re.search(r'(\d+\.\d+)', stdout_str)
            if match:
                new_ts = match.group(1)
                self._save_timestamp(game_id, new_ts)
                logger.info("[GOGSync] Updated GOG timestamp to %s", new_ts)

            logger.info("[GOGSync] sync_down completed successfully for %s", game_id)
            return True
        except Exception as e:
            logger.exception("[GOGSync] Error during sync_down for %s: %s", game_id, e)
            return False

    async def sync_up(self, game_id: str) -> bool:
        """Push local saves to GOG cloud."""
        auth_file = self._convert_gog_token()
        if not auth_file:
            logger.error("[GOGSync] Cannot sync up: GOG credentials conversion failed")
            return False

        local_dir = self.get_local_save_dir(game_id)
        if not local_dir:
            logger.error("[GOGSync] Cannot sync up: Local save dir not resolved")
            return False

        # Guard against wiping the cloud copy. gogdl's save-sync reconciles
        # deletions: uploading a state that's MISSING saves makes it delete
        # them from the cloud too. ``guard_before_upload`` snapshots a local
        # backup and raises SaveConflictError when the local copy has no
        # real save data or regressed vs the last-sync manifest — the
        # service turns that into a user-facing conflict instead of a wipe.
        safety.guard_before_upload(local_dir, "gog", game_id)

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

        # Final hard gate: NEVER invoke the destructive push for an empty /
        # settings-only dir, regardless of how we got here. Uploading
        # nothing wipes the cloud — that must be impossible.
        safety.assert_has_saves(local_dir, "gog", game_id)

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
            match = re.search(r'(\d+\.\d+)', stdout_str)
            if match:
                new_ts = match.group(1)
                self._save_timestamp(game_id, new_ts)
                logger.info("[GOGSync] Updated GOG timestamp to %s", new_ts)

            logger.info("[GOGSync] sync_up completed successfully for %s", game_id)
            return True
        except Exception as e:
            logger.exception("[GOGSync] Error during sync_up for %s: %s", game_id, e)
            return False
