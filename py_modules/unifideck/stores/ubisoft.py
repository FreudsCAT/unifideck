"""
Ubisoft Connect Store Connector

Main store adapter implementing the Store ABC for Ubisoft Connect.
Uses direct REST/GraphQL API for auth and library (via UbisoftAPIClient),
and delegates downloads/installs/launches to upc.exe via uplay:// protocol.
"""
import asyncio
import glob
import json
import logging
import os
import re
import shutil
import subprocess
from typing import Any, Dict, List, Optional

from .base import Store, Game
from .ubisoft_api import UbisoftAPIClient

logger = logging.getLogger(__name__)

# ============================================================================
# Paths
# ============================================================================

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
ID_MAP_FILE = os.path.join(DATA_DIR, "ubisoft_id_map.json")
SHORTCUTS_REGISTRY_PATH = os.path.join(DATA_DIR, "shortcuts_registry.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
TEMPLATE_DIR = os.path.join(PREFIXES_DIR, ".template")
AUTH_PREFIX_DIR = os.path.join(PREFIXES_DIR, ".upc-auth")
AUTH_SHORTCUT_STORE_ID = "ubisoft:upc-auth"
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

# SD card install path (mirrors download manager's StorageLocation.SDCARD)
SDCARD_INSTALL_BASE = "/run/media/mmcblk0p1/Games/Ubisoft"

# UPC paths within a Wine prefix
UPC_RELATIVE_PATH = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"
# UbisoftConnect.exe is the registered uplay:// protocol handler — use this for install URLs
UPC_CONNECT_RELATIVE_PATH = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/UbisoftConnect.exe"
CONFIGURATIONS_RELATIVE_PATH = (
    "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/"
    "cache/configuration/configurations"
)
# Ubisoft Connect localStorage (contains ubisoftConnectGameId and other metadata)
LOCALSTORAGE_RELATIVE_PATH = (
    "drive_c/users/steamuser/AppData/Local/Ubisoft Game Launcher/"
    "cache/http2/Default/Local Storage"
)


class UbisoftConnector(Store):
    """
    Ubisoft Connect store connector.

    Uses direct REST/GraphQL API for auth and library,
    delegates downloads/installs/launches to upc.exe via uplay:// protocol.
    """

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        self.plugin_dir = plugin_dir
        self.plugin_instance = plugin_instance
        self.api = UbisoftAPIClient()
        self._id_map_cache: Dict[str, Dict[str, Any]] = {}
        self._load_id_map()
        self._template_task: Optional[asyncio.Task] = None
        self._pending_2fa_ticket: Optional[str] = None  # Stored between login → 2FA
        self._active_install_pids: Dict[str, int] = {}  # game_id → PID for cancel support

    # ========================================================================
    # Store ABC Implementation
    # ========================================================================

    @property
    def store_name(self) -> str:
        return "ubisoft"

    async def is_available(self) -> bool:
        """Check if authenticated and tokens are valid."""
        logger.info("[Ubisoft] Checking availability")
        if not self.api.has_tokens():
            logger.info("[Ubisoft] No tokens found -- not authenticated")
            return False

        try:
            valid = await self.api.validate_ticket()
            logger.info(f"[Ubisoft] Ticket valid: {valid}")
            return valid
        except Exception as e:
            logger.warning(f"[Ubisoft] Availability check error: {e}")
            return False

    async def start_auth(self) -> Dict[str, Any]:
        """
        Return auth prompt config for native Decky form (email + password).

        Unlike Epic/GOG/Amazon, Ubisoft uses direct REST API login,
        so no URL is returned -- the frontend renders a form instead.
        """
        return {
            "success": True,
            "auth_type": "credentials",
            "message": "Enter your Ubisoft Connect email and password",
        }

    async def complete_auth(self, auth_data: str) -> Dict[str, Any]:
        """
        Complete auth with email/password credentials.

        Args:
            auth_data: JSON string with {email, password} or {code, two_fa_ticket}
        """
        try:
            data = json.loads(auth_data) if isinstance(auth_data, str) else auth_data
        except (json.JSONDecodeError, TypeError):
            return {"success": False, "error": "Invalid auth data format"}

        email = data.get("email", "")
        password = data.get("password", "")

        if not email or not password:
            return {"success": False, "error": "Email and password are required"}

        result = await self.api.login(email, password)

        if result.get("requires_2fa"):
            # Store 2FA ticket for subsequent complete_auth_2fa call
            self._pending_2fa_ticket = result.get("2fa_ticket", "")
            logger.info("[Ubisoft] Login requires 2FA, ticket stored")
        elif result.get("success"):
            # Auth succeeded -- trigger auto-sync. Launcher-based UPC auth is
            # now initiated by the frontend so it stays visible in Gaming Mode.
            if self.plugin_instance:
                logger.info("[Ubisoft] Triggering library sync after auth")
                asyncio.create_task(self.plugin_instance.force_sync_libraries())
            result["launch_upc_auth"] = self._template_exists()

        return result

    async def complete_auth_2fa(self, code: str, two_fa_ticket: str = None) -> Dict[str, Any]:
        """
        Complete 2FA verification.

        Args:
            code: 6-digit verification code
            two_fa_ticket: The 2FA ticket from initial login (optional,
                          uses stored ticket from previous login call if omitted)
        """
        ticket = two_fa_ticket or self._pending_2fa_ticket
        if not ticket:
            return {"success": False, "error": "No 2FA ticket available — please sign in again"}

        result = await self.api.complete_2fa(code, ticket)
        self._pending_2fa_ticket = None  # Clear after use

        if result.get("success"):
            # Auth succeeded -- trigger auto-sync. Launcher-based UPC auth is
            # now initiated by the frontend so it stays visible in Gaming Mode.
            if self.plugin_instance:
                logger.info("[Ubisoft] Triggering library sync after 2FA auth")
                asyncio.create_task(self.plugin_instance.force_sync_libraries())
            result["launch_upc_auth"] = self._template_exists()

        return result

    async def logout(self) -> Dict[str, Any]:
        """Logout from Ubisoft Connect, clearing all state."""
        return self.api.logout()

    async def _auto_capture_upc_token(self) -> None:
        """
        Auto-open UPC in the template prefix after REST auth to capture the
        native session token. Runs as a background task so auth returns immediately.
        """
        try:
            # Wait for template prefix to exist (may still be creating)
            for _ in range(60):  # Up to 5 minutes
                if self._template_exists():
                    break
                await asyncio.sleep(5)
            else:
                logger.warning("[Ubisoft] Template prefix not ready, skipping auto UPC token capture")
                return

            logger.info("[Ubisoft] Auto-opening UPC in template prefix for token capture")
            result = await self.connect_ubisoft_account()
            if result.get("success"):
                logger.info("[Ubisoft] Auto UPC token capture succeeded")
            else:
                logger.warning(f"[Ubisoft] Auto UPC token capture: {result.get('error', 'unknown')}")
        except Exception as e:
            logger.warning(f"[Ubisoft] Auto UPC token capture failed: {e}")

    async def get_library(self) -> Optional[List[Game]]:
        """
        Get the user's Ubisoft game library via GraphQL.

        Returns:
            List of Game objects, None on auth failure.
        """
        try:
            nodes = await self.api.get_owned_games()

            if nodes is None:
                # Auth failure -- return None so sync skips this store
                logger.warning("[Ubisoft] Library query returned None (auth failure)")
                return None

            # Get installed games for status
            installed = await self.get_installed()

            games = []
            seen_space_ids = set()

            for node in nodes:
                space_id = node.get("spaceId", "")
                if not space_id or space_id in seen_space_ids:
                    continue
                seen_space_ids.add(space_id)

                name = node.get("name", "Unknown")
                cover_url = node.get("coverUrl", "")
                background_url = node.get("backgroundUrl", "")
                banner_url = node.get("bannerUrl", "")

                is_installed = space_id in installed

                game = Game(
                    id=space_id,
                    title=name,
                    store="ubisoft",
                    is_installed=is_installed,
                    cover_image=cover_url,
                    install_path=installed.get(space_id, {}).get("install_path"),
                    executable=installed.get(space_id, {}).get("executable"),
                )

                # Store extra GraphQL data for artwork fetcher
                if not hasattr(game, "extra"):
                    game.extra = {}
                game.extra = {
                    "coverUrl": cover_url,
                    "backgroundUrl": background_url,
                    "bannerUrl": banner_url,
                }

                games.append(game)

            logger.info(f"[Ubisoft] Library: {len(games)} PC games")

            # Resolve install_ids from static database for all games
            if games:
                game_list = [{"space_id": g.id, "name": g.title} for g in games]
                try:
                    await self._resolve_install_ids_from_database(game_list)
                except Exception as e:
                    logger.warning(f"[Ubisoft] Static ID resolution failed: {e}")

            # Trigger template prefix creation as background task if needed
            if games and not self._template_exists():
                self._queue_template_creation()

            return games

        except Exception as e:
            logger.exception(f"[Ubisoft] Error fetching library: {e}")
            return []

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
        """Cancel a running install session by terminating UPC."""
        pid = self._active_install_pids.pop(game_id, None)
        if pid is None:
            return {"success": False, "error": "No active install session"}
        try:
            os.kill(pid, 15)  # SIGTERM
            logger.info(f"[Ubisoft] Sent SIGTERM to UPC PID {pid} for {game_id}")
            return {"success": True}
        except ProcessLookupError:
            return {"success": True, "message": "Process already exited"}
        except Exception as e:
            logger.error(f"[Ubisoft] Failed to cancel install {game_id}: {e}")
            return {"success": False, "error": str(e)}

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
                if "Ubisoft Game Launcher/games/" in install_dir or \
                   "Ubisoft Game Launcher\\games\\" in install_dir:
                    logger.info(
                        f"[Ubisoft] Extracted game ID {game_id} from registry "
                        f"(InstallDir={install_dir[:60]})"
                    )
                    return game_id

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

    async def _resolve_install_ids_from_database(
        self, games: List[Dict[str, str]]
    ) -> int:
        """
        Match owned games against the static game ID database by name.

        Args:
            games: List of dicts with 'space_id' and 'name' keys.

        Returns:
            Number of new mappings added.
        """
        db_entries = await self._fetch_game_id_database()
        if not db_entries:
            return 0

        # Build normalized lookup: {normalized_name: (install_id, original_name)}
        db_lookup: Dict[str, tuple] = {}
        for install_id, db_name in db_entries:
            norm = self._normalize_for_matching(db_name)
            db_lookup[norm] = (install_id, db_name)

        added = 0
        for game in games:
            space_id = game["space_id"]
            # Skip if already mapped
            if space_id in self._id_map_cache and self._id_map_cache[space_id].get("install_id"):
                continue

            game_name = game["name"]
            norm_name = self._normalize_for_matching(game_name)

            # Try exact normalized match first
            match = db_lookup.get(norm_name)

            # Try without common suffixes/prefixes
            if not match:
                # Try stripping "edition" variants (e.g., "Gold Edition", "Deluxe Edition")
                import re as _re
                stripped = _re.sub(r"\s*(standard|gold|deluxe|ultimate|complete|definitive|goty)\s*edition\s*$", "", norm_name).strip()
                if stripped != norm_name:
                    match = db_lookup.get(stripped)

            # Try word-set matching for close matches
            if not match:
                game_words = set(norm_name.split())
                best_score = 0.0
                best_match = None
                for db_norm, (db_id, db_orig) in db_lookup.items():
                    db_words = set(db_norm.split())
                    if not game_words or not db_words:
                        continue
                    # Jaccard similarity
                    intersection = len(game_words & db_words)
                    union = len(game_words | db_words)
                    score = intersection / union if union else 0
                    # Require high similarity to avoid false matches
                    if score > best_score and score >= 0.8:
                        best_score = score
                        best_match = (db_id, db_orig)
                match = best_match

            if match:
                install_id, db_name = match
                self._id_map_cache[space_id] = {
                    "install_id": install_id,
                    "launch_id": install_id,  # Usually same for Ubisoft
                    "name": game_name,
                }
                added += 1
                logger.info(f"[Ubisoft] Matched '{game_name}' → install_id={install_id} (db: '{db_name}')")
            else:
                logger.debug(f"[Ubisoft] No database match for '{game_name}'")

        if added:
            self._save_id_map()
            logger.info(f"[Ubisoft] Resolved {added} install_ids from static database")
        
        # Try to extract ubisoftConnectGameId from Ubisoft Connect's local cache
        # This is more reliable for native games than static database matching
        cache_ids = self._extract_cache_game_ids(TEMPLATE_DIR)
        for space_id, ubisoft_id in cache_ids.items():
            if space_id in self._id_map_cache:
                self._id_map_cache[space_id]["ubisoftconnect_game_id"] = ubisoft_id
                logger.info(f"[Ubisoft] Added cache ubisoftConnectGameId {ubisoft_id} for spaceId {space_id}")
        
        if cache_ids:
            self._save_id_map()

        return added

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

    # ========================================================================
    # Template Prefix Management
    # ========================================================================

    def _template_exists(self) -> bool:
        """Check if the template prefix with bootstrap marker exists."""
        marker = os.path.join(TEMPLATE_DIR, BOOTSTRAP_MARKER)
        return os.path.exists(marker)

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

            logger.info("[Ubisoft] Template prefix created successfully")

            # Clean up legacy .template shortcut if present (migration)
            await self._cleanup_legacy_auth_shortcut()

        except Exception as e:
            logger.exception(f"[Ubisoft] Template prefix creation failed: {e}")

    async def _ensure_auth_prefix(self) -> Optional[str]:
        """Ensure the dedicated UPC auth prefix exists and return the upc.exe path.

        Clones from template if needed. Returns None if template doesn't exist yet.
        """
        upc_path = self._find_upc_exe(AUTH_PREFIX_DIR)
        if upc_path:
            return upc_path

        # Corrupted: exists but no upc.exe — wipe and re-clone
        if os.path.isdir(AUTH_PREFIX_DIR):
            logger.warning("[Ubisoft] Auth prefix exists but upc.exe missing; re-cloning")
            shutil.rmtree(AUTH_PREFIX_DIR, ignore_errors=True)

        if not self._template_exists():
            logger.debug("[Ubisoft] Template not ready; cannot create auth prefix")
            return None

        logger.info("[Ubisoft] Cloning template → .upc-auth prefix")
        os.makedirs(AUTH_PREFIX_DIR, exist_ok=True)
        proc = await asyncio.create_subprocess_exec(
            "rsync", "-a", f"{TEMPLATE_DIR}/", f"{AUTH_PREFIX_DIR}/",
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        await proc.communicate()
        if proc.returncode != 0:
            logger.error("[Ubisoft] rsync failed for auth prefix clone")
            return None

        upc_path = self._find_upc_exe(AUTH_PREFIX_DIR)
        if upc_path:
            logger.info("[Ubisoft] Auth prefix created successfully")
        return upc_path

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

            session = await self.api._create_session()
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
        Pre-inject auth session into UPC config so no login prompt appears.

        Prefers a UPC-native restore_session token (captured after a real UPC login)
        over the REST API ticket. Skips writing if the prefix already has the correct
        token to avoid destroying a valid UPC-native token that UPC may have refreshed.
        """
        # Prefer UPC-native token from session file
        upc_session: Optional[str] = None
        if os.path.isfile(UPC_SESSION_FILE):
            try:
                with open(UPC_SESSION_FILE) as f:
                    upc_session = f.read().strip() or None
            except Exception:
                pass

        if upc_session:
            # Skip if prefix already has the correct token
            existing = self._read_prefix_restore_session(prefix_path)
            if existing == upc_session:
                logger.info("[Ubisoft] inject_upc_session: prefix already has correct UPC token, skipping")
                return True
            logger.info("[Ubisoft] inject_upc_session: writing UPC session token")
            return self._write_upc_session_to_prefix(prefix_path, upc_session)

        # Fall back to API ticket
        if not self.api.has_tokens():
            logger.warning("[Ubisoft] No tokens available for session injection")
            return False
        ticket = self.api.get_ticket() or ""
        if not ticket:
            logger.warning("[Ubisoft] No ticket available for session injection")
            return False
        logger.info("[Ubisoft] inject_upc_session: using API ticket (no UPC session captured yet)")
        return self._write_upc_session_to_prefix(prefix_path, ticket)

    def _write_upc_session_to_prefix(self, prefix_path: str, token: str) -> bool:
        """Write a restore_session token into a prefix's UPC settings.yml."""
        try:
            user_id = self.api.get_user_id() or ""
            settings_dir = os.path.join(
                prefix_path, "drive_c", "users", "deck",
                "AppData", "Roaming", "Ubisoft", "Ubisoft Connect",
            )
            os.makedirs(settings_dir, exist_ok=True)
            settings_file = os.path.join(settings_dir, "settings.yml")
            config = (
                "user:\n"
                "  remember_me: true\n"
                f'  restore_session: "{token}"\n'
                f'  userId: "{user_id}"\n'
            )
            with open(settings_file, "w") as f:
                f.write(config)
            logger.info(f"[Ubisoft] Wrote session to prefix {os.path.basename(prefix_path)}")
            return True
        except Exception as e:
            logger.warning(f"[Ubisoft] Failed to write session to prefix {prefix_path}: {e}")
            return False

    def _read_prefix_restore_session(self, prefix_path: str) -> Optional[str]:
        """Read the current restore_session token from a prefix's settings.yml."""
        for user_dir in ["deck", "steamuser"]:
            settings_file = os.path.join(
                prefix_path, "drive_c", "users", user_dir,
                "AppData", "Roaming", "Ubisoft", "Ubisoft Connect", "settings.yml"
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

    def _propagate_upc_session_to_all_prefixes(self, token: str) -> None:
        """Update restore_session in all existing per-game prefixes."""
        if not os.path.isdir(PREFIXES_DIR):
            return
        count = 0
        for entry in os.listdir(PREFIXES_DIR):
            if entry.startswith("."):
                continue  # skip .template and hidden dirs
            prefix_path = os.path.join(PREFIXES_DIR, entry)
            if not os.path.isdir(prefix_path):
                continue
            try:
                self._write_upc_session_to_prefix(prefix_path, token)
                count += 1
            except Exception as e:
                logger.warning(f"[Ubisoft] Failed to update prefix {entry}: {e}")
        logger.info(f"[Ubisoft] Propagated session token to {count} existing prefixes")

    def _capture_upc_session(self, prefix_path: str) -> Optional[str]:
        """
        Read back the restore_session token that UPC wrote to settings.yml
        after a successful login and save it for future use.

        UPC writes its own token (valid for rm_v1 auth) which differs from
        the REST API ticket we have. Capturing it enables future auto-login.
        Also writes the token back to the template prefix so future clones inherit it.

        Returns the captured token, or None if not found / unchanged.
        """
        for user_dir in ["steamuser", "deck"]:
            settings_file = os.path.join(
                prefix_path, "drive_c", "users", user_dir,
                "AppData", "Roaming", "Ubisoft", "Ubisoft Connect", "settings.yml"
            )
            if not os.path.isfile(settings_file):
                continue
            try:
                with open(settings_file) as f:
                    content = f.read()
            except Exception:
                continue
            m = re.search(r'restore_session:\s+"([^"]+)"', content)
            if not m:
                continue
            token = m.group(1)
            # Only save if different from what we injected (i.e. UPC wrote its own)
            current_api_ticket = self.api.get_ticket() or ""
            if token and token != current_api_ticket:
                try:
                    previous_token = ""
                    if os.path.isfile(UPC_SESSION_FILE):
                        with open(UPC_SESSION_FILE, "r") as f:
                            previous_token = f.read().strip()
                    if token == previous_token:
                        return None

                    os.makedirs(DATA_DIR, exist_ok=True)
                    with open(UPC_SESSION_FILE, "w") as f:
                        f.write(token)
                    # Also update the template so future clones inherit it
                    self._write_upc_session_to_prefix(TEMPLATE_DIR, token)
                    logger.info("[Ubisoft] Captured UPC restore_session token → template updated")
                    return token
                except Exception as e:
                    logger.warning(f"[Ubisoft] Failed to save UPC session token: {e}")
        return None

    async def connect_ubisoft_account(self) -> Dict[str, Any]:
        """
        Launch UPC in the template prefix so the user can log in once.

        Captures the restore_session token UPC writes after login and propagates
        it to all existing game prefixes. Future cloned prefixes inherit it via rsync.
        Exposed as a backend RPC for the plugin settings "Connect" button.
        """
        if not self._template_exists():
            return {"success": False, "error": "Template prefix not found. Install a game first."}

        prefix_path = TEMPLATE_DIR
        upc_path = self._find_upc_exe(prefix_path)
        umu_run = self._find_umu_run()
        if not upc_path or not umu_run:
            return {"success": False, "error": "UPC not found in template prefix"}

        python_bin = self._find_python()
        env = self._build_umu_env(prefix_path, "umu-ubisoft-auth")

        logger.info("[Ubisoft] Launching UPC in template prefix for login")
        proc = await asyncio.create_subprocess_exec(
            python_bin, umu_run, upc_path,
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

            # Close template UPC as soon as a new restore_session token is captured.
            captured_token = self._capture_upc_session(prefix_path)
            if captured_token:
                logger.info("[Ubisoft] UPC session captured during auth; closing template UPC")
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
            logger.warning("[Ubisoft] Template UPC auth timed out")
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
            return {"success": True, "message": "Ubisoft account connected successfully"}

        return {
            "success": False,
            "error": "Login not detected. Please log in and close Ubisoft Connect.",
        }

    # ========================================================================
    # Auth Shortcut Context & Session Monitor
    # ========================================================================

    async def get_ubisoft_auth_shortcut_context(self) -> Dict[str, Any]:
        """Get auth prefix info so the frontend can create a live shortcut.

        Ensures the auth prefix exists (cloned from template) and returns
        the UPC exe path and compat data path. The frontend uses
        SteamClient.Apps.AddShortcut() to register it in Steam's live cache.
        """
        upc_exe_path = await self._ensure_auth_prefix()
        if not upc_exe_path:
            return {"success": False, "error": "Template prefix not ready"}

        upc_dir = os.path.dirname(upc_exe_path)

        # Find a Proton tool name for the frontend to assign
        compat_tool = ""
        try:
            from py_modules.unifideck.compat.proton_tools import get_compat_tool_for_app
            from py_modules.unifideck.shortcuts.shortcuts_manager import load_shortcuts_registry
            from py_modules.unifideck.shortcuts.launch_options import get_store_prefix
            registry = load_shortcuts_registry()
            for store_id, entry in registry.items():
                if get_store_prefix(store_id) == "ubisoft" and store_id != AUTH_SHORTCUT_STORE_ID:
                    game_appid = entry.get("appid_unsigned")
                    if game_appid:
                        compat_tool = get_compat_tool_for_app(game_appid)
                        if compat_tool:
                            break
        except Exception:
            pass

        if not compat_tool:
            proton_path = self._find_proton_path()
            if proton_path:
                compat_tool = os.path.basename(proton_path)
            else:
                compat_tool = "proton_experimental"

        return {
            "success": True,
            "upc_exe_path": upc_exe_path,
            "upc_dir": upc_dir,
            "auth_prefix_path": AUTH_PREFIX_DIR,
            "compat_tool": compat_tool,
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
        timeout_seconds = 600
        poll_interval = 2
        elapsed = 0.0

        while elapsed < timeout_seconds:
            await asyncio.sleep(poll_interval)
            elapsed += poll_interval

            captured_token = self._capture_upc_session(AUTH_PREFIX_DIR)
            if captured_token:
                logger.info("[Ubisoft] Auth session monitor: token captured!")
                self._propagate_upc_session_to_all_prefixes(captured_token)
                self._auth_session_captured = True
                return

        logger.warning("[Ubisoft] Auth session monitor timed out after 600s")

    def check_ubisoft_auth_session_status(self) -> Dict[str, Any]:
        """Check whether the auth session monitor has captured a token."""
        captured = getattr(self, '_auth_session_captured', False)
        monitoring = (
            hasattr(self, '_auth_monitor_task')
            and self._auth_monitor_task
            and not self._auth_monitor_task.done()
        )
        return {"captured": captured, "monitoring": monitoring}

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
        external_game_roots = [DEFAULT_INSTALL_BASE, SDCARD_INSTALL_BASE]

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
        """Find a suitable Proton installation for umu-run."""
        compat_dir = os.path.expanduser("~/.local/share/Steam/compatibilitytools.d")
        if not os.path.isdir(compat_dir):
            return None

        # Prefer UMU-Proton, then GE-Proton (newest first)
        candidates = []
        for entry in os.listdir(compat_dir):
            full = os.path.join(compat_dir, entry)
            if os.path.isdir(full):
                if entry.startswith("UMU-Proton"):
                    candidates.insert(0, full)  # UMU-Proton first
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

        logger.warning("[Ubisoft] No Proton found in compatibilitytools.d")
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
    # ========================================================================

    async def _token_refresh_loop(self) -> None:
        """
        Background task: proactively refresh the Ubisoft API token every 30
        minutes so it never expires while the plugin is running.

        The REST API ticket lasts ~3-4 hours. Without proactive refresh,
        tickets expire silently between user interactions.
        """
        while True:
            try:
                await asyncio.sleep(30 * 60)  # 30 minutes
                if self.api.has_tokens():
                    ok = await self.api.refresh_token()
                    if ok:
                        logger.info("[Ubisoft] Background token refresh succeeded")
                    else:
                        logger.error(
                            "[Ubisoft] Background token refresh failed — "
                            "user may need to re-authenticate via the plugin"
                        )
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.warning(f"[Ubisoft] Token refresh loop error: {e}")

    def start_token_refresh(self) -> None:
        """Start the background token refresh loop."""
        self._refresh_task = asyncio.create_task(self._token_refresh_loop())
        logger.info("[Ubisoft] Background token refresh started (every 30 min)")

    def stop_token_refresh(self) -> None:
        """Stop the background token refresh loop."""
        task = getattr(self, "_refresh_task", None)
        if task and not task.done():
            task.cancel()
