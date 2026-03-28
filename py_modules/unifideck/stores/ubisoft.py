"""
Ubisoft Connect Store Connector

Main store adapter implementing the Store ABC for Ubisoft Connect.
Uses UPC-native auth and local binary cache parsing for library discovery.
Delegates downloads/installs/launches to upc.exe via uplay:// protocol.
"""
import asyncio
import glob
import hashlib
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Set, Tuple

from .base import Store, Game
from .ubisoft_api import UbisoftAPIClient

logger = logging.getLogger(__name__)

# ============================================================================
# Paths
# ============================================================================

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
ID_MAP_FILE = os.path.join(DATA_DIR, "ubisoft_id_map.json")
VISIBLE_GAMES_FILE = os.path.join(DATA_DIR, "ubisoft_visible_games.json")
SHORTCUTS_REGISTRY_PATH = os.path.join(DATA_DIR, "shortcuts_registry.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
TEMPLATE_DIR = os.path.join(PREFIXES_DIR, ".template")
AUTH_PREFIX_DIR = os.path.join(PREFIXES_DIR, ".upc-auth")
AUTH_SHORTCUT_STORE_ID = "ubisoft:upc-auth"
AUTH_SHORTCUT_LAUNCH_WAIT_MS = 1500
INSTALLER_CACHE_DIR = os.path.join(DATA_DIR, "ubisoft_installer_cache")
INSTALLER_FILENAME = "UbisoftConnectInstaller.exe"
INSTALLER_URL = "https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe"
BOOTSTRAP_MARKER = "unifideck_ubisoft_bootstrap.marker"
DEFAULT_INSTALL_BASE = os.path.expanduser("~/Games/Ubisoft")

# Static game ID database (community-maintained mapping of numeric IDs to names)
GAME_ID_DB_URL = "https://raw.githubusercontent.com/iArtorias/ubisoft_game_ids/main/UBI_GAMES.txt"
GAME_ID_DB_FILE = os.path.join(DATA_DIR, "ubisoft_game_db.txt")
GAME_ID_DB_MAX_AGE = 7 * 24 * 3600  # Refresh weekly

# UPC session token captured after first manual login (persists across runs)
UPC_SESSION_FILE = os.path.join(DATA_DIR, "ubisoft_upc_session.txt")

# UPC credential files synced alongside the session token.
# These are DPAPI-encrypted — they can only be shared between prefixes that
# share the same Wine MachineGuid (enforced by _ensure_auth_prefix).
_UPC_CREDENTIAL_FILES = ("ConnectSecureStorage.dat", "user.dat")
_UPC_LOCAL_SUBDIR = os.path.join("AppData", "Local", "Ubisoft Game Launcher")
_UPC_AUTH_CACHE_ARTIFACTS = (
    "settings.yaml",
    os.path.join("cache", "configuration"),
    os.path.join("cache", "settings"),
    os.path.join("cache", "ulcf"),
    os.path.join("cache", "http2", "Default", "Network"),
    os.path.join("cache", "http2", "Default", "Local Storage"),
    os.path.join("cache", "http2", "Default", "IndexedDB"),
    os.path.join("cache", "http2", "Default", "Preferences"),
    os.path.join("cache", "http2", "Default", "Session Storage"),
    os.path.join("cache", "ownership"),
)

# Wine system user directories that should never contain UPC settings
_WINE_SYSTEM_USERS = {"Public", "All Users", "Default", "Default User"}


def _iter_prefix_user_homes(prefix_path: str, pfx_first: bool = False):
    """Yield (prefix_root, user_home) for all real user dirs across both layouts.

    When pfx_first is True, yield pfx/ layout before bare drive_c/ layout.
    UPC writes to pfx/ so this ensures the freshest files are found first.
    """
    roots = [prefix_path, os.path.join(prefix_path, "pfx")]
    if pfx_first:
        roots = list(reversed(roots))
    for prefix_root in roots:
        users_dir = os.path.join(prefix_root, "drive_c", "users")
        if not os.path.isdir(users_dir):
            continue
        try:
            entries = os.listdir(users_dir)
        except OSError:
            continue
        for entry in entries:
            if entry in _WINE_SYSTEM_USERS:
                continue
            user_home = os.path.join(users_dir, entry)
            if os.path.isdir(user_home):
                yield prefix_root, user_home


# SD card install path (mirrors download manager's StorageLocation.SDCARD)
SDCARD_INSTALL_BASE = "/run/media/mmcblk0p1/Games/Ubisoft"

# UPC paths within a Wine prefix
UPC_RELATIVE_PATH = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"
# UbisoftConnect.exe is the registered uplay:// protocol handler — use this for install URLs
UPC_CONNECT_RELATIVE_PATH = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/UbisoftConnect.exe"
CONFIGURATIONS_RELATIVE_PATH = (
    "drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher/"
    "cache/configuration/configurations"
)
# Ubisoft Connect localStorage (contains ubisoftConnectGameId and other metadata)
LOCALSTORAGE_RELATIVE_PATH = (
    "drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher/"
    "cache/http2/Default/Local Storage"
)
# Ownership binary (contains ALL owned game IDs, including free/claimed)
OWNERSHIP_RELATIVE_PATH = (
    "drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher/"
    "cache/ownership"
)


class UbisoftConnector(Store):
    """
    Ubisoft Connect store connector.

    Uses UPC-native auth (user logs in through Ubisoft Connect directly)
    and local binary cache parsing for library discovery.
    Delegates downloads/installs/launches to upc.exe via uplay:// protocol.
    """

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        self.plugin_dir = plugin_dir
        self.plugin_instance = plugin_instance
        self.api = UbisoftAPIClient()
        self._id_map_cache: Dict[str, Dict[str, Any]] = {}
        self._load_id_map()
        self._template_task: Optional[asyncio.Task] = None
        self._auth_assets_task: Optional[asyncio.Task] = None
        self._auth_assets_lock = asyncio.Lock()
        self._auth_monitor_task: Optional[asyncio.Task] = None
        self._auth_session_captured = False
        self._active_install_pids: Dict[str, int] = {}  # game_id → PID for cancel support

    # ========================================================================
    # Store ABC Implementation
    # ========================================================================

    @property
    def store_name(self) -> str:
        return "ubisoft"

    async def is_available(self) -> bool:
        """Check if authenticated by looking for UPC credentials in the auth prefix."""
        if self._has_valid_credentials(AUTH_PREFIX_DIR):
            return True
        return False

    def _has_valid_credentials(self, prefix_path: str) -> bool:
        """Check if a prefix has valid ConnectSecureStorage.dat (>100 bytes)."""
        if not os.path.isdir(prefix_path):
            return False
        for _, user_home in _iter_prefix_user_homes(prefix_path, pfx_first=True):
            css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
            if os.path.isfile(css) and os.path.getsize(css) > 100:
                return True
        return False

    def _get_credential_mtime(self, prefix_path: str) -> float:
        """Get most recent mtime of ConnectSecureStorage.dat in a prefix."""
        best = 0.0
        for _, user_home in _iter_prefix_user_homes(prefix_path, pfx_first=True):
            css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
            if os.path.isfile(css) and os.path.getsize(css) > 100:
                mtime = os.path.getmtime(css)
                if mtime > best:
                    best = mtime
        return best

    async def start_auth(self) -> Dict[str, Any]:
        """Return auth prompt config for UPC-native login.

        Signals the frontend to launch Ubisoft Connect directly for
        the user to log in, rather than using API-based credentials.
        """
        return {
            "success": True,
            "auth_type": "upc_launch",
            "message": "Sign in through Ubisoft Connect",
        }

    async def complete_auth(self, auth_data: str) -> Dict[str, Any]:
        """No-op — Ubisoft uses UPC-native auth, not API credentials."""
        return {"success": False, "error": "Use UPC-native login instead"}

    async def logout(self) -> Dict[str, Any]:
        """Logout from Ubisoft Connect, clearing all auth state."""
        # Clear legacy API token file if it exists
        self.api.logout()

        # Clear UPC session file
        if os.path.isfile(UPC_SESSION_FILE):
            try:
                os.remove(UPC_SESSION_FILE)
                logger.info("[Ubisoft] Deleted UPC session file")
            except Exception as e:
                logger.error(f"[Ubisoft] Failed to delete UPC session file: {e}")

        # Delete auth prefix so user can re-login fresh
        if os.path.isdir(AUTH_PREFIX_DIR):
            try:
                import shutil
                shutil.rmtree(AUTH_PREFIX_DIR)
                logger.info("[Ubisoft] Deleted auth prefix directory")
            except Exception as e:
                logger.error(f"[Ubisoft] Failed to delete auth prefix: {e}")

        return {"success": True}

    async def get_library(self) -> Optional[List[Game]]:
        """Get the user's Ubisoft game library from local UPC binary cache.

        Parses configurations + ownership binaries that UPC writes to disk
        after the user logs in via Ubisoft Connect.

        Returns:
            List of Game objects, or empty list on error.
        """
        try:
            local_games = await self._get_library_from_local_binaries()
            if local_games is None:
                logger.info("[Ubisoft] No local binary data available yet")
                return []

            logger.info(
                f"[Ubisoft] Library: {len(local_games)} games from local binaries"
            )

            installed = await self.get_installed()

            override_manifest = self._load_visible_manifest()
            if override_manifest:
                local_games = self._apply_visible_manifest_filter(
                    local_games,
                    installed,
                    override_manifest,
                    source_label="override",
                )

            # Trigger template prefix creation as background task if needed
            if local_games and not self._template_exists():
                self._queue_template_creation()

            return local_games
        except Exception as e:
            logger.exception(f"[Ubisoft] Error fetching library: {e}")
            return []

    async def _get_library_from_local_binaries(self) -> Optional[List[Game]]:
        """Build game library from UPC's local binary cache files.

        Parses the configurations binary (game metadata) and ownership binary
        (owned IDs), cross-references them, and applies DLC/junk filtering.

        Returns:
            List of Game objects, or None if binary files not found.
        """
        from .ubisoft_parser import parse_configurations, parse_ownership

        # Find configurations binary
        cfg_path = None
        for prefix_dir in (AUTH_PREFIX_DIR, TEMPLATE_DIR):
            cfg_path = self._find_configurations(prefix_dir)
            if cfg_path:
                break

        if not cfg_path:
            logger.info("[Ubisoft] No configurations binary found")
            return None

        # Parse configurations
        configs = parse_configurations(cfg_path)
        if not configs:
            logger.warning("[Ubisoft] Configurations binary parsed but empty")
            return None

        # Find and parse ownership binary
        ownership_path, user_id = self._discover_ownership_file()
        owned_set: Optional[set] = None
        if ownership_path:
            owned_ids = parse_ownership(ownership_path)
            owned_set = set(owned_ids)
            logger.info(
                f"[Ubisoft] Ownership: {len(owned_set)} unique IDs "
                f"(userId={user_id[:8]}...)"
            )

        # Build install_id/launch_id -> config lookup
        config_by_id: Dict[int, Any] = {}
        for cfg in configs:
            config_by_id[cfg.install_id] = cfg
            if cfg.launch_id and cfg.launch_id != cfg.install_id:
                config_by_id[cfg.launch_id] = cfg

        # Cross-reference: keep only owned games
        if owned_set is not None:
            matched_configs = []
            for oid in owned_set:
                cfg = config_by_id.get(oid)
                if cfg and cfg.name:
                    matched_configs.append(cfg)
        else:
            # No ownership binary — show all config games (conservative)
            matched_configs = [c for c in configs if c.name]
            logger.info(
                "[Ubisoft] No ownership binary — using all "
                f"{len(matched_configs)} config entries"
            )

        # Get installed games for status
        installed = await self.get_installed()

        # Filter and deduplicate
        games: List[Game] = []
        seen_names: Set[str] = set()

        for cfg in sorted(matched_configs, key=lambda c: c.name.lower()):
            title = self._clean_launcher_title(cfg.name)
            if self._should_skip_launcher_title(title):
                continue

            norm_name = self._normalize_for_matching(title)
            if norm_name in seen_names:
                continue
            seen_names.add(norm_name)

            # Use space_id as game ID (preferred for SteamGridDB), fall back
            # to install_id
            game_id = cfg.space_id if cfg.space_id else str(cfg.install_id)

            is_installed = (
                game_id in installed
                or cfg.space_id in installed
            )
            install_meta = (
                installed.get(game_id, {})
                or installed.get(cfg.space_id, {})
            )

            game = Game(
                id=game_id,
                title=title,
                store="ubisoft",
                is_installed=is_installed,
                ownership_type="owned",
                install_path=install_meta.get("install_path") if install_meta else None,
                executable=install_meta.get("executable") if install_meta else None,
            )
            games.append(game)

            # Update id_map cache with accurate IDs from parser
            self._id_map_cache[game_id] = {
                "install_id": str(cfg.install_id),
                "launch_id": str(cfg.launch_id),
                "name": title,
                "executable": cfg.executable,
                "game_identifier": cfg.game_identifier,
                "source": "local_binary",
            }

        if self._id_map_cache:
            self._save_id_map()

        logger.info(
            f"[Ubisoft] Local binary library: {len(games)} games "
            f"(from {len(matched_configs)} matched configs)"
        )
        return games

    def _discover_ownership_file(self) -> tuple:
        """Find ownership binary and discover userId from filename.

        Returns:
            (filepath, userId) or ("", "") if not found.
        """
        for prefix_dir in (AUTH_PREFIX_DIR, TEMPLATE_DIR):
            for sub in ("pfx", ""):
                if sub:
                    ownership_dir = os.path.join(
                        prefix_dir, sub, OWNERSHIP_RELATIVE_PATH
                    )
                else:
                    ownership_dir = os.path.join(
                        prefix_dir, OWNERSHIP_RELATIVE_PATH
                    )
                if os.path.isdir(ownership_dir):
                    entries = [
                        e for e in os.listdir(ownership_dir)
                        if os.path.isfile(os.path.join(ownership_dir, e))
                    ]
                    if entries:
                        user_id = entries[0]
                        return os.path.join(ownership_dir, user_id), user_id
        return "", ""

    async def get_installed(self) -> Dict[str, Any]:
        """
        Get installed Ubisoft games by scanning per-game prefixes.

        Returns:
            Dict mapping space_id to installation metadata.
        """
        installed = {}

        try:
            if not os.path.exists(PREFIXES_DIR):
                return installed

            for entry in os.listdir(PREFIXES_DIR):
                if entry.startswith("."):
                    continue  # Skip .template and hidden dirs

                prefix_path = os.path.join(PREFIXES_DIR, entry)
                if not os.path.isdir(prefix_path):
                    continue

                # Check bootstrap marker
                marker_path = os.path.join(prefix_path, BOOTSTRAP_MARKER)
                if not os.path.exists(marker_path):
                    continue

                # Check if game is actually installed
                game_info = self._detect_installed_game(entry, prefix_path)
                if game_info:
                    installed[entry] = game_info

                    # Auto-resolve game ID if missing from the map
                    existing = self._id_map_cache.get(entry, {})
                    has_id = existing.get("launch_id") or existing.get("ubisoftconnect_game_id")
                    if not has_id:
                        # Primary: extract from Wine registry (authoritative, local)
                        reg_id = self._extract_game_id_from_registry(prefix_path)
                        if not reg_id:
                            # Fallback: online database by exact name match
                            game_title = game_info.get("title", "")
                            reg_id = await self._lookup_game_id_by_name(game_title)
                        if reg_id:
                            self._id_map_cache[entry] = {
                                **existing,
                                "install_id": reg_id,
                                "launch_id": reg_id,
                                "ubisoftconnect_game_id": reg_id,
                                "name": game_info.get("title", ""),
                            }
                            self._save_id_map()
                            logger.info(f"[Ubisoft] Auto-resolved game ID for {entry}: {reg_id}")

        except Exception as e:
            logger.warning(f"[Ubisoft] Error scanning installed games: {e}")

        return installed

    async def get_game_size(self, game_id: str) -> Optional[int]:
        """Get game download size (best-effort, may return None)."""
        # Ubisoft doesn't expose download sizes via public API.
        # Size is discovered during download via FS monitoring.
        return None

    async def install_game(self, game_id: str, progress_callback=None, install_path: str = None) -> Dict[str, Any]:
        """
        Install a game by opening UPC in the game's per-game prefix.

        The user installs the game manually through UPC's UI while we monitor
        the filesystem for new game directories to detect completion.

        Flow:
          1. Bootstrap per-game prefix (template clone or fresh install)
          2. Inject UPC session token
          3. Open UPC in the game's prefix (user installs manually)
          4. Monitor filesystem for new game directories
          5. Capture token on exit

        Args:
            game_id: The game's space_id.
            progress_callback: Async callable(dict) for progress updates.
            install_path: Base install directory (supports SD card via download queue).
                         Defaults to ~/Games/Ubisoft if not specified.

        Returns:
            {"success": True, "install_path": ..., "executable": ..., "install_size": ...}
            or {"success": False, "error": "..."}
        """
        try:
            logger.info(f"[Ubisoft] Installing game {game_id}")

            # Step 1: Bootstrap prefix
            if not await self.bootstrap_game_prefix(game_id):
                return {"success": False, "error": "Failed to bootstrap Wine prefix"}

            prefix_path = self.get_prefix_path(game_id)
            game_name = self._get_game_name(game_id)

            # Step 2: Find UPC + umu-run
            upc_path = self._find_upc_exe(prefix_path)
            if not upc_path:
                return {"success": False, "error": "upc.exe not found in prefix"}

            umu_run = self._find_umu_run()
            if not umu_run:
                return {"success": False, "error": "umu-run not found"}

            python_bin = self._find_python()
            env = self._build_umu_env(
                prefix_path, f"umu-ubisoft-{game_id}", f"ubisoft:{game_id}"
            )

            # Step 3: Open UPC for manual install
            return await self._install_via_upc_ui(
                game_id, game_name, prefix_path, upc_path,
                umu_run, python_bin, env, progress_callback,
                install_path,
            )

        except Exception as e:
            logger.exception(f"[Ubisoft] Install error for {game_id}: {e}")
            return {"success": False, "error": str(e)}

    async def open_launcher_for_install(self, game_id: str) -> Dict[str, Any]:
        """
        Open Ubisoft Connect in the game's prefix for manual install.

        Uses UbisoftConnect.exe (the registered uplay:// protocol handler) with
        uplay://install/{ubisoftConnectGameId} to open the game's install page directly.
        Falls back to opening the launcher without a URL if ID resolution fails.
        """
        try:
            logger.info(f"[Ubisoft] open_launcher_for_install called for {game_id}")

            if not await self.bootstrap_game_prefix(game_id):
                logger.error(f"[Ubisoft] Bootstrap failed for {game_id}")
                return {"success": False, "error": "Failed to bootstrap Wine prefix"}

            prefix_path = self.get_prefix_path(game_id)
            connect_exe = self._find_connect_exe(prefix_path)
            if not connect_exe:
                # Fallback to upc.exe if UbisoftConnect.exe not found
                connect_exe = self._find_upc_exe(prefix_path)
                if not connect_exe:
                    logger.error(f"[Ubisoft] Neither UbisoftConnect.exe nor upc.exe found in {prefix_path}")
                    return {"success": False, "error": "Ubisoft Connect executable not found in prefix"}
            
            umu_run = self._find_umu_run()
            if not umu_run:
                logger.error("[Ubisoft] umu-run not found")
                return {"success": False, "error": "umu-run not found"}

            self.inject_upc_session(prefix_path)

            python_bin = self._find_python()
            env = self._build_umu_env(
                prefix_path, f"umu-ubisoft-{game_id}", f"ubisoft:{game_id}"
            )
            launch_id = self.resolve_launch_id(game_id)
            # Use uplay://install/ URL to open the game's install page directly.
            # This works best with UbisoftConnect.exe (the registered protocol handler).
            launch_url = f"uplay://install/{launch_id}" if launch_id else ""

            cmd = [python_bin, umu_run, connect_exe]
            if launch_url:
                cmd.append(launch_url)

            logger.info(f"[Ubisoft] Launch cmd: {' '.join(cmd)}")
            logger.info(f"[Ubisoft] WINEPREFIX={env.get('WINEPREFIX')} "
                        f"DISPLAY={env.get('DISPLAY')} "
                        f"PROTONPATH={env.get('PROTONPATH')} "
                        f"GAMEID={env.get('GAMEID')} "
                        f"SteamAppId={env.get('SteamAppId')} "
                        f"UMU_STEAM_GAME_ID={env.get('UMU_STEAM_GAME_ID')}")

            proc = await asyncio.create_subprocess_exec(
                *cmd,
                env=env,
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.PIPE,
            )
            logger.info(f"[Ubisoft] Subprocess spawned PID={proc.pid}")

            # Track this install session for Cancel support
            self._active_install_pids[game_id] = proc.pid
            spawned_pid = proc.pid

            async def _monitor_after_exit() -> None:
                try:
                    # Read only stderr for diagnostics; stdout is discarded
                    _, stderr = await proc.communicate()
                    rc = proc.returncode
                    logger.info(f"[Ubisoft] UPC exited (PID={spawned_pid}, rc={rc})")
                    if stderr:
                        stderr_text = stderr.decode(errors="replace")[:2000]
                        logger.info(f"[Ubisoft] UPC stderr: {stderr_text}")
                except Exception as exc:
                    logger.warning(f"[Ubisoft] Monitor error: {exc}")
                finally:
                    # Only remove tracking if this PID is still the active one
                    if self._active_install_pids.get(game_id) == spawned_pid:
                        self._active_install_pids.pop(game_id, None)

                captured = self._capture_upc_session(prefix_path)
                if captured:
                    self._propagate_upc_session_to_all_prefixes(captured)

            asyncio.create_task(_monitor_after_exit())

            # Wait briefly to detect immediate crashes
            await asyncio.sleep(2)
            if proc.returncode is not None:
                logger.error(f"[Ubisoft] UPC exited immediately (rc={proc.returncode})")
                if self._active_install_pids.get(game_id) == spawned_pid:
                    self._active_install_pids.pop(game_id, None)
                return {
                    "success": False,
                    "error": f"Ubisoft Connect exited immediately (code {proc.returncode})",
                }

            return {
                "success": True,
                "pid": proc.pid,
                "launch_url": launch_url,
            }
        except Exception as e:
            logger.exception(f"[Ubisoft] Failed to open launcher for install {game_id}: {e}")
            return {"success": False, "error": str(e)}

    def is_install_session_active(self, game_id: str) -> bool:
        """Check if an install session (UPC) is currently running for a game."""
        pid = self._active_install_pids.get(game_id)
        if pid is None:
            return False
        try:
            os.kill(pid, 0)
            return True
        except (ProcessLookupError, PermissionError):
            self._active_install_pids.pop(game_id, None)
            return False

    async def cancel_install_session(self, game_id: str) -> Dict[str, Any]:
        """Cancel a running install session by terminating UPC.

        Always captures the session from the game prefix after cancellation,
        since the bash launcher (killed by Steam's TerminateApp) cannot run
        its own post-game capture.
        """
        pid = self._active_install_pids.pop(game_id, None)
        if pid is not None:
            try:
                os.kill(pid, 15)  # SIGTERM
                logger.info(f"[Ubisoft] Sent SIGTERM to UPC PID {pid} for {game_id}")
            except ProcessLookupError:
                logger.info(f"[Ubisoft] Install process already exited for {game_id}")
            except Exception as e:
                logger.error(f"[Ubisoft] Failed to kill install PID {pid}: {e}")

        # Capture session from the game prefix — the bash launcher's
        # capture_session.py is bypassed when Steam kills the shortcut,
        # so the Python backend must handle it.
        prefix_path = self.get_prefix_path(game_id)
        if prefix_path and os.path.isdir(prefix_path):
            await asyncio.sleep(2)  # Give UPC a moment to flush state
            captured = self._capture_upc_session(prefix_path)
            if captured:
                self._propagate_upc_session_to_all_prefixes(captured)
                logger.info(f"[Ubisoft] Post-cancel capture: propagated session from {game_id}")
            else:
                # Even if token didn't change, credentials were synced by _capture_upc_session
                logger.info(f"[Ubisoft] Post-cancel capture: credentials synced for {game_id}")

        return {"success": True}

    async def _delete_tree_with_retries(
        self,
        target_path: str,
        label: str,
        retries: int = 3,
    ) -> bool:
        """Delete a directory with retries and path safety guards."""
        if not target_path:
            logger.error(f"[Ubisoft] Refusing to delete empty path for {label}")
            return False

        resolved = os.path.realpath(target_path)
        home_dir = os.path.realpath(os.path.expanduser("~"))
        protected_paths = {
            "/",
            home_dir,
            os.path.realpath(DATA_DIR),
            os.path.realpath(PREFIXES_DIR),
            os.path.realpath(DEFAULT_INSTALL_BASE),
            os.path.realpath(SDCARD_INSTALL_BASE),
        }
        if resolved in protected_paths or len(resolved.strip("/")) < 8:
            logger.error(
                f"[Ubisoft] Refusing to delete unsafe path for {label}: {resolved}"
            )
            return False

        if not os.path.isdir(resolved):
            logger.info(f"[Ubisoft] Nothing to delete for {label}: {resolved}")
            return True

        for attempt in range(1, retries + 1):
            try:
                shutil.rmtree(resolved)
                logger.info(f"[Ubisoft] Deleted {label}: {resolved}")
                return True
            except OSError as e:
                logger.warning(
                    f"[Ubisoft] Failed deleting {label} (attempt {attempt}/{retries}): {e}"
                )
                if attempt < retries:
                    await asyncio.sleep(1.5)

        logger.error(f"[Ubisoft] Failed to delete {label} after {retries} attempts: {resolved}")
        return False

    async def uninstall_game(self, game_id: str, delete_prefix: bool = False) -> Dict[str, Any]:
        """
        Uninstall a game.

        Tries uplay://uninstall protocol first, falls back to direct deletion.

        Args:
            game_id: The game's space_id.
            delete_prefix: If True, also delete the game's entire Ubisoft prefix.

        Returns:
            {"success": True, "prefix_deleted": bool} or {"success": False, "error": "..."}
        """
        try:
            logger.info(f"[Ubisoft] Uninstalling game {game_id} (delete_prefix={delete_prefix})")

            prefix_path = self.get_prefix_path(game_id)
            game_info = self._detect_installed_game(game_id, prefix_path)
            install_path = game_info.get("install_path") if game_info else None

            # Try protocol-based uninstall first
            install_id = self.resolve_install_id(game_id) or self.resolve_launch_id(game_id)
            upc_path = self._find_upc_exe(prefix_path)

            protocol_attempted = False
            if not delete_prefix and install_id and upc_path:
                try:
                    protocol_attempted = True
                    umu_run = self._find_umu_run()
                    python_bin = self._find_python()

                    if umu_run:
                        env = self._build_umu_env(
                            prefix_path, f"umu-ubisoft-{game_id}", f"ubisoft:{game_id}"
                        )

                        uninstall_url = f"uplay://uninstall/{install_id}"
                        logger.info(f"[Ubisoft] Trying protocol uninstall: {uninstall_url}")

                        proc = await asyncio.create_subprocess_exec(
                            python_bin, umu_run, upc_path, uninstall_url,
                            env=env,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=90)
                        except asyncio.TimeoutError:
                            proc.kill()
                            logger.warning("[Ubisoft] Protocol uninstall timed out, continuing with fallback deletion")
                except Exception as e:
                    logger.warning(f"[Ubisoft] Protocol uninstall failed: {e}")
            elif delete_prefix:
                logger.info("[Ubisoft] delete_prefix=True: skipping uninstall URI and deleting files directly")

            # Refresh install info after uninstall attempt
            post_uninstall_info = self._detect_installed_game(game_id, prefix_path)
            if post_uninstall_info:
                install_path = post_uninstall_info.get("install_path") or install_path

            # Fallback: direct deletion when uninstall protocol did not remove files
            if install_path and os.path.isdir(install_path):
                path_inside_prefix = os.path.realpath(install_path).startswith(
                    os.path.realpath(prefix_path) + os.sep
                )
                if not path_inside_prefix or not delete_prefix:
                    logger.info(f"[Ubisoft] Fallback deleting game directory: {install_path}")
                    deleted = await self._delete_tree_with_retries(
                        install_path, "Ubisoft game install directory"
                    )
                    if not deleted:
                        return {
                            "success": False,
                            "error": f"Failed to remove Ubisoft game directory: {install_path}",
                        }

            prefix_deleted = False
            if delete_prefix and os.path.isdir(prefix_path):
                deleted_prefix = await self._delete_tree_with_retries(
                    prefix_path, "Ubisoft game prefix"
                )
                if not deleted_prefix:
                    return {
                        "success": False,
                        "error": f"Failed to remove Ubisoft prefix: {prefix_path}",
                    }
                prefix_deleted = True

            if not prefix_deleted:
                # Clean up registry keys from remaining prefix
                self._clean_install_registry(prefix_path, install_id or "")
            else:
                # Prefix removal invalidates cached launch/install IDs for this space_id
                if game_id in self._id_map_cache:
                    self._id_map_cache.pop(game_id, None)
                    self._save_id_map()

            logger.info(
                f"[Ubisoft] Game {game_id} uninstalled "
                f"(protocol_attempted={protocol_attempted}, prefix_deleted={prefix_deleted})"
            )
            return {"success": True, "prefix_deleted": prefix_deleted}

        except Exception as e:
            logger.exception(f"[Ubisoft] Uninstall error for {game_id}: {e}")
            return {"success": False, "error": str(e)}

    async def check_for_updates(self) -> List[str]:
        """
        Check for updates (Phase 1: passive — return empty).

        Updates are handled by upc.exe at launch time. Future phases
        may add proactive checking via configurations binary comparison.
        """
        return []

    async def update_game(self, game_id: str, install_path: Optional[str] = None) -> Dict[str, Any]:
        """
        Trigger update by launching upc.exe with game context.

        UPC auto-updates at launch, so this simply starts it with the game's
        per-game prefix. Any pending patches are applied automatically.
        """
        try:
            prefix_path = self.get_prefix_path(game_id)
            upc_path = self._find_upc_exe(prefix_path)

            if not upc_path:
                return {"success": False, "error": "upc.exe not found — game may need reinstall"}

            # Inject fresh session
            self.inject_upc_session(prefix_path)

            launch_id = self.resolve_launch_id(game_id)
            if not launch_id:
                return {"success": False, "error": "Could not resolve launch_id"}

            umu_run = self._find_umu_run()
            python_bin = self._find_python()
            if not umu_run:
                return {"success": False, "error": "umu-run not found"}

            env = self._build_umu_env(
                prefix_path, f"umu-ubisoft-{game_id}", f"ubisoft:{game_id}"
            )

            # Launch UPC — it will auto-patch the game
            launch_url = f"uplay://launch/{launch_id}/0"
            logger.info(f"[Ubisoft] Triggering update via {launch_url}")

            proc = await asyncio.create_subprocess_exec(
                python_bin, umu_run, upc_path, launch_url,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Wait for UPC to finish (it will update then launch the game)
            try:
                await asyncio.wait_for(proc.wait(), timeout=14400)  # 4h max
            except asyncio.TimeoutError:
                proc.kill()

            return {"success": True}

        except Exception as e:
            logger.exception(f"[Ubisoft] Update error for {game_id}: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # Game Info Methods
    # ========================================================================

    def get_installed_game_info(self, game_id: str) -> Optional[Dict[str, Any]]:
        """
        Get installed game info synchronously.

        Args:
            game_id: The space_id of the game.

        Returns:
            Dict with space_id, executable, install_path, work_dir, title
            or None if not installed.
        """
        prefix_path = os.path.join(PREFIXES_DIR, game_id)
        if not os.path.isdir(prefix_path):
            return None

        marker_path = os.path.join(prefix_path, BOOTSTRAP_MARKER)
        if not os.path.exists(marker_path):
            return None

        info = self._detect_installed_game(game_id, prefix_path)
        if info:
            # Ensure game ID is resolved in the map (registry-based, no network)
            existing = self._id_map_cache.get(game_id, {})
            if not (existing.get("launch_id") or existing.get("ubisoftconnect_game_id")):
                reg_id = self._extract_game_id_from_registry(prefix_path)
                if reg_id:
                    self._id_map_cache[game_id] = {
                        **existing,
                        "install_id": reg_id,
                        "launch_id": reg_id,
                        "ubisoftconnect_game_id": reg_id,
                        "name": info.get("title", ""),
                    }
                    self._save_id_map()
                    logger.info(f"[Ubisoft] Auto-resolved game ID for {game_id}: {reg_id}")
        return info

    async def write_install_marker(
        self, space_id: str, install_path: str, executable: str, game_title: str = ""
    ) -> None:
        """
        Write .unifideck_ubisoft marker to game install directory.

        Written atomically (tmp + rename) after download completion.
        """
        try:
            marker_data = {
                "space_id": space_id,
                "game_title": game_title,
                "install_path": install_path,
                "executable": executable,
                "install_date": __import__("datetime").datetime.now().isoformat(),
            }

            marker_path = os.path.join(install_path, ".unifideck_ubisoft")
            tmp_path = marker_path + ".tmp"

            os.makedirs(install_path, exist_ok=True)
            with open(tmp_path, "w") as f:
                json.dump(marker_data, f, indent=2)
            os.replace(tmp_path, marker_path)

            logger.info(f"[Ubisoft] Wrote install marker for {space_id}")
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to write install marker: {e}")

    def find_game_executable(self, install_path: str) -> Optional[str]:
        """
        Find the game executable in the install directory.

        Searches for the largest .exe that isn't an installer/uninstaller.
        """
        if not install_path or not os.path.isdir(install_path):
            return None

        skip_patterns = [
            "unins", "setup", "install", "crash", "redist",
            "vcredist", "dxsetup", "dotnet", "upc", "uplay",
        ]
        exe_candidates = []

        for pattern in ["*.exe", "**/*.exe"]:
            for exe_path in glob.glob(os.path.join(install_path, pattern), recursive=True):
                basename = os.path.basename(exe_path).lower()
                if any(skip in basename for skip in skip_patterns):
                    continue
                try:
                    size = os.path.getsize(exe_path)
                    exe_candidates.append((exe_path, size))
                except OSError:
                    continue

        if exe_candidates:
            exe_candidates.sort(key=lambda x: x[1], reverse=True)
            result = exe_candidates[0][0]
            logger.info(
                f"[Ubisoft] Found executable ({exe_candidates[0][1] / 1024 / 1024:.1f}MB): {result}"
            )
            return result

        logger.warning(f"[Ubisoft] No executable found in {install_path}")
        return None

    def get_game_official_url(self, game_id: str) -> Optional[str]:
        """Get the Ubisoft store URL for a game."""
        return f"https://store.ubisoft.com/game?pid={game_id}"

    # ========================================================================
    # ID Map (spaceId <-> installId/launchId)
    # ========================================================================

    def _load_id_map(self) -> None:
        """Load the spaceId-to-installId/launchId map from disk."""
        try:
            if os.path.exists(ID_MAP_FILE):
                with open(ID_MAP_FILE, "r") as f:
                    self._id_map_cache = json.load(f)
                logger.info(f"[Ubisoft] Loaded ID map ({len(self._id_map_cache)} entries)")
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to load ID map: {e}")
            self._id_map_cache = {}

    def _save_id_map(self) -> None:
        """Save the ID map to disk atomically."""
        try:
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp_path = ID_MAP_FILE + ".tmp"
            with open(tmp_path, "w") as f:
                json.dump(self._id_map_cache, f, indent=2)
            os.replace(tmp_path, ID_MAP_FILE)
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to save ID map: {e}")

    def _load_visible_manifest(self) -> List[Dict[str, Any]]:
        """Load a locally-resolved Ubisoft visible-library manifest.

        This file is optional and lives under the Unifideck data directory.
        When present, it acts as the final allowlist for which Ubisoft titles
        should appear in the UI on this machine.
        """
        if not os.path.isfile(VISIBLE_GAMES_FILE):
            return []

        try:
            with open(VISIBLE_GAMES_FILE, "r", encoding="utf-8") as f:
                payload = json.load(f)
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to load visible manifest: {e}")
            return []

        raw_games: Any
        if isinstance(payload, dict):
            raw_games = payload.get("games", [])
        else:
            raw_games = payload

        manifest: List[Dict[str, Any]] = []
        for raw in raw_games or []:
            if not isinstance(raw, dict):
                continue

            title = str(raw.get("title") or raw.get("name") or "").strip()
            if not title:
                continue

            space_id = str(raw.get("space_id") or raw.get("spaceId") or "").strip()
            install_id = str(raw.get("install_id") or "").strip()
            launch_id = str(raw.get("launch_id") or install_id or "").strip()
            ubisoftconnect_game_id = str(
                raw.get("ubisoftconnect_game_id")
                or raw.get("product_id")
                or ""
            ).strip()
            cover_image = str(
                raw.get("cover_image")
                or raw.get("coverUrl")
                or raw.get("thumb_url")
                or ""
            ).strip()

            manifest.append({
                "title": title,
                "space_id": space_id,
                "install_id": install_id,
                "launch_id": launch_id,
                "ubisoftconnect_game_id": ubisoftconnect_game_id,
                "cover_image": cover_image,
                "ownership_type": str(raw.get("ownership_type") or "owned").strip(),
                "source": str(raw.get("source") or "visible_manifest").strip(),
            })

        return manifest

    @staticmethod
    def _clean_launcher_title(title: str) -> str:
        """Normalize mojibake-heavy launcher/cache titles into readable text."""
        return (
            (title or "")
            .strip()
            .strip('"')
            .strip("'")
            .replace("Â®", "®")
            .replace("â¢", "™")
            .replace("â„¢", "™")
            .replace("â", "’")
            .replace("Â", "")
        )

    @staticmethod
    def _is_launcher_placeholder_title(title: str) -> bool:
        """Return True when a launcher/cache title is clearly a placeholder."""
        cleaned = UbisoftConnector._clean_launcher_title(title)
        if not cleaned:
            return True
        normalized = UbisoftConnector._normalize_for_matching(cleaned)
        return bool(
            re.fullmatch(r"(l\d+|[A-Z0-9_]+)", cleaned)
            or normalized in {"a ubisoft game"}
        )

    def _should_skip_launcher_title(self, title: str) -> bool:
        """Reject non-library launcher config entries such as DLC, trials, or packs."""
        cleaned = self._clean_launcher_title(title)
        if not cleaned or len(cleaned.strip()) <= 2:
            return True
        if self._is_launcher_placeholder_title(cleaned):
            return True
        if re.search(r"\[STEAM\]|\[Uplay", cleaned, re.IGNORECASE):
            return True
        if re.search(
            r"\b(test\b|beta|alpha|closed|preorder|pre-order|promotion|"
            r"internal|dev/qc|pts|test server|demo|trial)\b",
            cleaned,
            re.IGNORECASE,
        ):
            return True
        if re.search(r"[\u0400-\u04FF]", cleaned):
            return True
        if re.search(
            r"\b(dlc|season pass|expansion|pack|bonus|soundtrack|art ?book|"
            r"skins?|outfit|costume|weapon|map|mission|episode|revolver|"
            r"kukri|cane-sword|hammer|knife|dagger|conspiracy|runaway train|"
            r"texture|language|starter edition|battle pass|car shipment|"
            r"full stock|full ownership|master unlock|paint|perk|club|"
            r"credit pack|currency pack|ownership|ubicollectibles|"
            r"legion of the dead|calling all units)\b",
            cleaned,
            re.IGNORECASE,
        ):
            return True
        return False

    def _find_visible_configurations_path(self) -> Optional[str]:
        """Locate the Ubisoft launcher configurations blob in the auth/template prefixes."""
        for prefix_dir in (TEMPLATE_DIR, AUTH_PREFIX_DIR):
            cfg_path = self._find_configurations(prefix_dir)
            if cfg_path:
                return cfg_path
        return None

    def _extract_launcher_config_entries(
        self,
        graphql_names_normalized: Set[str],
    ) -> Tuple[List[Dict[str, Any]], List[Dict[str, Any]]]:
        """Extract launcher-correlated base-game entries from Ubisoft's config cache."""
        cfg_path = self._find_visible_configurations_path()
        if not cfg_path:
            return [], []

        try:
            from .ubisoft_parser import parse_configurations
        except Exception as e:
            logger.debug(f"[Ubisoft] Config parser unavailable: {e}")
            return [], []

        parsed_entries: List[Dict[str, Any]] = []
        parsed_names: Set[str] = set()
        try:
            for cfg in parse_configurations(cfg_path):
                title = self._clean_launcher_title(cfg.name)
                if self._should_skip_launcher_title(title):
                    continue

                norm_title = self._normalize_for_matching(title)
                if norm_title in parsed_names:
                    continue

                parsed_entries.append({
                    "title": title,
                    "space_id": cfg.space_id or "",
                    "install_id": str(cfg.install_id or ""),
                    "launch_id": str(cfg.launch_id or cfg.install_id or ""),
                    "source": "parsed_config",
                    "ownership_type": "owned",
                })
                parsed_names.add(norm_title)
        except Exception as e:
            logger.debug(f"[Ubisoft] Parsed configurations load failed: {e}")

        try:
            with open(cfg_path, "rb") as f:
                config_text = f.read().decode("latin-1", "ignore")
        except OSError as e:
            logger.debug(f"[Ubisoft] Failed to read configurations blob: {e}")
            return [], parsed_entries

        raw_entries: List[Dict[str, Any]] = []
        raw_by_norm: Dict[str, Dict[str, Any]] = {}
        for part in config_text.split("version: 2.0")[1:]:
            chunk = "version: 2.0" + part[:8000]
            if "root:" not in chunk:
                continue

            title_candidates: List[str] = []
            for key in ("display_name", "game_identifier", "name"):
                match = re.search(
                    rf"\b{key}:\s*(?:\"([^\"]+)\"|([^\n]+))",
                    chunk,
                )
                if match:
                    title_candidates.append(
                        self._clean_launcher_title(
                            match.group(1) or match.group(2) or ""
                        )
                    )

            localization_match = re.search(
                r"localizations:\s*\n\s*default:\s*\n\s*l1:\s*"
                r"(?:\"([^\"]+)\"|([^\n]+))",
                chunk,
            )
            if localization_match:
                title_candidates.append(
                    self._clean_launcher_title(
                        localization_match.group(1)
                        or localization_match.group(2)
                        or ""
                    )
                )

            title = next(
                (
                    candidate for candidate in title_candidates
                    if candidate and not self._is_launcher_placeholder_title(candidate)
                ),
                "",
            )
            if self._should_skip_launcher_title(title):
                continue

            space_match = re.search(
                r"\bspace_id:\s*([0-9a-f-]{36})",
                chunk,
                re.IGNORECASE,
            )
            space_id = space_match.group(1) if space_match else ""
            norm_title = self._normalize_for_matching(title)

            if (
                not space_id
                and norm_title not in parsed_names
                and norm_title not in graphql_names_normalized
            ):
                continue

            candidate = {
                "title": title,
                "space_id": space_id,
                "source": "launcher_config",
                "ownership_type": "owned",
            }
            existing = raw_by_norm.get(norm_title)
            if existing is None or (space_id and not existing.get("space_id")):
                raw_by_norm[norm_title] = candidate

        raw_entries = list(raw_by_norm.values())
        return raw_entries, parsed_entries

    @staticmethod
    def _visible_manifest_game_id(entry: Dict[str, Any]) -> str:
        """Return the runtime game id for a visible-manifest entry."""
        space_id = str(entry.get("space_id") or "").strip()
        if space_id:
            return space_id

        install_id = str(entry.get("install_id") or "").strip()
        if install_id:
            return f"ubi-{install_id}"

        digest = hashlib.sha1(
            str(entry.get("title", "")).encode("utf-8")
        ).hexdigest()[:12]
        return f"ubi-visible-{digest}"

    def _merge_visible_manifest_into_id_map(self, entry: Dict[str, Any]) -> bool:
        """Persist visible-manifest IDs into the shared Ubisoft id map."""
        cache_key = self._visible_manifest_game_id(entry)
        current = dict(self._id_map_cache.get(cache_key, {}))
        updated = dict(current)

        updated["name"] = entry.get("title") or updated.get("name")
        updated["source"] = "visible_manifest"

        for field in ("install_id", "launch_id", "ubisoftconnect_game_id"):
            value = str(entry.get(field) or "").strip()
            if value:
                updated[field] = value

        if updated != current:
            self._id_map_cache[cache_key] = updated
            return True
        return False

    def _apply_visible_manifest_filter(
        self,
        games: List[Game],
        installed: Dict[str, Any],
        manifest: Optional[List[Dict[str, Any]]] = None,
        source_label: str = "manifest",
    ) -> List[Game]:
        """Restrict the Ubisoft library to a resolved visible manifest."""
        manifest = manifest if manifest is not None else self._load_visible_manifest()
        if not manifest:
            return games

        allowed_norms = {
            self._normalize_for_matching(entry["title"])
            for entry in manifest
            if entry.get("title")
        }
        allowed_ids = {
            self._visible_manifest_game_id(entry)
            for entry in manifest
        }
        manifest_by_norm = {
            self._normalize_for_matching(entry["title"]): entry
            for entry in manifest
            if entry.get("title")
        }
        manifest_by_id = {
            self._visible_manifest_game_id(entry): entry
            for entry in manifest
        }

        id_map_changed = False
        for entry in manifest:
            id_map_changed |= self._merge_visible_manifest_into_id_map(entry)

        filtered: List[Game] = []
        seen_norms: Set[str] = set()
        seen_ids: Set[str] = set()
        for game in games:
            norm_title = self._normalize_for_matching(game.title)
            if game.id not in allowed_ids and norm_title not in allowed_norms:
                logger.debug(f"[Ubisoft] Visible {source_label} skip: {game.title}")
                continue

            entry = manifest_by_id.get(game.id) or manifest_by_norm.get(norm_title)
            if entry:
                if entry.get("title"):
                    game.title = entry["title"]
                if entry.get("ownership_type"):
                    game.ownership_type = entry["ownership_type"]
                if entry.get("cover_image"):
                    game.cover_image = entry["cover_image"]
                    if not hasattr(game, "extra") or game.extra is None:
                        game.extra = {}
                    game.extra.setdefault("coverUrl", entry["cover_image"])

            filtered.append(game)
            seen_norms.add(norm_title)
            seen_ids.add(game.id)

        injected = 0
        for entry in manifest:
            game_id = self._visible_manifest_game_id(entry)
            norm_title = self._normalize_for_matching(entry["title"])
            if game_id in seen_ids or norm_title in seen_norms:
                continue

            install_meta = (
                installed.get(entry.get("space_id", ""))
                or installed.get(game_id)
                or {}
            )
            cover_image = entry.get("cover_image") or None
            game = Game(
                id=game_id,
                title=entry["title"],
                store="ubisoft",
                is_installed=bool(install_meta),
                cover_image=cover_image,
                ownership_type=entry.get("ownership_type") or "owned",
                install_path=install_meta.get("install_path"),
                executable=install_meta.get("executable"),
            )
            if cover_image:
                game.extra = {
                    "coverUrl": cover_image,
                    "backgroundUrl": "",
                    "bannerUrl": "",
                }

            filtered.append(game)
            seen_ids.add(game_id)
            seen_norms.add(norm_title)
            injected += 1
            logger.info(
                f"[Ubisoft] Visible {source_label} injected: {entry['title']} "
                f"[id={game_id}]"
            )

        if id_map_changed:
            self._save_id_map()

        logger.info(
            f"[Ubisoft] Visible {source_label} filter kept {len(filtered)} games "
            f"from {len(games)} base entries (+{injected} injected)"
        )
        return filtered

    def resolve_install_id(self, space_id: str) -> Optional[str]:
        """Resolve spaceId to installId from cache, preferring ubisoftConnectGameId."""
        entry = self._id_map_cache.get(space_id, {})
        # For native Ubisoft games, prefer ubisoftConnectGameId (more reliable for deeplinks)
        if "ubisoftconnect_game_id" in entry:
            return entry.get("ubisoftconnect_game_id")
        return entry.get("install_id")

    def resolve_launch_id(self, space_id: str) -> Optional[str]:
        """Resolve spaceId to launchId from cache, preferring ubisoftConnectGameId."""
        entry = self._id_map_cache.get(space_id, {})
        # For native Ubisoft games, prefer ubisoftConnectGameId (more reliable for deeplinks)
        if "ubisoftconnect_game_id" in entry:
            return entry.get("ubisoftconnect_game_id")
        return entry.get("launch_id")

    def update_id_map(self, space_id: str, install_id: str, launch_id: str) -> None:
        """Add or update a mapping entry and persist."""
        self._id_map_cache[space_id] = {
            "install_id": install_id,
            "launch_id": launch_id,
        }
        self._save_id_map()

    def resolve_ubisoftconnect_game_id(self, space_id: str) -> Optional[str]:
        """Resolve spaceId to ubisoftConnectGameId from cache (preferred deeplink ID).
        
        Returns the cached ubisoftConnectGameId if available, which is more reliable
        than the static install_id for native Ubisoft games.
        """
        entry = self._id_map_cache.get(space_id, {})
        return entry.get("ubisoftconnect_game_id")

    def _extract_cache_game_ids(self, prefix_path: str) -> Dict[str, str]:
        """Extract ubisoftConnectGameId mappings from Ubisoft Connect's local cache.
        
        Reads Ubisoft Connect's localStorage leveldb cache to find spaceId -> ubisoftConnectGameId
        mappings. This is more reliable for native games than static name-matching.
        
        Returns a dict of {spaceId: ubisoftConnectGameId}.
        """
        result = {}
        
        # Try to find localStorage leveldb path
        for path_variant in [
            os.path.join(prefix_path, LOCALSTORAGE_RELATIVE_PATH, "leveldb"),
            os.path.join(prefix_path, "pfx", LOCALSTORAGE_RELATIVE_PATH, "leveldb"),
        ]:
            if not os.path.isdir(path_variant):
                continue
            
            try:
                # Read all leveldb files to look for game metadata
                # The cache contains JSON-like game data with spaceId and ubisoftConnectGameId
                leveldb_files = glob.glob(os.path.join(path_variant, "*.ldb"))
                if not leveldb_files:
                    leveldb_files = glob.glob(os.path.join(path_variant, "*.log"))
                
                for ldb_file in leveldb_files:
                    try:
                        with open(ldb_file, "rb") as f:
                            content = f.read()
                            # Search for patterns containing spaceId and ubisoftConnectGameId
                            # These are stored in JSON-like format in the cache
                            self._extract_ids_from_binary(content, result)
                    except Exception as e:
                        logger.debug(f"[Ubisoft] Error reading leveldb file {ldb_file}: {e}")
                
                if result:
                    logger.info(f"[Ubisoft] Extracted {len(result)} ubisoftConnectGameId mappings from cache")
                    return result
                    
            except Exception as e:
                logger.debug(f"[Ubisoft] Error accessing localStorage leveldb: {e}")
        
        return result

    @staticmethod
    def _extract_ids_from_binary(data: bytes, result: Dict[str, str]) -> None:
        """Search binary data for spaceId and ubisoftConnectGameId patterns.
        
        These are typically stored in JSON form within the leveldb cache.
        Looks for patterns like "ubisoftConnectGameId" followed by a numeric value,
        and associates it with nearby spaceId values.
        """
        try:
            # Decode attempts to find readable strings in binary data
            decoded = data.decode("utf-8", errors="ignore")
            
            # Look for JSON-like game entries with both fields
            # Pattern: ...spaceId...ubisoftConnectGameId...
            import re
            
            # Find all potential game entries (chunks containing both identifiers)
            # UUIDs can vary in format, so use flexible pattern
            for match in re.finditer(
                r'"spaceId"\s*:\s*"([a-f0-9\-]+)".*?"ubisoftConnectGameId"\s*:\s*(\d+)',
                decoded,
                re.IGNORECASE | re.DOTALL
            ):
                space_id = match.group(1)
                ubisoft_id = match.group(2)
                if space_id and ubisoft_id:
                    result[space_id] = ubisoft_id
                    
            # Also try reverse order: ubisoftConnectGameId then spaceId
            for match in re.finditer(
                r'"ubisoftConnectGameId"\s*:\s*(\d+).*?"spaceId"\s*:\s*"([a-f0-9\-]+)"',
                decoded,
                re.IGNORECASE | re.DOTALL
            ):
                ubisoft_id = match.group(1)
                space_id = match.group(2)
                if space_id and ubisoft_id:
                    result[space_id] = ubisoft_id
        except Exception as e:
            logger.debug(f"[Ubisoft] Error extracting IDs from binary: {e}")

    # ========================================================================
    # Static Game ID Database
    # ========================================================================

    async def _fetch_game_id_database(self) -> List[tuple]:
        """
        Fetch the community game ID database (numeric install_id → name mapping).

        Caches locally and refreshes weekly. Returns list of (install_id, name) tuples.
        """
        import time as _time

        # Use cached file if fresh enough
        if os.path.isfile(GAME_ID_DB_FILE):
            age = _time.time() - os.path.getmtime(GAME_ID_DB_FILE)
            if age < GAME_ID_DB_MAX_AGE:
                return self._parse_game_id_database(GAME_ID_DB_FILE)

        # Download fresh copy
        try:
            import urllib.request
            os.makedirs(DATA_DIR, exist_ok=True)
            tmp = GAME_ID_DB_FILE + ".tmp"
            urllib.request.urlretrieve(GAME_ID_DB_URL, tmp)
            os.replace(tmp, GAME_ID_DB_FILE)
            logger.info("[Ubisoft] Game ID database downloaded")
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to download game ID database: {e}")
            # Fall back to existing file if available
            if not os.path.isfile(GAME_ID_DB_FILE):
                return []

        return self._parse_game_id_database(GAME_ID_DB_FILE)

    @staticmethod
    def _parse_game_id_database(filepath: str) -> List[tuple]:
        """Parse the game ID database file. Format: '{numeric_id}, {game_name}' per line."""
        entries = []
        try:
            with open(filepath, "r", encoding="utf-8", errors="replace") as f:
                for line in f:
                    line = line.strip()
                    if not line or line.startswith("#"):
                        continue
                    parts = line.split(", ", 1)
                    if len(parts) == 2 and parts[0].isdigit():
                        entries.append((parts[0], parts[1]))
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to parse game ID database: {e}")
        return entries

    @staticmethod
    def _normalize_for_matching(name: str) -> str:
        """Normalize a game name for fuzzy matching."""
        import re as _re
        name = name.lower()
        # Replace underscores with spaces (Watch_Dogs → Watch Dogs)
        name = name.replace("_", " ")
        # Remove trademark symbols and punctuation
        name = _re.sub(r"[®™©''\-:.,!?()\"']", "", name)
        # Normalize whitespace
        name = " ".join(name.split())
        return name

    @staticmethod
    def _extract_game_id_from_registry(prefix_path: str) -> Optional[str]:
        """Extract the Ubisoft numeric game ID from the Wine prefix registry.

        Reads system.reg for entries under
        ``Software\\Wow6432Node\\Ubisoft\\Launcher\\Installs\\<GAME_ID>``
        where ``InstallDir`` points into the Ubisoft Game Launcher games folder
        (i.e. the *real* per-prefix install, not an external/stale entry).

        This is the authoritative, fully-local source — no network or fuzzy
        matching required.
        """
        for reg_name in ("system.reg", os.path.join("pfx", "system.reg")):
            reg_path = os.path.join(prefix_path, reg_name)
            if not os.path.isfile(reg_path):
                continue

            try:
                with open(reg_path, "r", encoding="utf-8", errors="replace") as f:
                    content = f.read()
            except OSError:
                continue

            # Pattern: [Software\\Wow6432Node\\Ubisoft\\Launcher\\Installs\\<ID>]
            # followed by "InstallDir"="<path>"
            fallback_id = None
            for m in re.finditer(
                r'\[Software\\\\Wow6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\(\d+)\]'
                r'[^\[]*?"InstallDir"\s*=\s*"([^"]*)"',
                content,
                re.DOTALL,
            ):
                game_id = m.group(1)
                install_dir = m.group(2).replace("\\\\", "/")
                # Prefer the entry whose InstallDir is inside the prefix
                # (the per-prefix local install, not an external/stale one)
                standard_path = (
                    "Ubisoft Game Launcher/games/" in install_dir
                    or "Ubisoft Game Launcher\\games\\" in install_dir
                )
                if standard_path:
                    logger.info(
                        f"[Ubisoft] Extracted game ID {game_id} from registry "
                        f"(InstallDir={install_dir[:60]})"
                    )
                    return game_id
                # Track non-standard path as fallback (custom install location)
                if not fallback_id:
                    fallback_id = game_id
            if fallback_id:
                logger.info(
                    f"[Ubisoft] Extracted game ID {fallback_id} from registry "
                    f"(non-standard InstallDir, custom location)"
                )
                return fallback_id

            # Fallback: also check user.reg for HKCU Installs entries
            user_reg = os.path.join(prefix_path, reg_name.replace("system.reg", "user.reg"))
            if os.path.isfile(user_reg):
                try:
                    with open(user_reg, "r", encoding="utf-8", errors="replace") as f:
                        content = f.read()
                except OSError:
                    continue

                for m in re.finditer(
                    r'\[Software\\\\Ubisoft\\\\Launcher\\\\Installs\\\\(\d+)\]',
                    content,
                ):
                    game_id = m.group(1)
                    logger.info(f"[Ubisoft] Extracted game ID {game_id} from user.reg")
                    return game_id

        return None

    async def _lookup_game_id_by_name(self, game_name: str) -> Optional[str]:
        """Fallback: look up a Ubisoft game ID by name from the online database.

        Only used when registry extraction fails (e.g. prefix not yet created).
        """
        if not game_name:
            return None

        try:
            db_entries = await self._fetch_game_id_database()
        except Exception as e:
            logger.debug(f"[Ubisoft] Failed to fetch game ID database: {e}")
            return None
        if not db_entries:
            return None

        normalized_query = self._normalize_for_matching(game_name)

        for install_id, db_name in db_entries:
            if self._normalize_for_matching(db_name) == normalized_query:
                logger.info(f"[Ubisoft] DB match for '{game_name}': ID {install_id}")
                return install_id

        return None
    # Steam-Linked Game Filtering
    # ========================================================================

    @staticmethod
    def _get_steam_game_titles() -> Set[str]:
        """Get normalized titles of ALL games in the user's Steam library.

        Uses librarycache directories (all owned app IDs, including uninstalled)
        cross-referenced with appinfo.vdf (game names). This covers the full
        Steam library, not just installed games.
        """
        try:
            from ..steam.library import get_steam_library_names
        except ImportError:
            logger.debug("[Ubisoft] Steam library module not available")
            return set()

        try:
            raw_names = get_steam_library_names()
            steam_titles: Set[str] = set()
            for name in raw_names:
                if not name or name.startswith(
                    ("Proton", "Steam Linux Runtime", "Steamworks")
                ):
                    continue
                steam_titles.add(
                    UbisoftConnector._normalize_for_matching(name)
                )
            logger.debug(
                f"[Ubisoft] Found {len(steam_titles)} Steam library titles"
            )
            return steam_titles
        except Exception as e:
            logger.debug(f"[Ubisoft] Steam library scan failed: {e}")
            return set()

    # ========================================================================
    # Prefix Utilities
    # ========================================================================

    def _find_upc_exe(self, prefix_path: str) -> Optional[str]:
        """Find upc.exe in a prefix, checking both direct and pfx/ layouts.

        Proton creates a pfx/ subdirectory; bare Wine uses the root directly.
        """
        for path in [
            os.path.join(prefix_path, UPC_RELATIVE_PATH),
            os.path.join(prefix_path, "pfx", UPC_RELATIVE_PATH),
        ]:
            if os.path.isfile(path):
                return path
        return None

    def _find_connect_exe(self, prefix_path: str) -> Optional[str]:
        """Find UbisoftConnect.exe — the registered uplay:// protocol handler.

        Use this (not upc.exe) when launching with a uplay://install/ URL,
        since only UbisoftConnect.exe is registered to process protocol URLs.
        """
        for path in [
            os.path.join(prefix_path, UPC_CONNECT_RELATIVE_PATH),
            os.path.join(prefix_path, "pfx", UPC_CONNECT_RELATIVE_PATH),
        ]:
            if os.path.isfile(path):
                return path
        return None

    def _find_configurations(self, prefix_path: str) -> Optional[str]:
        """Find configurations binary in prefix, checking both direct and pfx/ layouts."""
        for path in [
            os.path.join(prefix_path, CONFIGURATIONS_RELATIVE_PATH),
            os.path.join(prefix_path, "pfx", CONFIGURATIONS_RELATIVE_PATH),
        ]:
            if os.path.isfile(path):
                return path
        return None

    def _backfill_hidden_prefix_session(self, prefix_path: str) -> None:
        """Inject the current Ubisoft session into a recreated hidden prefix."""
        if not os.path.isdir(prefix_path):
            return
        if not (self._find_upc_exe(prefix_path) or self._find_connect_exe(prefix_path)):
            return
        self._ensure_upc_auth_state_in_prefixes([prefix_path])

    # ========================================================================
    # Template Prefix Management
    # ========================================================================

    def _template_exists(self) -> bool:
        """Check if the template prefix with bootstrap marker exists."""
        marker = os.path.join(TEMPLATE_DIR, BOOTSTRAP_MARKER)
        return os.path.exists(marker)

    def queue_auth_assets_ensure(self, reason: str = "background") -> None:
        """Ensure Ubisoft hidden auth assets exist in the background.

        This keeps `.template`, `.upc-auth`, and the auth shortcut resilient
        across plugin installs, reloads, and restarts without blocking init.
        """
        if self._auth_assets_task and not self._auth_assets_task.done():
            logger.info(
                f"[Ubisoft] Auth asset ensure already in progress (reason={reason})"
            )
            return

        logger.info(f"[Ubisoft] Queuing auth asset ensure (reason={reason})")
        self._auth_assets_task = asyncio.create_task(
            self._ensure_auth_assets(reason)
        )

    async def _ensure_auth_assets(self, reason: str) -> None:
        """Repair or recreate hidden Ubisoft auth assets."""
        async with self._auth_assets_lock:
            logger.info(f"[Ubisoft] Ensuring auth assets (reason={reason})")

            await self._regenerate_template_if_stale()

            if not self._template_exists():
                await self._ensure_template_prefix()
            else:
                self._backfill_hidden_prefix_session(TEMPLATE_DIR)

            if os.path.isdir(AUTH_PREFIX_DIR):
                self._backfill_hidden_prefix_session(AUTH_PREFIX_DIR)
            elif os.path.isfile(UPC_SESSION_FILE):
                logger.info("[Ubisoft] Auth prefix missing but user is authenticated; recreating")
                await self._ensure_auth_prefix()

            self._ensure_upc_auth_state_in_prefixes(list(self._iter_game_prefix_paths() or []))

            # Re-ensure the auth shortcut VDF + artwork (handles user deletion)
            await self._ensure_ubisoft_auth_shortcut()

    @staticmethod
    def _proton_family(version_str: str) -> str:
        """Classify a Proton version string into a family.

        Proton writes different formats depending on variant:
          - Proton Experimental: bare numbers like "10.1000-200"
          - UMU-Proton: "UMU-Proton-9.0-4e"
          - GE-Proton: "GE-Proton9-27"
          - Installation dir version: "1773313326 experimental-10.0-..."

        Returns family name for coarse comparison.
        """
        v = version_str.lower()
        if "umu-proton" in v:
            return "umu-proton"
        if "ge-proton" in v:
            return "ge-proton"
        if "experimental" in v:
            return "experimental"
        # Proton Experimental writes bare version numbers (e.g. "10.1000-200")
        # to the prefix. Treat purely numeric versions as experimental.
        stripped = v.replace(".", "").replace("-", "").replace(" ", "")
        if stripped.isdigit():
            return "experimental"
        return "other"

    def _is_prefix_version_stale(self, prefix_dir: str) -> bool:
        """Check if a prefix was built with a different Proton family.

        Since Proton Experimental is always used, a prefix created by
        UMU-Proton or GE-Proton is considered stale.
        """
        version_file = os.path.join(prefix_dir, "version")
        if not os.path.isfile(version_file):
            return False
        try:
            prefix_version = open(version_file).read().strip()
        except OSError:
            return False
        if not prefix_version:
            return False

        prefix_family = self._proton_family(prefix_version)
        # Proton Experimental is always forced; anything non-experimental is stale
        if prefix_family != "experimental":
            logger.info(
                f"[Ubisoft] Prefix version stale: '{prefix_version}' "
                f"(family={prefix_family}, expected=experimental) "
                f"prefix={prefix_dir}"
            )
            return True
        return False

    @staticmethod
    def _read_prefix_machine_guid(prefix_path: str) -> str:
        """Read the Wine MachineGuid from a prefix's system.reg.

        Checks pfx/system.reg first because Proton uses that for DPAPI
        encryption; the root-level system.reg may be a stale template copy.
        """
        for reg_path in [
            os.path.join(prefix_path, "pfx", "system.reg"),
            os.path.join(prefix_path, "system.reg"),
        ]:
            if not os.path.isfile(reg_path):
                continue
            try:
                with open(reg_path, "r", encoding="utf-8", errors="ignore") as f:
                    m = re.search(r'"MachineGuid"="([^"]+)"', f.read())
                    if m:
                        return m.group(1)
            except Exception:
                pass
        return ""

    async def _regenerate_template_if_stale(self) -> None:
        """Delete and recreate the template if it was built with a different Proton."""
        if not self._template_exists():
            return
        if not self._is_prefix_version_stale(TEMPLATE_DIR):
            return

        logger.warning("[Ubisoft] Template prefix is stale, removing for recreation")
        shutil.rmtree(TEMPLATE_DIR, ignore_errors=True)
        # _ensure_template_prefix will recreate on next call

    def _queue_template_creation(self) -> None:
        """Queue template prefix creation as a background task."""
        if self._template_task and not self._template_task.done():
            logger.info("[Ubisoft] Template creation already in progress")
            return

        logger.info("[Ubisoft] Queuing background template prefix creation")
        self._template_task = asyncio.create_task(self._ensure_template_prefix())

    async def _ensure_template_prefix(self) -> None:
        """
        Create the template Wine prefix with upc.exe installed.

        This is a background task triggered after first successful sync.
        Idempotent -- skips if template already exists.
        """
        if self._template_exists():
            logger.info("[Ubisoft] Template prefix already exists, skipping")
            return

        try:
            logger.info("[Ubisoft] Starting template prefix creation...")

            # Step 1: Ensure installer is cached
            installer_path = await self._ensure_installer_cached()
            if not installer_path:
                logger.error("[Ubisoft] Failed to cache installer, aborting template creation")
                return

            # Step 2: Create template prefix directory
            os.makedirs(TEMPLATE_DIR, exist_ok=True)

            # Step 3: Install UPC silently into template prefix
            logger.info("[Ubisoft] Installing Ubisoft Connect into template prefix...")
            umu_run = self._find_umu_run()
            if not umu_run:
                logger.error("[Ubisoft] umu-run not found, aborting template creation")
                return

            env = self._build_umu_env(TEMPLATE_DIR, "umu-ubisoft-template")

            python_bin = self._find_python()
            logger.info(f"[Ubisoft] Template install: PROTONPATH={env.get('PROTONPATH')}")
            proc = await asyncio.create_subprocess_exec(
                python_bin, umu_run, installer_path, "/S",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(
                    f"[Ubisoft] Template UPC install failed (rc={proc.returncode}): "
                    f"{stderr.decode()[:500]}"
                )
                return

            # Step 4: Verify upc.exe exists (check both direct and pfx/ layouts)
            if not self._find_upc_exe(TEMPLATE_DIR):
                logger.error("[Ubisoft] upc.exe not found after install")
                return

            # Step 5: Write bootstrap marker
            marker_path = os.path.join(TEMPLATE_DIR, BOOTSTRAP_MARKER)
            with open(marker_path, "w") as f:
                f.write(f"template\ncreated={__import__('datetime').datetime.now().isoformat()}\n")

            self._backfill_hidden_prefix_session(TEMPLATE_DIR)
            logger.info("[Ubisoft] Template prefix created successfully")

            # Create persistent VDF shortcut for auth (also cleans up legacy)
            await self._ensure_ubisoft_auth_shortcut()

        except Exception as e:
            logger.exception(f"[Ubisoft] Template prefix creation failed: {e}")

    async def _ensure_auth_prefix(self) -> Optional[str]:
        """Ensure the dedicated UPC auth prefix exists and return the upc.exe path.

        Clones from template if available, or falls back to any existing game prefix.
        On a fresh install with no Ubisoft prefixes yet, performs a direct UPC
        install into `.upc-auth` so auth never depends on installing a game first.
        Automatically rebuilds if prefix Proton version doesn't match current
        or if DPAPI keys (MachineGuid) differ from the .template prefix.
        """
        upc_path = self._find_upc_exe(AUTH_PREFIX_DIR)
        rebuild_required = False

        if upc_path:
            # Check for Proton version mismatch
            if self._is_prefix_version_stale(AUTH_PREFIX_DIR):
                logger.warning("[Ubisoft] Auth prefix Proton version stale, rebuilding")
                rebuild_required = True
            
            # Check for DPAPI mismatch against template
            elif self._template_exists():
                auth_guid = self._read_prefix_machine_guid(AUTH_PREFIX_DIR)
                tmpl_guid = self._read_prefix_machine_guid(TEMPLATE_DIR)

                if tmpl_guid and auth_guid and tmpl_guid != auth_guid:
                    logger.warning(
                        "[Ubisoft] Auth prefix DPAPI keys (MachineGuid) desynced from template. "
                        "Rebuilding .upc-auth to ensure ConnectSecureStorage.dat portability."
                    )
                    rebuild_required = True

        if rebuild_required or (os.path.isdir(AUTH_PREFIX_DIR) and not upc_path):
            if not rebuild_required:
                logger.warning("[Ubisoft] Auth prefix exists but upc.exe missing; re-cloning")
            shutil.rmtree(AUTH_PREFIX_DIR, ignore_errors=True)
            upc_path = None

        if upc_path:
            return upc_path

        # Regenerate template if stale (prevents cloning an outdated prefix)
        await self._regenerate_template_if_stale()

        # Choose source: template preferred, game prefix as fallback
        src = None
        label = ""
        if self._template_exists():
            src = TEMPLATE_DIR
            label = "template"
        elif os.path.isdir(PREFIXES_DIR):
            for entry in sorted(os.listdir(PREFIXES_DIR)):
                if entry.startswith('.'):
                    continue
                candidate = os.path.join(PREFIXES_DIR, entry)
                if self._find_upc_exe(candidate):
                    src = candidate
                    label = f"game prefix {entry[:8]}"
                    break

        if src:
            logger.info(f"[Ubisoft] Cloning {label} → .upc-auth prefix")
            os.makedirs(AUTH_PREFIX_DIR, exist_ok=True)
            proc = await asyncio.create_subprocess_exec(
                "rsync", "-a", "--exclude=games", f"{src}/", f"{AUTH_PREFIX_DIR}/",
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()
            if proc.returncode != 0:
                logger.error("[Ubisoft] rsync failed for auth prefix clone")
                return None
        else:
            logger.info("[Ubisoft] No template/game prefix found, bootstrapping .upc-auth directly")
            installer_path = await self._ensure_installer_cached()
            if not installer_path:
                return None

            os.makedirs(AUTH_PREFIX_DIR, exist_ok=True)
            umu_run = self._find_umu_run()
            if not umu_run:
                return None

            env = self._build_umu_env(
                AUTH_PREFIX_DIR,
                "umu-ubisoft-auth",
                AUTH_SHORTCUT_STORE_ID,
            )
            python_bin = self._find_python()
            proc = await asyncio.create_subprocess_exec(
                python_bin, umu_run, installer_path, "/S",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            _stdout, stderr = await proc.communicate()
            if proc.returncode != 0 and not self._find_upc_exe(AUTH_PREFIX_DIR):
                logger.error(
                    f"[Ubisoft] Direct .upc-auth install failed (rc={proc.returncode}): "
                    f"{stderr.decode(errors='replace')[:500]}"
                )
                return None

            marker_path = os.path.join(AUTH_PREFIX_DIR, BOOTSTRAP_MARKER)
            try:
                with open(marker_path, "w") as f:
                    f.write("auth_prefix\n")
            except OSError:
                pass

        # Fix pfx symlink: rsync preserves the source's symlink target, but
        # Proton expects pfx/ to point to the prefix itself (not the source).
        pfx_link = os.path.join(AUTH_PREFIX_DIR, "pfx")
        if os.path.islink(pfx_link):
            current_target = os.readlink(pfx_link)
            if current_target != AUTH_PREFIX_DIR and current_target != ".":
                os.remove(pfx_link)
                os.symlink(AUTH_PREFIX_DIR, pfx_link)
                logger.info(f"[Ubisoft] Fixed pfx symlink: {current_target} → {AUTH_PREFIX_DIR}")

        upc_path = self._find_upc_exe(AUTH_PREFIX_DIR)
        if upc_path:
            self._backfill_hidden_prefix_session(AUTH_PREFIX_DIR)
            logger.info("[Ubisoft] Auth prefix created successfully")
        return upc_path

    def _build_auth_launch_options(self) -> str:
        """Build the canonical launch options for the auth shortcut.

        Uses the unifideck-launcher with auth action + prefix override.
        Keep this a plain native shortcut launch so Steam does not try to run
        the bash launcher through Proton. The launcher itself invokes umu-run.
        """
        return (
            'ubisoft:upc-auth '
            'UNIFIDECK_UBISOFT_ACTION=auth '
            'UNIFIDECK_UBISOFT_PREFIX_NAME=.upc-auth'
        )

    def _get_launcher_path(self) -> str:
        """Return the path to the unifideck-launcher script."""
        # Use self.plugin_dir (set from DECKY_PLUGIN_DIR in __init__),
        # NOT self.plugin_instance.plugin_dir (Plugin class doesn't have it).
        plugin_dir = self.plugin_dir
        if not plugin_dir:
            # Fallback: derive from __file__ (4 levels up from stores/ubisoft.py)
            plugin_dir = os.path.dirname(os.path.dirname(os.path.dirname(
                os.path.dirname(os.path.abspath(__file__))
            )))
        return os.path.join(plugin_dir, 'bin', 'unifideck-launcher')

    async def _ensure_ubisoft_auth_shortcut(self) -> Optional[int]:
        """Create a persistent VDF shortcut for the UPC auth flow.

        Uses the unifideck-launcher as the exe (same as game shortcuts) so
        that umu-run handles Proton with proper gamescope integration.
        Writes to shortcuts.vdf, registers in shortcuts_registry.json,
        keeps the shortcut native, and downloads SteamGridDB artwork.
        The shortcut itself does not require `.upc-auth` to exist up front —
        the launcher can bootstrap that prefix on first run.
        """
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            return None

        try:
            from py_modules.unifideck.shortcuts.shortcuts_manager import (
                load_shortcuts_registry, register_shortcut
            )
            from py_modules.unifideck.shortcuts.launch_options import get_full_id

            # Check if already registered AND present in VDF
            sm = self.plugin_instance.shortcuts_manager
            registry = load_shortcuts_registry()
            if AUTH_SHORTCUT_STORE_ID in registry:
                vdf_found = await self._validate_auth_shortcut()
                if vdf_found:
                    uid = registry[AUTH_SHORTCUT_STORE_ID].get("appid_unsigned")
                    # Gap-fill artwork if any types are missing (non-force: cheap check)
                    if uid:
                        await self._fetch_auth_shortcut_artwork(uid)
                    return uid

                # VDF entry missing (user deleted shortcut) — recreate from
                # registry data. No auth prefix needed for the VDF entry; the
                # prefix is only required at launch time by the launcher script.
                entry = registry[AUTH_SHORTCUT_STORE_ID]
                appid = entry.get("appid")
                unsigned_id = entry.get("appid_unsigned")
                if appid and unsigned_id:
                    logger.info(
                        f"[Ubisoft] Recreating auth shortcut VDF from registry "
                        f"(appid={unsigned_id})"
                    )
                    launcher_path = self._get_launcher_path()
                    launch_options = self._build_auth_launch_options()
                    shortcuts_data = await sm.read_shortcuts()
                    shortcuts = shortcuts_data.get('shortcuts', {})

                    already_in_vdf = any(
                        get_full_id(s.get('LaunchOptions', '')) == AUTH_SHORTCUT_STORE_ID
                        for s in shortcuts.values()
                    )
                    if not already_in_vdf:
                        existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
                        next_idx = max(existing_indices, default=-1) + 1
                        shortcuts[str(next_idx)] = {
                            'appid': appid,
                            'AppName': 'Ubisoft Connect',
                            'exe': f'"{launcher_path}"',
                            'StartDir': f'"{os.path.dirname(launcher_path)}"',
                            'LaunchOptions': launch_options,
                            'IsHidden': 1,
                            'AllowDesktopConfig': 1,
                            'OpenVR': 0,
                            'tags': {'0': 'Ubisoft'},
                        }
                        await sm.write_shortcuts(shortcuts_data)

                    # The auth shortcut is a native bash launcher that manages
                    # Proton internally via umu-run, so Steam compat must stay clear.
                    await sm._clear_proton_compatibility(appid)

                    # Re-fetch artwork (may have been deleted along with shortcut)
                    await self._fetch_auth_shortcut_artwork(unsigned_id, force=True)

                    return unsigned_id

            # First-time creation: build the launcher shortcut immediately.
            # The launcher can bootstrap `.upc-auth` lazily on first run.
            launcher_path = self._get_launcher_path()

            # Compute appId using same CRC32 algorithm as Steam
            # Use the launcher as exe (same as game shortcuts)
            appid = sm.generate_app_id("Ubisoft Connect", launcher_path)
            unsigned_id = appid if appid >= 0 else appid + 2**32

            launch_options = self._build_auth_launch_options()

            # Read VDF once, batch all modifications, then write once.
            # This minimizes VDF writes which trigger Steam to reload all shortcuts.
            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})
            vdf_dirty = False

            # Remove orphaned AddShortcut entries: empty exe+LaunchOptions with
            # a name that matches our shortcut names. These are debris from old
            # ephemeral AddShortcut calls that Steam persisted to VDF.
            orphan_names = {'upc.exe', 'ubisoft connect'}
            orphan_ids = [
                idx for idx, s in shortcuts.items()
                if s.get('AppName', '').lower() in orphan_names
                and not s.get('exe', '').strip('"')
                and not s.get('LaunchOptions', '')
            ]
            for idx in orphan_ids:
                name = shortcuts[idx].get('AppName', '?')
                logger.info(f"[Ubisoft] Removing orphaned shortcut [{idx}] '{name}'")
                del shortcuts[idx]
                vdf_dirty = True

            # Remove legacy .template auth shortcut entries (migration)
            legacy_ids = [
                idx for idx, s in shortcuts.items()
                if s.get('LaunchOptions', '') == 'ubisoft:.template'
            ]
            for idx in legacy_ids:
                logger.info(f"[Ubisoft] Removing legacy .template shortcut [{idx}]")
                del shortcuts[idx]
                vdf_dirty = True

            # Add canonical auth shortcut if not already in VDF
            already_in_vdf = any(
                get_full_id(s.get('LaunchOptions', '')) == AUTH_SHORTCUT_STORE_ID
                for s in shortcuts.values()
            )

            if not already_in_vdf:
                existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
                next_idx = max(existing_indices, default=-1) + 1
                shortcuts[str(next_idx)] = {
                    'appid': appid,
                    'AppName': 'Ubisoft Connect',
                    'exe': f'"{launcher_path}"',
                    'StartDir': f'"{os.path.dirname(launcher_path)}"',
                    'LaunchOptions': launch_options,
                    'IsHidden': 1,
                    'AllowDesktopConfig': 1,
                    'OpenVR': 0,
                    'tags': {'0': 'Ubisoft'},
                }
                logger.info(f"[Ubisoft] Created auth shortcut in VDF (appid={unsigned_id})")
                vdf_dirty = True

            # Single VDF write for all batched changes
            if vdf_dirty:
                await sm.write_shortcuts(shortcuts_data)
                logger.info(
                    f"[Ubisoft] VDF updated: orphans={len(orphan_ids)} "
                    f"legacy={len(legacy_ids)} added={not already_in_vdf}"
                )

            # Register in shortcuts_registry.json
            register_shortcut(AUTH_SHORTCUT_STORE_ID, appid, "Ubisoft Connect")

            # Clean up legacy .template from registry too
            if "ubisoft:.template" in registry:
                from py_modules.unifideck.shortcuts.shortcuts_manager import save_shortcuts_registry
                del registry["ubisoft:.template"]
                save_shortcuts_registry(registry)
                logger.info("[Ubisoft] Removed legacy .template from shortcuts registry")

            # Keep the auth shortcut native; the launcher manages Proton itself.
            await sm._clear_proton_compatibility(appid)

            # Download SteamGridDB artwork
            await self._fetch_auth_shortcut_artwork(unsigned_id)

            return unsigned_id

        except Exception as e:
            logger.warning(f"[Ubisoft] Auth shortcut creation failed: {e}")
            return None

    async def _validate_auth_shortcut(self) -> bool:
        """Validate the auth shortcut VDF entry, fixing if needed.

        Runs on every plugin init (fast: just reads + compares).
        Ensures launch options use the unifideck-launcher format,
        the exe points to the launcher, and the auth shortcut stays native.

        NOTE: This writes to shortcuts.vdf when fixes are needed.
        Do NOT call this right before launching the shortcut — Steam
        needs time to reload VDF changes, and a rewrite here causes
        RunGame() to silently fail. Use the fast path in
        get_ubisoft_auth_shortcut_context() instead.

        Returns True if the VDF entry was found (even if it needed fixing),
        False if the entry is missing from VDF entirely.
        """
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            return True  # Can't check — assume OK to avoid unnecessary recreation

        try:
            from py_modules.unifideck.shortcuts.launch_options import get_full_id

            sm = self.plugin_instance.shortcuts_manager
            launcher_path = self._get_launcher_path()
            expected_launch_options = self._build_auth_launch_options()

            # Recompute appId from current launcher path (may differ from old upc.exe-based ID)
            expected_appid = sm.generate_app_id("Ubisoft Connect", launcher_path)

            # Scan VDF for auth shortcut entry (match by launch options content)
            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})
            vdf_updated = False
            found = False

            for idx, s in shortcuts.items():
                full_id = get_full_id(s.get('LaunchOptions', ''))
                if full_id == AUTH_SHORTCUT_STORE_ID:
                    found = True
                    # Fix launch options if corrupted
                    if s.get('LaunchOptions', '') != expected_launch_options:
                        logger.info(
                            f"[Ubisoft] Auth shortcut launch options outdated, fixing. "
                            f"Was: {s.get('LaunchOptions', '')!r}"
                        )
                        s['LaunchOptions'] = expected_launch_options
                        vdf_updated = True
                    # Fix exe if it points to upc.exe instead of launcher
                    current_exe = s.get('exe', '').strip('"')
                    if current_exe != launcher_path:
                        logger.info(f"[Ubisoft] Auth shortcut exe outdated, fixing")
                        s['exe'] = f'"{launcher_path}"'
                        s['StartDir'] = f'"{os.path.dirname(launcher_path)}"'
                        vdf_updated = True
                    # Fix appid if it changed (exe path changed → CRC changed)
                    if s.get('appid') != expected_appid:
                        logger.info(f"[Ubisoft] Auth shortcut appid changed, fixing")
                        s['appid'] = expected_appid
                        vdf_updated = True
                    break

            if vdf_updated:
                await sm.write_shortcuts(shortcuts_data)

            if not found:
                logger.warning("[Ubisoft] Auth shortcut not found in VDF during validation")
                return False

            # Always (re-)register in the shortcuts registry so the auth
            # context lookup succeeds even if the registry was cleared.
            from py_modules.unifideck.shortcuts.shortcuts_manager import register_shortcut
            register_shortcut(AUTH_SHORTCUT_STORE_ID, expected_appid, "Ubisoft Connect")

            # Auth launches must stay native; the launcher invokes umu-run itself.
            await sm._clear_proton_compatibility(expected_appid)

            return True

        except Exception as e:
            logger.warning(f"[Ubisoft] Auth shortcut validation failed: {e}")
            return True  # Safe default — don't trigger recreation on error

    async def _fetch_auth_shortcut_artwork(self, unsigned_id: int, force: bool = False) -> None:
        """Download SteamGridDB artwork for the auth shortcut.

        Args:
            force: If True, skip the has_artwork check and always attempt download.
                   Used when recreating a deleted shortcut whose artwork may also
                   have been removed.
        """
        try:
            plugin = self.plugin_instance
            if not plugin or not hasattr(plugin, 'steamgriddb') or not plugin.steamgriddb:
                logger.debug("[Ubisoft] SteamGridDB client not available, skipping artwork")
                return

            if not force:
                # Check if artwork already exists (skip on force to handle partial/missing)
                if hasattr(plugin, 'has_artwork') and await plugin.has_artwork(unsigned_id):
                    logger.debug("[Ubisoft] Auth shortcut artwork already exists")
                    return

            # Determine which types are missing so we only download gaps
            only_types = None
            if not force and hasattr(plugin, 'get_missing_artwork_types'):
                missing = await plugin.get_missing_artwork_types(unsigned_id)
                if missing:
                    only_types = missing
                    logger.info(f"[Ubisoft] Auth shortcut artwork gap-fill: {missing}")

            logger.info(f"[Ubisoft] Fetching SteamGridDB artwork for Ubisoft Connect (force={force})")
            await plugin.steamgriddb.fetch_game_art(
                title="Ubisoft Connect",
                app_id=unsigned_id,
                only_types=only_types,
            )
        except Exception as e:
            logger.warning(f"[Ubisoft] Auth shortcut artwork fetch failed: {e}")

    async def _cleanup_legacy_auth_shortcut(self) -> None:
        """Remove the old .template auth shortcut from VDF and registry (migration)."""
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            return

        try:
            from py_modules.unifideck.shortcuts.shortcuts_manager import (
                load_shortcuts_registry, save_shortcuts_registry
            )

            registry = load_shortcuts_registry()
            if "ubisoft:.template" in registry:
                logger.info("[Ubisoft] Removing legacy .template auth shortcut from registry")
                del registry["ubisoft:.template"]
                save_shortcuts_registry(registry)

            sm = self.plugin_instance.shortcuts_manager
            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})
            removed = False
            for idx in list(shortcuts.keys()):
                if shortcuts[idx].get('LaunchOptions', '') == 'ubisoft:.template':
                    del shortcuts[idx]
                    removed = True
            if removed:
                await sm.write_shortcuts(shortcuts_data)
                logger.info("[Ubisoft] Removed legacy .template shortcut from shortcuts.vdf")

        except Exception as e:
            logger.warning(f"[Ubisoft] Legacy shortcut cleanup failed: {e}")

    async def _ensure_installer_cached(self) -> Optional[str]:
        """
        Download the UbisoftConnectInstaller.exe if not already cached.

        Returns path to cached installer, or None on failure.
        """
        os.makedirs(INSTALLER_CACHE_DIR, exist_ok=True)
        cached_path = os.path.join(INSTALLER_CACHE_DIR, INSTALLER_FILENAME)

        # Check if cached and valid (PE file starts with 'MZ')
        if os.path.exists(cached_path) and os.path.getsize(cached_path) > 1000:
            try:
                with open(cached_path, "rb") as f:
                    header = f.read(2)
                if header == b"MZ":
                    logger.info("[Ubisoft] Using cached installer")
                    return cached_path
            except Exception:
                pass

        # Download installer
        logger.info(f"[Ubisoft] Downloading installer from {INSTALLER_URL}...")
        try:
            import aiohttp

            session = aiohttp.ClientSession()
            try:
                async with session.get(
                    INSTALLER_URL,
                    timeout=aiohttp.ClientTimeout(total=600),  # 10 min timeout
                ) as resp:
                    if resp.status not in (200, 206):
                        logger.error(f"[Ubisoft] Installer download failed: HTTP {resp.status}")
                        return None

                    tmp_path = cached_path + ".tmp"
                    total = 0
                    with open(tmp_path, "wb") as f:
                        async for chunk in resp.content.iter_chunked(1024 * 1024):
                            f.write(chunk)
                            total += len(chunk)

                    os.replace(tmp_path, cached_path)
                    logger.info(f"[Ubisoft] Installer downloaded ({total / 1024 / 1024:.1f} MB)")
                    return cached_path
            finally:
                await session.close()

        except Exception as e:
            logger.error(f"[Ubisoft] Installer download failed: {e}")
            return None

    # ========================================================================
    # Per-Game Prefix Bootstrap
    # ========================================================================

    async def bootstrap_game_prefix(self, space_id: str) -> bool:
        """
        Ensure a per-game prefix exists with upc.exe installed.

        Uses template clone (Path B) if available, otherwise fresh install (Path A).

        Args:
            space_id: The game's space_id.

        Returns:
            True if prefix is ready, False otherwise.
        """
        prefix_path = os.path.join(PREFIXES_DIR, space_id)
        marker_path = os.path.join(prefix_path, BOOTSTRAP_MARKER)

        # Already bootstrapped?
        if os.path.exists(marker_path):
            if self._find_upc_exe(prefix_path):
                self._ensure_upc_auth_state_in_prefixes([prefix_path])
                return True

        # Path B: Clone from template (fast, ~30 seconds)
        if self._template_exists():
            logger.info(f"[Ubisoft] Cloning template prefix for {space_id}")
            try:
                os.makedirs(prefix_path, exist_ok=True)
                proc = await asyncio.create_subprocess_exec(
                    "rsync", "-a", f"{TEMPLATE_DIR}/", f"{prefix_path}/",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                )
                await proc.communicate()
                if proc.returncode == 0:
                    with open(marker_path, "w") as f:
                        f.write(f"cloned_from_template\ngame={space_id}\n")
                    self._ensure_upc_auth_state_in_prefixes([prefix_path])
                    logger.info(f"[Ubisoft] Prefix cloned for {space_id}")
                    return True
                else:
                    logger.error(f"[Ubisoft] rsync clone failed for {space_id}")
            except Exception as e:
                logger.error(f"[Ubisoft] Clone failed: {e}")

        # Path A: Fresh install (slow, ~5-10 minutes)
        logger.info(f"[Ubisoft] Fresh install of upc.exe for {space_id}")
        installer_path = await self._ensure_installer_cached()
        if not installer_path:
            return False

        try:
            os.makedirs(prefix_path, exist_ok=True)
            umu_run = self._find_umu_run()
            if not umu_run:
                return False

            env = self._build_umu_env(
                prefix_path, f"umu-ubisoft-{space_id}", f"ubisoft:{space_id}"
            )

            python_bin = self._find_python()
            logger.info(f"[Ubisoft] Running: {python_bin} {umu_run} {installer_path} /S")
            logger.info(f"[Ubisoft] WINEPREFIX={prefix_path} GAMEID={env.get('GAMEID')} PROTONPATH={env.get('PROTONPATH')}")
            proc = await asyncio.create_subprocess_exec(
                python_bin, umu_run, installer_path, "/S",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            stdout, stderr = await proc.communicate()

            stderr_text = stderr.decode(errors='replace')[:2000] if stderr else ''
            stdout_text = stdout.decode(errors='replace')[:1000] if stdout else ''
            logger.info(f"[Ubisoft] umu-run exited (rc={proc.returncode})")
            if stderr_text:
                logger.info(f"[Ubisoft] stderr: {stderr_text}")
            if stdout_text:
                logger.info(f"[Ubisoft] stdout: {stdout_text}")

            if self._find_upc_exe(prefix_path):
                with open(marker_path, "w") as f:
                    f.write(f"fresh_install\ngame={space_id}\n")

                self._ensure_upc_auth_state_in_prefixes([prefix_path])

                # Also create/update template if it doesn't exist
                if not self._template_exists():
                    logger.info("[Ubisoft] Creating template from first game prefix")
                    try:
                        os.makedirs(TEMPLATE_DIR, exist_ok=True)
                        proc = await asyncio.create_subprocess_exec(
                            "rsync", "-a", f"{prefix_path}/", f"{TEMPLATE_DIR}/",
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        await proc.communicate()
                        template_marker = os.path.join(TEMPLATE_DIR, BOOTSTRAP_MARKER)
                        with open(template_marker, "w") as f:
                            f.write("template\n")
                        self._backfill_hidden_prefix_session(TEMPLATE_DIR)
                    except Exception as e:
                        logger.warning(f"[Ubisoft] Template creation from game prefix failed: {e}")

                return True

            logger.error(f"[Ubisoft] upc.exe not found after fresh install for {space_id}")
            return False

        except Exception as e:
            logger.exception(f"[Ubisoft] Fresh install failed for {space_id}: {e}")
            return False

    # ========================================================================
    # UPC Session Injection
    # ========================================================================

    def inject_upc_session(self, prefix_path: str) -> bool:
        """
        Pre-inject UPC credential files into a game prefix so no login
        prompt appears.

        Syncs ConnectSecureStorage.dat, user.dat, and auth cache artifacts
        from the best available auth source (auth prefix preferred).
        Returns True if any credential files were synced.
        """
        source = self._find_best_credential_source()
        if not source:
            logger.warning("[Ubisoft] inject_upc_session: no credential source found")
            return False

        credentials_synced = False
        try:
            synced = self._sync_upc_credentials_to_prefix(source, prefix_path)
            artifact_synced = self._sync_upc_auth_artifacts_to_prefix(source, prefix_path)
            if synced:
                logger.info(f"[Ubisoft] inject_upc_session: synced {synced} credential file(s)")
                credentials_synced = True
            if artifact_synced:
                logger.info(
                    f"[Ubisoft] inject_upc_session: synced {artifact_synced} auth cache artifact(s)"
                )
                credentials_synced = True
        except Exception as e:
            logger.warning(f"[Ubisoft] inject_upc_session: auth sync failed: {e}")

        if not credentials_synced:
            logger.warning("[Ubisoft] inject_upc_session: no credentials synced")
        return credentials_synced

    def _iter_game_prefix_paths(self):
        """Yield all non-hidden Ubisoft game prefixes."""
        if not os.path.isdir(PREFIXES_DIR):
            return
        for entry in sorted(os.listdir(PREFIXES_DIR)):
            if entry.startswith("."):
                continue
            prefix_path = os.path.join(PREFIXES_DIR, entry)
            if os.path.isdir(prefix_path):
                yield prefix_path

    def _get_current_upc_session_token(self) -> Optional[str]:
        """Return the UPC session token from the session file, or None."""
        if os.path.isfile(UPC_SESSION_FILE):
            try:
                with open(UPC_SESSION_FILE) as f:
                    token = f.read().strip()
                if token:
                    return token
            except Exception:
                pass
        return None

    def _ensure_upc_auth_state_in_prefixes(self, prefix_paths: List[str]) -> int:
        """Ensure the current Ubisoft auth state is present in the target prefixes.

        Always calls inject_upc_session() even without a standalone session token,
        because it independently syncs credential files (ConnectSecureStorage.dat etc.)
        from the auth prefix — which is sufficient for UPC auto-login.
        """
        ensured = 0

        for prefix_path in prefix_paths:
            if not os.path.isdir(prefix_path):
                continue
            try:
                if self.inject_upc_session(prefix_path):
                    ensured += 1
            except Exception as e:
                logger.warning(
                    f"[Ubisoft] Failed to ensure auth state in {os.path.basename(prefix_path)}: {e}"
                )

        if ensured:
            logger.info(f"[Ubisoft] Ensured current auth state across {ensured} prefix(es)")
        return ensured

    def _write_upc_session_to_prefix(self, prefix_path: str, token: str) -> bool:
        """Write a restore_session token into a prefix's UPC settings.yml.

        Writes to ALL existing user dirs in both root and pfx/ layouts,
        so the token is available regardless of how Proton created the prefix.
        Only writes to user dirs that already exist (avoids creating wrong layout).
        """
        try:
            user_id = self.api.get_user_id() or ""
            config = (
                "user:\n"
                "  remember_me: true\n"
                f'  restore_session: "{token}"\n'
                f'  userId: "{user_id}"\n'
            )
            wrote_any = False
            for _prefix_root, user_home in _iter_prefix_user_homes(prefix_path):
                settings_dir = os.path.join(
                    user_home, "AppData", "Roaming", "Ubisoft", "Ubisoft Connect",
                )
                os.makedirs(settings_dir, exist_ok=True)
                settings_file = os.path.join(settings_dir, "settings.yml")
                with open(settings_file, "w") as f:
                    f.write(config)
                wrote_any = True
            if wrote_any:
                logger.info(f"[Ubisoft] Wrote session to prefix {os.path.basename(prefix_path)}")
            return wrote_any
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to write session to prefix {prefix_path}: {e}")
            return False

    def _sync_upc_credentials_to_prefix(self, source_prefix: str, target_prefix: str) -> int:
        """Copy UPC credential files from source prefix to target prefix.

        Copies ConnectSecureStorage.dat and user.dat into all user homes
        (both root and pfx/ layouts) so UPC can validate the session token.
        Skips sync when MachineGuid differs (DPAPI files won't decrypt).
        Returns number of files synced.
        """
        if os.path.realpath(source_prefix) == os.path.realpath(target_prefix):
            return 0

        # DPAPI-encrypted files require matching MachineGuid
        source_guid = self._read_prefix_machine_guid(source_prefix)
        target_guid = self._read_prefix_machine_guid(target_prefix)
        if source_guid and target_guid and source_guid != target_guid:
            logger.warning(
                f"[Ubisoft] MachineGuid mismatch: source={source_guid[:8]}... "
                f"target={target_guid[:8]}... — skipping DPAPI credential sync"
            )
            return 0

        # Collect source credential files (first valid one per filename, pfx/ first)
        source_files: Dict[str, str] = {}
        for _root, user_home in _iter_prefix_user_homes(source_prefix, pfx_first=True):
            for fname in _UPC_CREDENTIAL_FILES:
                if fname in source_files:
                    continue
                src = os.path.join(user_home, _UPC_LOCAL_SUBDIR, fname)
                if os.path.isfile(src) and os.path.getsize(src) > 10:
                    source_files[fname] = src

        if not source_files:
            return 0

        synced = 0
        for _root, user_home in _iter_prefix_user_homes(target_prefix):
            target_dir = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
            for fname, src_path in source_files.items():
                dst_path = os.path.join(target_dir, fname)
                if os.path.isfile(dst_path):
                    try:
                        if self._hash_upc_artifact(src_path) == self._hash_upc_artifact(dst_path):
                            continue
                    except Exception:
                        pass
                os.makedirs(target_dir, exist_ok=True)
                shutil.copy2(src_path, dst_path)
                synced += 1
        return synced

    @staticmethod
    def _hash_upc_artifact(path: str) -> str:
        """Build a stable content hash for a file or directory."""
        digest = hashlib.sha256()

        if os.path.isdir(path):
            for root, _dirs, files in os.walk(path):
                files.sort()
                for name in files:
                    file_path = os.path.join(root, name)
                    rel_path = os.path.relpath(file_path, path)
                    digest.update(rel_path.encode("utf-8"))
                    with open(file_path, "rb") as f:
                        for chunk in iter(lambda: f.read(1024 * 1024), b""):
                            digest.update(chunk)
        elif os.path.isfile(path):
            with open(path, "rb") as f:
                for chunk in iter(lambda: f.read(1024 * 1024), b""):
                    digest.update(chunk)

        return digest.hexdigest()

    def _collect_upc_auth_artifact_sources(self, source_prefix: str) -> Dict[str, str]:
        """Collect auth-adjacent cache/config artifacts from the source prefix."""
        artifacts: Dict[str, str] = {}
        for _root, user_home in _iter_prefix_user_homes(source_prefix, pfx_first=True):
            local_root = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
            for rel_path in _UPC_AUTH_CACHE_ARTIFACTS:
                if rel_path in artifacts:
                    continue
                candidate = os.path.join(local_root, rel_path)
                if os.path.isdir(candidate) or os.path.isfile(candidate):
                    artifacts[rel_path] = candidate
        return artifacts

    def _sync_upc_auth_artifacts_to_prefix(self, source_prefix: str, target_prefix: str) -> int:
        """Copy auth-adjacent Ubisoft cache/config artifacts into a target prefix."""
        if os.path.realpath(source_prefix) == os.path.realpath(target_prefix):
            return 0

        source_artifacts = self._collect_upc_auth_artifact_sources(source_prefix)
        if not source_artifacts:
            return 0

        synced = 0
        for _root, user_home in _iter_prefix_user_homes(target_prefix):
            target_local_root = os.path.join(user_home, _UPC_LOCAL_SUBDIR)
            for rel_path, src_path in source_artifacts.items():
                dst_path = os.path.join(target_local_root, rel_path)
                if os.path.exists(dst_path):
                    try:
                        if self._hash_upc_artifact(src_path) == self._hash_upc_artifact(dst_path):
                            continue
                    except Exception:
                        pass

                os.makedirs(os.path.dirname(dst_path), exist_ok=True)
                if os.path.isdir(dst_path):
                    shutil.rmtree(dst_path, ignore_errors=True)
                elif os.path.exists(dst_path):
                    os.remove(dst_path)

                if os.path.isdir(src_path):
                    shutil.copytree(src_path, dst_path)
                else:
                    shutil.copy2(src_path, dst_path)
                synced += 1

        return synced

    def _find_best_credential_source(self) -> Optional[str]:
        """Find the prefix with the freshest UPC credentials.

        Prefers .upc-auth if it has any valid ConnectSecureStorage.dat,
        falls back to whichever prefix has the most recently modified one.
        """
        # Check auth prefix first (preferred source) — pfx/ layout first
        if os.path.isdir(AUTH_PREFIX_DIR):
            for _root, user_home in _iter_prefix_user_homes(AUTH_PREFIX_DIR, pfx_first=True):
                css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
                if os.path.isfile(css) and os.path.getsize(css) > 10:
                    return AUTH_PREFIX_DIR  # Always prefer auth if it has valid CSS

        # Fallback: scan all prefixes for most recently modified credentials
        best_path = None
        best_mtime: float = 0
        if os.path.isdir(PREFIXES_DIR):
            for entry in os.listdir(PREFIXES_DIR):
                prefix = os.path.join(PREFIXES_DIR, entry)
                if not os.path.isdir(prefix):
                    continue
                for _root, user_home in _iter_prefix_user_homes(prefix, pfx_first=True):
                    css = os.path.join(user_home, _UPC_LOCAL_SUBDIR, "ConnectSecureStorage.dat")
                    if os.path.isfile(css) and os.path.getsize(css) > 10:
                        mtime = os.path.getmtime(css)
                        if mtime > best_mtime:
                            best_mtime = mtime
                            best_path = prefix
                        break  # Use first valid user_home per prefix
        return best_path

    def _propagate_upc_credentials_to_all_prefixes(self) -> int:
        """Sync UPC credential files from the best source to all game prefixes.

        Called after auth capture and as a retroactive sync.
        Returns total number of files synced.
        """
        source = self._find_best_credential_source()
        if not source:
            logger.info("[Ubisoft] No credential source found for propagation")
            return 0

        total = 0
        if not os.path.isdir(PREFIXES_DIR):
            return 0
        for entry in os.listdir(PREFIXES_DIR):
            prefix_path = os.path.join(PREFIXES_DIR, entry)
            if not os.path.isdir(prefix_path):
                continue
            try:
                total += self._sync_upc_credentials_to_prefix(source, prefix_path)
            except Exception as e:
                logger.warning(f"[Ubisoft] Failed to sync credentials to {entry}: {e}")
        if total:
            logger.info(f"[Ubisoft] Propagated {total} credential file(s) across prefixes")
        return total

    def _propagate_upc_auth_artifacts_to_all_prefixes(self) -> int:
        """Sync UPC auth cache artifacts from the best source to all prefixes."""
        source = self._find_best_credential_source()
        if not source:
            return 0

        total = 0
        if not os.path.isdir(PREFIXES_DIR):
            return 0
        for entry in os.listdir(PREFIXES_DIR):
            prefix_path = os.path.join(PREFIXES_DIR, entry)
            if not os.path.isdir(prefix_path):
                continue
            try:
                total += self._sync_upc_auth_artifacts_to_prefix(source, prefix_path)
            except Exception as e:
                logger.warning(f"[Ubisoft] Failed to sync auth artifacts to {entry}: {e}")
        if total:
            logger.info(f"[Ubisoft] Propagated {total} auth cache artifact(s) across prefixes")
        return total

    def _read_prefix_restore_session(self, prefix_path: str) -> Optional[str]:
        """Read the current restore_session token from a prefix's settings.yml."""
        for _prefix_root, user_home in _iter_prefix_user_homes(prefix_path, pfx_first=True):
            settings_file = os.path.join(
                user_home, "AppData", "Roaming", "Ubisoft",
                "Ubisoft Connect", "settings.yml"
            )
            if not os.path.isfile(settings_file):
                continue
            try:
                with open(settings_file) as f:
                    content = f.read()
                m = re.search(r'restore_session:\s+"([^"]+)"', content)
                if m:
                    return m.group(1)
            except Exception:
                continue
        return None

    def _propagate_upc_session_to_all_prefixes(self, _token: Optional[str] = None) -> None:
        """Propagate credential files and auth artifacts to all game prefixes.

        UPC stores auth in ConnectSecureStorage.dat (DPAPI-encrypted), not in
        settings.yml tokens. This method copies credential files and auth cache
        artifacts from the auth prefix to every game prefix.
        """
        self._propagate_upc_credentials_to_all_prefixes()
        self._propagate_upc_auth_artifacts_to_all_prefixes()

    def _capture_upc_session(self, prefix_path: str) -> Optional[str]:
        """
        Detect fresh UPC credentials (ConnectSecureStorage.dat) in a prefix
        after a successful login and propagate them.

        UPC stores auth entirely in DPAPI-encrypted credential files
        (ConnectSecureStorage.dat, user.dat) — not in settings.yml tokens.
        Detecting a new/changed ConnectSecureStorage.dat is the auth signal.

        Returns a non-None sentinel string when new credentials are detected
        (callers check truthiness), or None if no change. Credential sync
        to .template and .upc-auth happens whenever valid credentials exist.
        """
        if not self._has_valid_credentials(prefix_path):
            return None

        new_mtime = self._get_credential_mtime(prefix_path)
        if not new_mtime:
            return None

        # Compare with stored mtime to detect changes
        stored_mtime = 0.0
        if os.path.isfile(UPC_SESSION_FILE):
            try:
                with open(UPC_SESSION_FILE, "r") as f:
                    content = f.read().strip()
                if content.startswith("credential_mtime:"):
                    stored_mtime = float(content.split(":", 1)[1])
            except Exception:
                pass

        credentials_changed = new_mtime > stored_mtime

        if credentials_changed:
            try:
                os.makedirs(DATA_DIR, exist_ok=True)
                with open(UPC_SESSION_FILE, "w") as f:
                    f.write(f"credential_mtime:{new_mtime}\n")
                logger.info("[Ubisoft] Detected new UPC credentials (ConnectSecureStorage.dat)")
            except Exception as e:
                logger.warning(f"[Ubisoft] Failed to write credential marker: {e}")

        # Always sync credentials + artifacts to template and auth prefix,
        # even when nothing changed — UPC may have refreshed files.
        for target in [TEMPLATE_DIR, AUTH_PREFIX_DIR]:
            if not os.path.isdir(target):
                continue
            if os.path.realpath(target) == os.path.realpath(prefix_path):
                continue
            try:
                self._sync_upc_credentials_to_prefix(prefix_path, target)
                self._sync_upc_auth_artifacts_to_prefix(prefix_path, target)
            except Exception as e:
                logger.warning(
                    f"[Ubisoft] Failed to sync capture to {os.path.basename(target)}: {e}"
                )

        if credentials_changed:
            logger.info("[Ubisoft] Captured UPC credentials → template + auth prefix updated")

        return "credentials_captured" if credentials_changed else None

    async def connect_ubisoft_account(self) -> Dict[str, Any]:
        """
        Launch Ubisoft Connect in the dedicated auth prefix so the user can
        log in once without requiring any previously installed Ubisoft game.

        Captures the restore_session token UPC writes after login and propagates
        it to all existing game prefixes. Future cloned prefixes inherit it via rsync.
        Exposed as a backend RPC for the plugin settings "Connect" button.
        """
        self.queue_auth_assets_ensure("connect-account")

        upc_path = await self._ensure_auth_prefix()
        umu_run = self._find_umu_run()
        if not upc_path or not umu_run:
            return {"success": False, "error": "UPC not found in auth prefix"}

        prefix_path = AUTH_PREFIX_DIR
        connect_path = self._find_connect_exe(prefix_path)
        if not connect_path:
            return {"success": False, "error": "UbisoftConnect.exe not found in auth prefix"}

        python_bin = self._find_python()
        env = self._build_umu_env(prefix_path, "umu-ubisoft-auth", AUTH_SHORTCUT_STORE_ID)

        logger.info("[Ubisoft] Launching Ubisoft Connect in auth prefix for login")
        proc = await asyncio.create_subprocess_exec(
            python_bin, umu_run, connect_path,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        loop = asyncio.get_event_loop()
        start = loop.time()
        timeout_seconds = 600  # 10 min
        captured_token: Optional[str] = None

        while loop.time() - start < timeout_seconds:
            if proc.returncode is not None:
                break

            # Close auth UPC as soon as a new restore_session token is captured.
            captured_token = self._capture_upc_session(prefix_path)
            if captured_token:
                logger.info("[Ubisoft] UPC session captured during auth; closing auth launcher")
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except Exception:
                    try:
                        proc.kill()
                        await asyncio.wait_for(proc.wait(), timeout=5)
                    except Exception:
                        pass
                break

            await asyncio.sleep(2)
        else:
            logger.warning("[Ubisoft] Auth launcher timed out")
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                try:
                    proc.kill()
                    await asyncio.wait_for(proc.wait(), timeout=5)
                except Exception:
                    pass

        if not captured_token:
            captured_token = self._capture_upc_session(prefix_path)

        if captured_token:
            # Propagate to all existing game prefixes
            self._propagate_upc_session_to_all_prefixes(captured_token)
            self.queue_auth_assets_ensure("post-connect-account")
            return {"success": True, "message": "Ubisoft account connected successfully"}

        return {
            "success": False,
            "error": "Login not detected. Please log in and close Ubisoft Connect.",
        }

    # ========================================================================
    # Auth Shortcut Context & Session Monitor
    # ========================================================================

    async def _auth_shortcut_exists_in_vdf(self) -> bool:
        """Read-only check whether the auth shortcut exists in VDF.

        Used by get_ubisoft_auth_shortcut_context() to detect if the user
        deleted the shortcut since plugin startup, without writing to VDF
        (which would cause a RunGame race condition).
        """
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            return True  # Can't check — assume present
        try:
            from py_modules.unifideck.shortcuts.launch_options import get_full_id
            sm = self.plugin_instance.shortcuts_manager
            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})
            return any(
                get_full_id(s.get('LaunchOptions', '')) == AUTH_SHORTCUT_STORE_ID
                for s in shortcuts.values()
            )
        except Exception:
            return True  # Can't check — assume present

    async def get_ubisoft_auth_shortcut_context(self) -> Dict[str, Any]:
        """Return the auth shortcut's appid so the frontend can call RunGame().

        The frontend launches the shortcut via RunGame using temporary launch
        options, but this still returns the underlying shortcut appid.

        Recovery path: when the shortcuts registry was cleared (e.g. fresh
        install over existing VDF entries), this scans VDF to re-discover the
        shortcut and re-populate the registry before falling back to full
        creation.
        """
        from py_modules.unifideck.shortcuts.shortcuts_manager import (
            load_shortcuts_registry,
        )

        registry = load_shortcuts_registry()
        entry = registry.get(AUTH_SHORTCUT_STORE_ID)
        if entry and entry.get("appid_unsigned"):
            # Verify the shortcut still exists in VDF (read-only check).
            # User may have deleted the shortcut since plugin startup.
            if await self._auth_shortcut_exists_in_vdf():
                logger.info(
                    f"[Ubisoft] Auth shortcut context: appid={entry['appid_unsigned']}"
                )
                return {
                    "success": True,
                    "appid_unsigned": entry["appid_unsigned"],
                    "launch_wait_ms": 0,
                }

            # Shortcut was deleted — recreate it
            logger.info("[Ubisoft] Auth shortcut in registry but missing from VDF, recreating")
            unsigned_id = await self._ensure_ubisoft_auth_shortcut()
            if unsigned_id:
                return {
                    "success": True,
                    "appid_unsigned": unsigned_id,
                    "launch_wait_ms": AUTH_SHORTCUT_LAUNCH_WAIT_MS,
                }

        # Registry miss — the shortcut may still exist in VDF (e.g. registry
        # was cleared but user never deleted the Steam shortcut).  Validate
        # scans VDF, fixes up fields, and re-registers in the registry.
        logger.info("[Ubisoft] Auth shortcut not in registry, scanning VDF for recovery")
        vdf_found = await self._validate_auth_shortcut()
        if vdf_found:
            registry = load_shortcuts_registry()
            entry = registry.get(AUTH_SHORTCUT_STORE_ID)
            if entry and entry.get("appid_unsigned"):
                logger.info(
                    f"[Ubisoft] Auth shortcut recovered from VDF: appid={entry['appid_unsigned']}"
                )
                return {
                    "success": True,
                    "appid_unsigned": entry["appid_unsigned"],
                    "launch_wait_ms": AUTH_SHORTCUT_LAUNCH_WAIT_MS,
                }

        # Not in registry or VDF — create the shortcut entry immediately.
        unsigned_id = await self._ensure_ubisoft_auth_shortcut()
        if not unsigned_id:
            return {"success": False, "error": "Auth shortcut not ready"}
        return {
            "success": True,
            "appid_unsigned": unsigned_id,
            "launch_wait_ms": AUTH_SHORTCUT_LAUNCH_WAIT_MS,
        }

    async def start_ubisoft_auth_session_monitor(self) -> Dict[str, Any]:
        """Start a background task that polls for UPC session capture in the auth prefix.

        Idempotent — cancels any existing monitor before starting a new one.
        """
        # Cancel existing monitor
        if hasattr(self, '_auth_monitor_task') and self._auth_monitor_task and not self._auth_monitor_task.done():
            self._auth_monitor_task.cancel()
            try:
                await self._auth_monitor_task
            except (asyncio.CancelledError, Exception):
                pass

        self._auth_session_captured = False
        self._auth_monitor_task = asyncio.create_task(self._auth_session_monitor_loop())
        logger.info("[Ubisoft] Started auth session monitor")
        return {"success": True}

    async def _auth_session_monitor_loop(self) -> None:
        """Poll auth prefix for session token capture (background task)."""
        timeout_seconds = 1800
        poll_interval = 2
        elapsed = 0.0

        while elapsed < timeout_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            captured_token = self._capture_upc_session(AUTH_PREFIX_DIR)
            if captured_token:
                logger.info("[Ubisoft] Auth session monitor: token captured!")
                self._propagate_upc_session_to_all_prefixes(captured_token)
                self.queue_auth_assets_ensure("post-auth-session-capture")
                self._auth_session_captured = True

                # Trigger library sync now that auth is complete
                if self.plugin_instance:
                    logger.info("[Ubisoft] Triggering library sync after UPC auth")
                    asyncio.create_task(
                        self.plugin_instance.request_auth_sync(
                            force=True,
                            source='auth:ubisoft',
                        )
                    )
                return

        logger.warning("[Ubisoft] Auth session monitor timed out after 1800s")

    def check_ubisoft_auth_session_status(self) -> Dict[str, Any]:
        """Check whether the auth session monitor has captured a token."""
        captured = getattr(self, '_auth_session_captured', False)
        monitoring = (
            hasattr(self, '_auth_monitor_task')
            and self._auth_monitor_task
            and not self._auth_monitor_task.done()
        )
        return {"captured": captured, "monitoring": monitoring}

    def sync_ubisoft_credentials(self) -> Dict[str, Any]:
        """Retroactively sync UPC credentials and session token to all prefixes.

        Exposed as a backend RPC. Finds the freshest credential source and
        propagates both binary credential files and the session token to
        every Ubisoft prefix (including template).
        """
        try:
            cred_count = self._propagate_upc_credentials_to_all_prefixes()

            # Also propagate session token if available
            target_prefixes: List[str] = []
            for hidden_prefix in [AUTH_PREFIX_DIR, TEMPLATE_DIR]:
                if os.path.isdir(hidden_prefix):
                    target_prefixes.append(hidden_prefix)
            target_prefixes.extend(list(self._iter_game_prefix_paths() or []))
            token_count = self._ensure_upc_auth_state_in_prefixes(target_prefixes)

            return {
                "success": True,
                "credentials_synced": cred_count,
                "token_propagated": token_count > 0,
            }
        except Exception as e:
            logger.error(f"[Ubisoft] Retroactive credential sync failed: {e}")
            return {"success": False, "error": str(e)}

    # ========================================================================
    # Installed Game Detection
    # ========================================================================

    def _detect_installed_game(
        self, space_id: str, prefix_path: str
    ) -> Optional[Dict[str, Any]]:
        """
        Detect if a game is installed in its per-game prefix.

        Checks: .unifideck_ubisoft marker, uplay_install.state, registry keys.
        """
        from .ubisoft_parser import check_install_state

        known_name = self._get_game_name(space_id) or ""
        normalized_known_name = self._normalize_for_matching(known_name) if known_name else ""

        prefix_game_roots = [
            os.path.join(
                prefix_path,
                "drive_c",
                "Program Files (x86)",
                "Ubisoft",
                "Ubisoft Game Launcher",
                "games",
            ),
            os.path.join(
                prefix_path,
                "pfx",
                "drive_c",
                "Program Files (x86)",
                "Ubisoft",
                "Ubisoft Game Launcher",
                "games",
            ),
        ]
        external_game_roots = self._get_external_game_roots()

        def _build_result(game_dir: str, title_hint: str = "") -> Dict[str, Any]:
            exe = self.find_game_executable(game_dir) or ""
            title = title_hint or os.path.basename(game_dir)
            return {
                "space_id": space_id,
                "executable": exe,
                "install_path": game_dir,
                "work_dir": game_dir,
                "title": title,
            }

        # Method 1: .unifideck_ubisoft marker lookup (authoritative when present)
        marker_roots = [*prefix_game_roots, *external_game_roots]
        for base_dir in marker_roots:
            if not os.path.isdir(base_dir):
                continue
            for folder in os.listdir(base_dir):
                game_dir = os.path.join(base_dir, folder)
                if not os.path.isdir(game_dir):
                    continue
                marker_path = os.path.join(game_dir, ".unifideck_ubisoft")
                if not os.path.isfile(marker_path):
                    continue
                try:
                    with open(marker_path, "r", encoding="utf-8", errors="replace") as f:
                        marker_data = json.load(f)
                    if marker_data.get("space_id") != space_id:
                        continue

                    install_path = marker_data.get("install_path") or game_dir
                    executable = marker_data.get("executable", "") or ""
                    if executable and not os.path.isabs(executable):
                        executable = os.path.join(install_path, executable)
                    if not executable or not os.path.exists(executable):
                        executable = self.find_game_executable(install_path) or ""

                    return {
                        "space_id": space_id,
                        "executable": executable,
                        "install_path": install_path,
                        "work_dir": install_path,
                        "title": marker_data.get("game_title") or known_name or folder,
                    }
                except Exception:
                    continue

        # Method 2: Prefix-local install state (most reliable for per-game Ubisoft prefixes)
        prefix_state_candidates: List[str] = []
        for base_dir in prefix_game_roots:
            if not os.path.isdir(base_dir):
                continue
            for folder in os.listdir(base_dir):
                game_dir = os.path.join(base_dir, folder)
                if not os.path.isdir(game_dir):
                    continue
                state_file = os.path.join(game_dir, "uplay_install.state")
                if check_install_state(state_file):
                    prefix_state_candidates.append(game_dir)

        if prefix_state_candidates:
            if normalized_known_name:
                for game_dir in prefix_state_candidates:
                    normalized_folder = self._normalize_for_matching(os.path.basename(game_dir))
                    if (
                        normalized_folder == normalized_known_name
                        or normalized_folder in normalized_known_name
                        or normalized_known_name in normalized_folder
                    ):
                        return _build_result(game_dir, known_name or os.path.basename(game_dir))
            # Fallback: a per-game prefix should usually contain only one installed title.
            first_dir = prefix_state_candidates[0]
            return _build_result(first_dir, known_name or os.path.basename(first_dir))

        # Method 3: External install dirs via install state + name match
        # (avoid false positives by requiring a known title match)
        if normalized_known_name:
            for base_dir in external_game_roots:
                if not os.path.isdir(base_dir):
                    continue
                for folder in os.listdir(base_dir):
                    game_dir = os.path.join(base_dir, folder)
                    if not os.path.isdir(game_dir):
                        continue
                    state_file = os.path.join(game_dir, "uplay_install.state")
                    if not check_install_state(state_file):
                        continue
                    normalized_folder = self._normalize_for_matching(folder)
                    if (
                        normalized_folder != normalized_known_name
                        and normalized_folder not in normalized_known_name
                        and normalized_known_name not in normalized_folder
                    ):
                        continue
                    return _build_result(game_dir, known_name or folder)

        # Method 4: Registry InstallDir for specific install_id
        # Handles custom install locations chosen by the user in UPC.
        # Scoped to the exact install_id for this space_id to prevent
        # false positives — we never blind-scan all registry entries.
        install_id = self.resolve_install_id(space_id)
        if install_id:
            install_id_section = (
                f"Installs\\\\{install_id}]"
            )
            for reg_name in ("pfx/system.reg", "system.reg"):
                reg_path = os.path.join(prefix_path, reg_name)
                if not os.path.isfile(reg_path):
                    continue
                try:
                    with open(reg_path, "r", encoding="utf-8", errors="replace") as f:
                        reg_content = f.read()
                    # Find only the section for our specific install_id
                    for m in re.finditer(
                        r'\[Software\\\\(?:Wow6432Node\\\\)?Ubisoft\\\\Launcher\\\\Installs\\\\'
                        + re.escape(install_id)
                        + r'\]'
                        r'[^\[]*?"InstallDir"\s*=\s*"([^"]*)"',
                        reg_content,
                        re.DOTALL,
                    ):
                        install_dir_raw = m.group(1).replace("\\\\", "/")
                        linux_path = self._wine_path_to_linux(
                            install_dir_raw, prefix_path
                        )
                        if not linux_path or not os.path.isdir(linux_path):
                            continue
                        # Validate: must have install state or look like a game
                        state_file = os.path.join(
                            linux_path, "uplay_install.state"
                        )
                        if not (
                            check_install_state(state_file)
                            or self._looks_like_game_install(linux_path)
                        ):
                            continue
                        logger.info(
                            f"[Ubisoft] Method 4: detected install via registry "
                            f"InstallDir for {install_id}: {linux_path[:80]}"
                        )
                        result = _build_result(
                            linux_path,
                            known_name or os.path.basename(linux_path),
                        )
                        # Write marker for fast future lookups (Method 1)
                        marker_path = os.path.join(
                            linux_path, ".unifideck_ubisoft"
                        )
                        if not os.path.exists(marker_path):
                            try:
                                marker_data = {
                                    "space_id": space_id,
                                    "install_path": linux_path,
                                    "game_title": (
                                        known_name
                                        or os.path.basename(linux_path)
                                    ),
                                }
                                with open(marker_path, "w") as mf:
                                    json.dump(marker_data, mf)
                            except Exception:
                                pass
                        return result
                except Exception:
                    continue

        return None

    # ========================================================================
    # Utility Methods
    # ========================================================================

    def _build_umu_env(
        self, wineprefix: str, gameid: str, store_game_id: Optional[str] = None
    ) -> dict:
        """Build a clean environment for umu-run, free of Steam/Decky interference.

        The Decky plugin inherits Steam's env vars (STEAM_COMPAT_DATA_PATH,
        SteamAppId, etc.) which can confuse umu-run into using wrong prefixes
        or skipping execution. We build a minimal env with only what's needed.

        NOTE: Decky Loader runs as a systemd service without display env vars.
        We must detect them from the active user session (Steam/Gamescope).
        """
        home = os.environ.get("HOME", os.path.expanduser("~"))
        uid = os.getuid()
        env = {
            "HOME": home,
            "USER": os.environ.get("USER", "deck"),
            "PATH": os.environ.get("PATH", "/usr/local/bin:/usr/bin:/bin"),
            "LANG": os.environ.get("LANG", "en_US.UTF-8"),
            "XDG_RUNTIME_DIR": os.environ.get("XDG_RUNTIME_DIR", f"/run/user/{uid}"),
            "XDG_DATA_HOME": os.environ.get("XDG_DATA_HOME", os.path.join(home, ".local", "share")),
            "WINEPREFIX": wineprefix,
            "GAMEID": gameid,
            "STORE": "ubisoft",
            "PROTON_VERB": "waitforexitandrun",
        }

        resolved_store_game_id = store_game_id or self._derive_store_game_id(gameid)
        env.update(self._build_steam_window_env(resolved_store_game_id))

        proton_path = self._resolve_proton_path_for_gameid(gameid, resolved_store_game_id)
        if proton_path:
            env["PROTONPATH"] = proton_path

        # Display env: Decky runs as a systemd service so os.environ may not
        # have DISPLAY etc.  Detect from the running session if needed.
        display_vars = self._detect_display_env()
        env.update(display_vars)

        return env

    def _derive_store_game_id(self, gameid: str) -> Optional[str]:
        """Derive store:game_id from a Ubisoft UMU GAMEID string."""
        prefix = "umu-ubisoft-"
        if not gameid.startswith(prefix):
            return None
        suffix = gameid[len(prefix):]
        if not suffix or suffix in {"template", "auth", "warmup", "login"}:
            return None
        return f"ubisoft:{suffix}"

    def _load_shortcuts_registry(self) -> Dict[str, Any]:
        """Load shortcuts registry used for shortcut appid lookup."""
        try:
            if os.path.isfile(SHORTCUTS_REGISTRY_PATH):
                with open(SHORTCUTS_REGISTRY_PATH, "r") as f:
                    data = json.load(f)
                    if isinstance(data, dict):
                        return data
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed loading shortcuts registry: {e}")
        return {}

    @staticmethod
    def _parse_positive_int(value: Any) -> Optional[int]:
        """Parse a value as a positive integer."""
        try:
            parsed = int(value)
        except (TypeError, ValueError):
            return None
        return parsed if parsed > 0 else None

    def _resolve_shortcut_appid(self, store_game_id: Optional[str]) -> Optional[int]:
        """Resolve an unsigned shortcut appid for Steam/gamescope window matching."""
        registry = self._load_shortcuts_registry()

        if store_game_id:
            entry = registry.get(store_game_id, {})
            appid = self._parse_positive_int(entry.get("appid_unsigned"))
            if appid:
                return appid
        else:
            for key, entry in registry.items():
                if not isinstance(key, str) or not key.startswith("ubisoft:"):
                    continue
                if not isinstance(entry, dict):
                    continue
                appid = self._parse_positive_int(entry.get("appid_unsigned"))
                if appid:
                    return appid

        for env_var in ("SteamAppId", "SteamGameId", "STEAM_COMPAT_APP_ID"):
            appid = self._parse_positive_int(os.environ.get(env_var))
            if appid:
                return appid

        if store_game_id:
            for key, entry in registry.items():
                if not isinstance(key, str) or not key.startswith("ubisoft:"):
                    continue
                if not isinstance(entry, dict):
                    continue
                appid = self._parse_positive_int(entry.get("appid_unsigned"))
                if appid:
                    return appid

        return None

    def _build_steam_window_env(self, store_game_id: Optional[str]) -> Dict[str, str]:
        """
        Build Steam app-id env vars so gamescope can attach UMU windows to Steam UI.

        Mirrors bin/unifideck-launcher behavior for SteamGameId/SteamAppId and
        UMU_STEAM_GAME_ID, which helps launcher/login/install windows appear in
        gaming mode instead of launching invisibly in the background.
        """
        appid = self._resolve_shortcut_appid(store_game_id)
        if appid:
            encoded = str((appid << 32) | 0x02000000)
            logger.info(
                f"[Ubisoft] Steam window env: appid={appid} store_game_id={store_game_id or '<none>'}"
            )
            appid_str = str(appid)
            return {
                "SteamGameId": appid_str,
                "STEAM_COMPAT_APP_ID": appid_str,
                "SteamAppId": appid_str,
                "UMU_STEAM_GAME_ID": encoded,
            }

        logger.info("[Ubisoft] Steam window env: no shortcut appid resolved, using 0")
        return {
            "SteamGameId": "0",
            "STEAM_COMPAT_APP_ID": "0",
            "SteamAppId": "0",
            "UMU_STEAM_GAME_ID": "0",
        }

    def _resolve_proton_path_for_gameid(
        self, gameid: str, store_game_id: Optional[str] = None
    ) -> Optional[str]:
        """Resolve PROTONPATH with launcher-matching priority for Ubisoft flows."""
        env_protonpath = os.environ.get("PROTONPATH", "").strip()
        if env_protonpath and os.path.isdir(env_protonpath):
            return env_protonpath

        try:
            from ..compat.proton_tools import (
                get_compat_tool_for_game,
                get_saved_proton_tool,
                resolve_proton_path,
            )
        except ImportError as e:
            logger.warning(f"[Ubisoft] Proton tools import failed: {e}")
            return self._find_proton_path()

        env_proton = os.environ.get("PROTON", "").strip()
        if env_proton:
            resolved = resolve_proton_path(env_proton)
            if resolved:
                logger.info(f"[Ubisoft] Using PROTON env override: {env_proton}")
                return resolved
            logger.warning(f"[Ubisoft] PROTON={env_proton} could not be resolved")

        resolved_store_game_id = store_game_id or self._derive_store_game_id(gameid)

        if resolved_store_game_id:
            saved_tool = get_saved_proton_tool(resolved_store_game_id)
            if saved_tool:
                saved_path = resolve_proton_path(saved_tool)
                if saved_path:
                    logger.info(
                        f"[Ubisoft] Using saved Proton setting for {resolved_store_game_id}: {saved_tool}"
                    )
                    return saved_path
                logger.warning(
                    f"[Ubisoft] Saved Proton tool for {resolved_store_game_id} not found: {saved_tool}"
                )

            compat_info = get_compat_tool_for_game(resolved_store_game_id)
            if compat_info.get("success"):
                compat_tool = (compat_info.get("tool_name") or "").strip()
                if compat_tool and not compat_info.get("is_linux_runtime"):
                    compat_path = resolve_proton_path(compat_tool)
                    if compat_path:
                        logger.info(
                            f"[Ubisoft] Using Steam compat tool for {resolved_store_game_id}: {compat_tool}"
                        )
                        return compat_path

        return self._find_proton_path()

    def _detect_display_env(self) -> dict:
        """Detect display environment variables for GUI subprocess spawning.

        Checks os.environ first, then falls back to reading from a running
        Steam or Gamescope process (since Decky Loader's systemd service
        does not inherit the user's graphical session environment).
        """
        result: dict = {}
        targets = ["DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XAUTHORITY"]

        # First pass: check our own env
        for var in targets:
            val = os.environ.get(var)
            if val:
                result[var] = val

        if result.get("DISPLAY") or result.get("WAYLAND_DISPLAY"):
            return result

        # Fallback: read from a running Steam or gamescope-session process
        try:
            for proc_name in ["steam", "gamescope-session"]:
                pids = subprocess.run(
                    ["pgrep", "-u", str(os.getuid()), "-x", proc_name],
                    capture_output=True, text=True, timeout=5,
                ).stdout.strip().split("\n")
                for pid in pids:
                    pid = pid.strip()
                    if not pid:
                        continue
                    try:
                        env_path = f"/proc/{pid}/environ"
                        with open(env_path, "rb") as f:
                            env_bytes = f.read()
                        for entry in env_bytes.split(b"\x00"):
                            decoded = entry.decode("utf-8", errors="replace")
                            if "=" in decoded:
                                k, v = decoded.split("=", 1)
                                if k in targets and k not in result:
                                    result[k] = v
                        if result.get("DISPLAY") or result.get("WAYLAND_DISPLAY"):
                            logger.info(f"[Ubisoft] Display env detected from PID {pid} ({proc_name}): "
                                        f"DISPLAY={result.get('DISPLAY')}")
                            return result
                    except (PermissionError, FileNotFoundError, OSError):
                        continue
        except Exception as e:
            logger.debug(f"[Ubisoft] Display env detection error: {e}")

        # Last resort: common Steam Deck defaults
        if not result.get("DISPLAY"):
            result["DISPLAY"] = ":0"
            logger.info("[Ubisoft] Using fallback DISPLAY=:0")
        xauth = os.path.join(os.path.expanduser("~"), ".Xauthority")
        if not result.get("XAUTHORITY") and os.path.isfile(xauth):
            result["XAUTHORITY"] = xauth

        return result

    def _find_umu_run(self) -> Optional[str]:
        """Find the bundled umu-run path."""
        if self.plugin_dir:
            umu_path = os.path.join(self.plugin_dir, "bin", "umu", "umu", "umu-run")
            if os.path.exists(umu_path):
                return umu_path

        # Fallback: check common paths
        for path in [
            os.path.expanduser("~/.local/share/unifideck/bin/umu/umu/umu-run"),
            "/usr/bin/umu-run",
        ]:
            if os.path.exists(path):
                return path

        logger.warning("[Ubisoft] umu-run not found")
        return None

    def _find_proton_path(self) -> Optional[str]:
        """Find a suitable Proton installation for umu-run.

        Priority matches the launcher script (unifideck-launcher):
        1. Proton Experimental (steamapps/common/)
        2. Proton 10.0 (steamapps/common/)
        3. Proton 9.0 Beta (steamapps/common/)
        4. UMU-Proton (compatibilitytools.d/)
        5. GE-Proton newest (compatibilitytools.d/)
        """
        home = os.path.expanduser("~")

        # Priority 1-3: Official Proton from Steam (matches launcher default order)
        steam_common_dirs = [
            os.path.join(home, ".steam", "steam", "steamapps", "common"),
            os.path.join(home, ".local", "share", "Steam", "steamapps", "common"),
            os.path.join(home, ".steam", "root", "steamapps", "common"),
        ]
        proton_names = [
            "Proton - Experimental",
            "Proton 10.0",
            "Proton 9.0 (Beta)",
        ]
        for steam_common in steam_common_dirs:
            for name in proton_names:
                candidate = os.path.join(steam_common, name)
                if os.path.isdir(candidate):
                    logger.info(f"[Ubisoft] Using Proton: {name}")
                    return candidate

        # Priority 4-5: Custom Proton from compatibilitytools.d
        compat_dir = os.path.expanduser("~/.local/share/Steam/compatibilitytools.d")
        if not os.path.isdir(compat_dir):
            logger.warning("[Ubisoft] No Proton found")
            return None

        candidates = []
        for entry in os.listdir(compat_dir):
            full = os.path.join(compat_dir, entry)
            if os.path.isdir(full):
                if entry.startswith("UMU-Proton"):
                    candidates.insert(0, full)
                elif entry.startswith("GE-Proton"):
                    candidates.append(full)

        # Sort GE-Proton candidates by version (newest first)
        candidates.sort(key=lambda p: os.path.basename(p), reverse=True)
        # But keep UMU-Proton at front
        umu = [c for c in candidates if "UMU-Proton" in c]
        ge = [c for c in candidates if "GE-Proton" in c]
        candidates = umu + ge

        if candidates:
            logger.info(f"[Ubisoft] Using Proton: {os.path.basename(candidates[0])}")
            return candidates[0]

        logger.warning("[Ubisoft] No Proton found in steamapps or compatibilitytools.d")
        return None

    def _find_python(self) -> str:
        """Find python3 executable."""
        import shutil as _shutil

        for name in ["python3", "python"]:
            path = _shutil.which(name)
            if path:
                return path
        return "python3"

    def get_prefix_path(self, space_id: str) -> str:
        """Get the per-game prefix path for a given space_id."""
        return os.path.join(PREFIXES_DIR, space_id)

    # ========================================================================
    # UPC Warm-up (populate configurations cache)
    # ========================================================================

    async def _warmup_upc(self, prefix_path: str) -> bool:
        """
        Run UPC briefly to let it download its configuration cache.

        After bootstrap, UPC is installed but hasn't run yet. Running it
        briefly lets it connect and populate the configurations binary,
        which we need to resolve space_id -> install_id mappings.

        Returns True if configurations file was created.
        """
        logger.info("[Ubisoft] Running UPC warm-up to populate configuration cache...")

        upc_path = self._find_upc_exe(prefix_path)
        if not upc_path:
            logger.warning("[Ubisoft] Cannot warm up UPC: upc.exe not found")
            return False

        # Inject session so UPC can authenticate
        self.inject_upc_session(prefix_path)

        umu_run = self._find_umu_run()
        if not umu_run:
            logger.warning("[Ubisoft] Cannot warm up UPC: umu-run not found")
            return False

        python_bin = self._find_python()
        env = self._build_umu_env(prefix_path, "umu-ubisoft-warmup")

        proc = await asyncio.create_subprocess_exec(
            python_bin, umu_run, upc_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Wait for configurations file to appear (check every 5s, up to 120s)
        configs_found = False
        for i in range(24):  # 24 * 5s = 120s
            await asyncio.sleep(5)

            configs_path = self._find_configurations(prefix_path)
            if configs_path:
                logger.info(f"[Ubisoft] Configurations file found after {(i + 1) * 5}s")
                configs_found = True
                break

            # Check if process died early
            if proc.returncode is not None:
                logger.warning(
                    f"[Ubisoft] UPC warm-up exited early (rc={proc.returncode})"
                )
                break

        # Terminate UPC
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        if configs_found:
            logger.info("[Ubisoft] UPC warm-up complete, configurations cached")
        else:
            logger.warning(
                "[Ubisoft] UPC warm-up timed out without producing configurations"
            )

        # Capture any session token UPC wrote (only happens if user logged in)
        captured = self._capture_upc_session(prefix_path)
        if captured:
            self._propagate_upc_session_to_all_prefixes(captured)

        return configs_found

    # ========================================================================
    # Manual UPC Install Fallback
    # ========================================================================

    async def _install_via_upc_ui(
        self,
        game_id: str,
        game_name: Optional[str],
        prefix_path: str,
        upc_path: str,
        umu_run: str,
        python_bin: str,
        env: dict,
        progress_callback=None,
        install_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """
        Fallback install: launch UPC authenticated and let the user install
        the game manually through the Ubisoft Connect UI.

        Monitors the filesystem for new game directories to detect what was
        installed and where.
        """
        logger.info(
            f"[Ubisoft] install_id unavailable for {game_id} — "
            "launching UPC for manual install"
        )

        # Inject session so UPC is pre-authenticated
        self.inject_upc_session(prefix_path)

        # Snapshot existing directories before UPC launches
        install_base = install_path or DEFAULT_INSTALL_BASE
        os.makedirs(install_base, exist_ok=True)
        dirs_before = set()
        try:
            dirs_before = set(os.listdir(install_base))
        except Exception:
            pass

        # Also snapshot the default UPC install location inside the prefix
        upc_games_dir = os.path.join(
            prefix_path, "drive_c", "Program Files (x86)",
            "Ubisoft", "Ubisoft Game Launcher", "games"
        )
        pfx_games_dir = os.path.join(
            prefix_path, "pfx", "drive_c", "Program Files (x86)",
            "Ubisoft", "Ubisoft Game Launcher", "games"
        )
        upc_dirs_before: Dict[str, set] = {}
        for gdir in [upc_games_dir, pfx_games_dir]:
            if os.path.isdir(gdir):
                upc_dirs_before[gdir] = set(os.listdir(gdir))

        if progress_callback:
            await progress_callback({
                "status": "waiting",
                "message": "Ubisoft Connect is opening. Please install the game from the UPC interface.",
                "progress": 0,
            })

        # Launch UPC (no install URL — just open the client)
        logger.info("[Ubisoft] Launching UPC for manual install")
        proc = await asyncio.create_subprocess_exec(
            python_bin, umu_run, upc_path,
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )

        # Monitor for new directories (check every 10s, up to 2 hours)
        install_dir = None
        max_checks = 720  # 720 * 10s = 7200s = 2h
        for i in range(max_checks):
            await asyncio.sleep(10)

            # Check for new directories in install base
            try:
                dirs_now = set(os.listdir(install_base))
                new_dirs = dirs_now - dirs_before
                for d in new_dirs:
                    candidate = os.path.join(install_base, d)
                    if os.path.isdir(candidate) and self._looks_like_game_install(candidate):
                        install_dir = candidate
                        break
            except Exception:
                pass

            # Check UPC's default game directories
            if not install_dir:
                for gdir, before in upc_dirs_before.items():
                    try:
                        now = set(os.listdir(gdir))
                        new = now - before
                        for d in new:
                            candidate = os.path.join(gdir, d)
                            if os.path.isdir(candidate) and self._looks_like_game_install(candidate):
                                install_dir = candidate
                                break
                    except Exception:
                        pass
                    if install_dir:
                        break

            if install_dir:
                logger.info(f"[Ubisoft] Detected game install at: {install_dir}")
                if progress_callback:
                    await progress_callback({
                        "status": "installing",
                        "message": f"Game detected at {os.path.basename(install_dir)}",
                        "progress": 50,
                    })
                # Wait a bit more for install to complete
                await self._wait_for_install_completion(install_dir, progress_callback)
                break

            # Check if UPC exited
            if proc.returncode is not None:
                logger.info(f"[Ubisoft] UPC exited (rc={proc.returncode})")
                break

            if progress_callback and i % 6 == 0:  # Every 60s
                await progress_callback({
                    "status": "waiting",
                    "message": "Waiting for game installation in Ubisoft Connect...",
                    "progress": 0,
                })

        # Terminate UPC if still running
        if proc.returncode is None:
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=15)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        # Capture any session token UPC wrote after login
        captured = self._capture_upc_session(prefix_path)
        if captured:
            self._propagate_upc_session_to_all_prefixes(captured)

        if install_dir:
            exe = self.find_game_executable(install_dir)
            await self.write_install_marker(
                game_id, install_dir, exe or "", game_name or ""
            )
            final_size = self._get_directory_size(install_dir)
            logger.info(
                f"[Ubisoft] Manual install complete: {install_dir} "
                f"({final_size / 1024 / 1024:.0f} MB)"
            )

            # Try to refresh ID map now that UPC has run
            try:
                await self._refresh_id_map(game_id)
            except Exception:
                pass

            return {
                "success": True,
                "install_path": install_dir,
                "executable": exe,
                "install_size": final_size,
            }

        return {
            "success": False,
            "error": "No game installation detected. "
                     "Please try again and install the game through Ubisoft Connect.",
        }

    async def _run_upc_for_login(self, prefix_path: str) -> bool:
        """
        Launch UPC without an install URL so the user can log in once.

        After the user closes UPC, we capture the restore_session token it
        wrote to settings.yml. Returns True if a token was captured.
        """
        upc_path = self._find_upc_exe(prefix_path)
        umu_run = self._find_umu_run()
        if not upc_path or not umu_run:
            logger.warning("[Ubisoft] _run_upc_for_login: upc.exe or umu-run not found")
            return False

        python_bin = self._find_python()
        env = self._build_umu_env(prefix_path, "umu-ubisoft-login")

        logger.info("[Ubisoft] Launching UPC for first-time login (no install URL)")
        proc = await asyncio.create_subprocess_exec(
            python_bin, umu_run, upc_path,
            env=env,
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )

        # Wait up to 5 minutes for user to log in and close UPC
        try:
            await asyncio.wait_for(proc.wait(), timeout=300)
        except asyncio.TimeoutError:
            logger.info("[Ubisoft] Login timeout — terminating UPC")
            try:
                proc.terminate()
                await asyncio.wait_for(proc.wait(), timeout=10)
            except Exception:
                try:
                    proc.kill()
                except Exception:
                    pass

        token = self._capture_upc_session(prefix_path)
        if token:
            logger.info("[Ubisoft] First-time login successful — UPC session captured")
        else:
            logger.warning("[Ubisoft] No UPC session captured after login flow")
        return token is not None

    @staticmethod
    def _looks_like_game_install(path: str) -> bool:
        """Check if a directory looks like a game installation (has .exe files or is >100MB)."""
        try:
            # Check for executables
            for root, _dirs, files in os.walk(path):
                for f in files:
                    if f.lower().endswith(".exe"):
                        return True
                # Only check top 2 levels
                depth = root[len(path):].count(os.sep)
                if depth >= 2:
                    break

            # Check if directory is substantial (>100MB)
            total = 0
            for root, _dirs, files in os.walk(path):
                for f in files:
                    total += os.path.getsize(os.path.join(root, f))
                    if total > 100 * 1024 * 1024:
                        return True
        except Exception:
            pass
        return False

    @staticmethod
    def _wine_path_to_linux(
        wine_path: str, prefix_path: str
    ) -> Optional[str]:
        """Convert a Wine drive-letter path to a Linux filesystem path.

        Handles:
          Z:\\home\\deck\\Games\\...  →  /home/deck/Games/...
          C:\\Program Files\\...    →  {prefix}/drive_c/Program Files/...
          D:\\Mods\\Brawlhalla\\... →  (resolved via dosdevices/d: symlink)

        For drive letters other than Z: and C:, reads the prefix's
        dosdevices/ directory to resolve the symlink target.
        """
        # Normalize separators
        path = wine_path.replace("\\", "/")

        # Must look like a drive-letter path (e.g. "X:/...")
        if len(path) < 2 or path[1] != ":":
            return None

        drive_letter = path[0].upper()
        relative = path[2:].lstrip("/")

        # Z: maps to /
        if drive_letter == "Z":
            return "/" + relative if relative else "/"

        # C: maps to drive_c within the prefix
        if drive_letter == "C":
            for base in [os.path.join(prefix_path, "pfx"), prefix_path]:
                candidate = os.path.join(base, "drive_c", relative)
                if os.path.exists(candidate):
                    return candidate
            # Return pfx layout even if doesn't exist yet
            return os.path.join(prefix_path, "pfx", "drive_c", relative)

        # Any other drive letter: resolve via dosdevices/ symlinks
        # Wine maps drive letters like d: → /run/media/deck/SD_CARD
        drive_name = f"{drive_letter.lower()}:"
        for base in [os.path.join(prefix_path, "pfx"), prefix_path]:
            link_path = os.path.join(base, "dosdevices", drive_name)
            if os.path.islink(link_path):
                target = os.path.realpath(link_path)
                result = os.path.join(target, relative) if relative else target
                return result

        return None

    def _get_external_game_roots(self) -> List[str]:
        """Get all possible external Ubisoft game install directories.

        Dynamically discovers:
        - Default install base (~/Games/Ubisoft)
        - SD card install base (/run/media/mmcblk0p1/Games/Ubisoft)
        - User's custom install path from download_settings.json (+ /Ubisoft)
        - All mounted media under /run/media/ (USB drives, etc.)

        Returns a deduplicated list of existing or potential Ubisoft game roots.
        """
        roots = [DEFAULT_INSTALL_BASE, SDCARD_INSTALL_BASE]

        # Custom path from download settings
        settings_file = os.path.expanduser(
            "~/.local/share/unifideck/download_settings.json"
        )
        try:
            if os.path.isfile(settings_file):
                with open(settings_file, "r") as f:
                    settings = json.load(f)
                custom_path = settings.get("custom_path")
                if custom_path:
                    # Check both the custom path directly and with /Ubisoft suffix
                    roots.append(os.path.join(custom_path, "Ubisoft"))
                    roots.append(custom_path)
        except Exception:
            pass

        # Mounted media: /run/media/**/Games/Ubisoft
        media_base = "/run/media"
        if os.path.isdir(media_base):
            try:
                for entry in os.listdir(media_base):
                    entry_path = os.path.join(media_base, entry)
                    if not os.path.isdir(entry_path):
                        continue
                    # Direct mount: /run/media/mmcblk0p1/Games/Ubisoft
                    candidate = os.path.join(entry_path, "Games", "Ubisoft")
                    roots.append(candidate)
                    # Nested mount: /run/media/deck/SDCARD/Games/Ubisoft
                    try:
                        for sub in os.listdir(entry_path):
                            sub_path = os.path.join(entry_path, sub)
                            if os.path.isdir(sub_path):
                                candidate = os.path.join(
                                    sub_path, "Games", "Ubisoft"
                                )
                                roots.append(candidate)
                    except OSError:
                        pass
            except OSError:
                pass

        # Deduplicate via realpath, keep only existing dirs
        seen: set = set()
        unique: List[str] = []
        for r in roots:
            try:
                real = os.path.realpath(r)
            except Exception:
                real = r
            if real not in seen:
                seen.add(real)
                unique.append(r)

        return unique

    async def _wait_for_install_completion(
        self, install_dir: str, progress_callback=None
    ) -> None:
        """Wait for a game install directory to stop growing (install complete)."""
        prev_size = 0
        stable_count = 0
        for _ in range(360):  # Up to 1 hour
            await asyncio.sleep(10)
            curr_size = self._get_directory_size(install_dir)

            if curr_size == prev_size and curr_size > 0:
                stable_count += 1
                if stable_count >= 3:  # Stable for 30s
                    break
            else:
                stable_count = 0

            prev_size = curr_size

            if progress_callback and curr_size > 0:
                await progress_callback({
                    "status": "installing",
                    "message": f"Installing... ({curr_size / 1024 / 1024 / 1024:.1f} GB)",
                    "progress": min(90, 50 + stable_count * 10),
                })

    # ========================================================================
    # Install/Uninstall Helpers
    # ========================================================================

    def _get_game_name(self, space_id: str) -> Optional[str]:
        """Get human-readable game name from ID map cache."""
        entry = self._id_map_cache.get(space_id, {})
        return entry.get("name")

    async def _refresh_id_map(self, space_id: str) -> None:
        """
        Try to populate the ID map from the configurations binary.

        Scans template and per-game prefixes (checking both direct and pfx/
        layouts) for the configurations file, then parses it.
        """
        try:
            from .ubisoft_parser import build_id_map_from_configurations

            # Check template prefix first
            configs_path = self._find_configurations(TEMPLATE_DIR)
            if configs_path:
                new_map = build_id_map_from_configurations(configs_path)
                if new_map:
                    self._id_map_cache.update(new_map)
                    self._save_id_map()
                    logger.info(f"[Ubisoft] ID map refreshed from template ({len(new_map)} entries)")
                    return

            # Check existing per-game prefixes
            if os.path.isdir(PREFIXES_DIR):
                for entry in os.listdir(PREFIXES_DIR):
                    if entry.startswith("."):
                        continue
                    prefix_path = os.path.join(PREFIXES_DIR, entry)
                    configs_path = self._find_configurations(prefix_path)
                    if configs_path:
                        new_map = build_id_map_from_configurations(configs_path)
                        if new_map:
                            self._id_map_cache.update(new_map)
                            self._save_id_map()
                            logger.info(f"[Ubisoft] ID map refreshed from prefix {entry} ({len(new_map)} entries)")
                            return

            logger.info("[Ubisoft] No configurations binary found in any prefix")

        except Exception as e:
            logger.warning(f"[Ubisoft] ID map refresh failed: {e}")

    def _inject_install_registry(
        self, prefix_path: str, install_id: str, install_dir: str
    ) -> None:
        """Inject registry keys for game installation path."""
        try:
            active_prefix = prefix_path
            pfx = os.path.join(prefix_path, "pfx")
            if os.path.isdir(pfx) and os.path.isfile(os.path.join(pfx, "system.reg")):
                active_prefix = pfx

            system_reg = os.path.join(active_prefix, "system.reg")
            if not os.path.isfile(system_reg):
                return

            # Convert Linux path to Wine Z: drive
            wine_path = install_dir
            if install_dir.startswith("/"):
                wine_path = "Z:" + install_dir.replace("/", "\\\\")

            import re as _re
            section = f"[Software\\\\WOW6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\{install_id}]"
            values = [f'"InstallDir"="{wine_path}"']

            with open(system_reg, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            if section in content:
                sec_start = content.index(section)
                next_sec = _re.search(r"\n\[", content[sec_start + len(section):])
                sec_end = (
                    sec_start + len(section) + next_sec.start()
                    if next_sec
                    else len(content)
                )
                sec_body = content[sec_start + len(section): sec_end]
                for val in values:
                    key = val.split("=")[0]
                    pattern = rf'^{_re.escape(key)}="[^"]*"'
                    new_body, count = _re.subn(pattern, val, sec_body, flags=_re.MULTILINE)
                    sec_body = new_body if count else sec_body.rstrip("\n") + "\n" + val + "\n"
                content = content[: sec_start + len(section)] + sec_body + content[sec_end:]
            else:
                content += f"\n{section}\n" + "\n".join(values) + "\n"

            with open(system_reg, "w", encoding="utf-8") as f:
                f.write(content)

            logger.info(f"[Ubisoft] Install registry injected for {install_id}")

        except Exception as e:
            logger.warning(f"[Ubisoft] Registry injection failed: {e}")

    def _clean_install_registry(self, prefix_path: str, install_id: str) -> None:
        """Remove Ubisoft install registry keys from prefix on uninstall."""
        if not install_id:
            return
        try:
            import re as _re
            active_prefix = prefix_path
            pfx = os.path.join(prefix_path, "pfx")
            if os.path.isdir(pfx):
                active_prefix = pfx

            system_reg = os.path.join(active_prefix, "system.reg")
            if not os.path.isfile(system_reg):
                return

            with open(system_reg, "r", encoding="utf-8", errors="replace") as f:
                content = f.read()

            section = f"[Software\\\\WOW6432Node\\\\Ubisoft\\\\Launcher\\\\Installs\\\\{install_id}]"
            if section in content:
                sec_start = content.index(section)
                next_sec = _re.search(r"\n\[", content[sec_start + len(section):])
                sec_end = (
                    sec_start + len(section) + next_sec.start()
                    if next_sec
                    else len(content)
                )
                content = content[:sec_start] + content[sec_end:]
                with open(system_reg, "w", encoding="utf-8") as f:
                    f.write(content)
                logger.info(f"[Ubisoft] Cleaned registry for {install_id}")
        except Exception as e:
            logger.warning(f"[Ubisoft] Registry cleanup failed: {e}")

    async def _monitor_install_progress(
        self,
        game_id: str,
        install_dir: str,
        proc: asyncio.subprocess.Process,
        progress_callback=None,
        timeout: int = 14400,  # 4 hours
    ) -> bool:
        """
        Monitor file system for installation progress.

        Since upc.exe is a GUI app with no stdout progress, we poll
        the game directory size every 3 seconds and calculate speed
        from size deltas.

        Completion is detected via uplay_install.state first byte == 0x0A,
        or directory size stabilization.
        """
        from .ubisoft_parser import check_install_state

        start_time = asyncio.get_event_loop().time()
        last_size = 0
        last_poll_time = start_time
        stable_count = 0  # Number of consecutive polls with no growth

        while True:
            elapsed = asyncio.get_event_loop().time() - start_time
            if elapsed > timeout:
                logger.error(f"[Ubisoft] Install timeout after {timeout}s")
                return False

            # Check process exit
            if proc.returncode is not None:
                # Process exited — check if installation completed
                state_file = os.path.join(install_dir, "uplay_install.state")
                if check_install_state(state_file):
                    return True
                # Process may have exited normally after install
                if proc.returncode == 0:
                    await asyncio.sleep(5)
                    if check_install_state(state_file):
                        return True
                logger.warning(
                    f"[Ubisoft] upc.exe exited (rc={proc.returncode}) "
                    "before install state detected"
                )
                return False

            # Check install state file
            state_file = os.path.join(install_dir, "uplay_install.state")
            if check_install_state(state_file):
                logger.info(f"[Ubisoft] Install state detected — game installed")
                if progress_callback:
                    await progress_callback({"progress_percent": 100})
                return True

            # Monitor directory size
            current_size = self._get_directory_size(install_dir)
            now = asyncio.get_event_loop().time()
            poll_delta = now - last_poll_time

            if current_size > 0 and progress_callback:
                # Calculate speed from size delta
                speed_mbps = 0.0
                if poll_delta > 0 and current_size > last_size:
                    delta_bytes = current_size - last_size
                    speed_mbps = (delta_bytes / poll_delta) / (1024 * 1024)

                is_growing = current_size != last_size
                await progress_callback({
                    "progress_percent": min(95, int(elapsed / 60)),  # ~1% per minute
                    "downloaded_bytes": current_size,
                    "speed_mbps": round(speed_mbps, 2) if is_growing else 0.0,
                    "phase": "downloading" if is_growing else "installing",
                    "phase_message": (
                        f"Downloading... ({current_size / (1024 * 1024):.0f} MB"
                        f" @ {speed_mbps:.1f} MB/s)"
                        if is_growing and speed_mbps > 0
                        else f"Downloading... ({current_size / (1024 * 1024):.0f} MB)"
                        if is_growing
                        else "Installing..."
                    ),
                })

            # Detect size stabilization
            if current_size > 0 and current_size == last_size:
                stable_count += 1
            else:
                stable_count = 0
            last_size = current_size
            last_poll_time = now

            # If stable for 30+ seconds and we have data, might be in verification phase
            if stable_count >= 10 and current_size > 100 * 1024 * 1024:
                if progress_callback:
                    await progress_callback({
                        "phase": "verifying",
                        "phase_message": "Verifying installation...",
                    })

            await asyncio.sleep(3)

    @staticmethod
    def _get_directory_size(path: str) -> int:
        """Get total size of a directory in bytes."""
        total = 0
        try:
            for dirpath, _, filenames in os.walk(path):
                for f in filenames:
                    try:
                        total += os.path.getsize(os.path.join(dirpath, f))
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def kill_upc_processes(self) -> None:
        """Kill any running upc.exe processes (for cancellation)."""
        try:
            import subprocess
            subprocess.run(["pkill", "-f", "upc.exe"], capture_output=True)
            logger.info("[Ubisoft] Killed upc.exe processes")
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to kill upc.exe: {e}")

    # ========================================================================
    # Prefix Repair
    # ========================================================================

    async def repair_prefix(self, space_id: str) -> Dict[str, Any]:
        """
        Repair a game's Wine prefix by re-cloning from template.

        This is used when a game's prefix is corrupted (upc.exe missing,
        registry damaged, etc.). It preserves the game's install directory
        but rebuilds the UPC client within the prefix.

        Args:
            space_id: The game's space_id.

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            prefix_path = self.get_prefix_path(space_id)
            logger.info(f"[Ubisoft] Repairing prefix for {space_id}")

            # Remove old prefix (keep game data which is in ~/Games/)
            if os.path.isdir(prefix_path):
                shutil.rmtree(prefix_path)
                logger.info(f"[Ubisoft] Removed corrupted prefix for {space_id}")

            # Re-bootstrap from template or fresh install
            success = await self.bootstrap_game_prefix(space_id)
            if not success:
                return {"success": False, "error": "Failed to rebuild prefix"}

            # Re-inject session
            self.inject_upc_session(prefix_path)

            # Re-inject registry for installed game
            install_id = self.resolve_install_id(space_id)
            if install_id:
                game_info = self._detect_installed_game(space_id, prefix_path)
                if game_info and game_info.get("install_path"):
                    self._inject_install_registry(
                        prefix_path, install_id, game_info["install_path"]
                    )

            logger.info(f"[Ubisoft] Prefix repaired for {space_id}")
            return {"success": True}

        except Exception as e:
            logger.exception(f"[Ubisoft] Prefix repair failed for {space_id}: {e}")
            return {"success": False, "error": str(e)}

    async def get_all_install_bases(self) -> List[str]:
        """
        Get all known Ubisoft install base directories.

        Returns both internal and SD card paths that exist.
        """
        bases = []
        if os.path.isdir(DEFAULT_INSTALL_BASE):
            bases.append(DEFAULT_INSTALL_BASE)
        if os.path.isdir(SDCARD_INSTALL_BASE):
            bases.append(SDCARD_INSTALL_BASE)
        return bases

    # ========================================================================
    # Background API Token Refresh
    # Background token refresh removed — API calls are now event-driven only.
    # Tokens refresh on-demand during explicit sync/login operations.
    # After login and sync, the UPC app handles session management.
