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
import shutil
from typing import Any, Dict, List, Optional

from .base import Store, Game
from .ubisoft_api import UbisoftAPIClient

logger = logging.getLogger(__name__)

# ============================================================================
# Paths
# ============================================================================

DATA_DIR = os.path.expanduser("~/.local/share/unifideck")
ID_MAP_FILE = os.path.join(DATA_DIR, "ubisoft_id_map.json")
PREFIXES_DIR = os.path.join(DATA_DIR, "prefixes", "ubisoft")
TEMPLATE_DIR = os.path.join(PREFIXES_DIR, ".template")
INSTALLER_CACHE_DIR = os.path.join(DATA_DIR, "ubisoft_installer_cache")
INSTALLER_FILENAME = "UbisoftConnectInstaller.exe"
INSTALLER_URL = "https://static3.cdn.ubi.com/orbit/launcher_installer/UbisoftConnectInstaller.exe"
BOOTSTRAP_MARKER = "unifideck_ubisoft_bootstrap.marker"
DEFAULT_INSTALL_BASE = os.path.expanduser("~/Games/Ubisoft")

# SD card install path (mirrors download manager's StorageLocation.SDCARD)
SDCARD_INSTALL_BASE = "/run/media/mmcblk0p1/Games/Ubisoft"

# UPC paths within a Wine prefix
UPC_RELATIVE_PATH = "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe"
CONFIGURATIONS_RELATIVE_PATH = (
    "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/"
    "cache/configuration/configurations"
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
            # Auth succeeded -- trigger auto-sync
            if self.plugin_instance:
                logger.info("[Ubisoft] Triggering library sync after auth")
                asyncio.create_task(self.plugin_instance.force_sync_libraries())

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
            # Auth succeeded -- trigger auto-sync
            if self.plugin_instance:
                logger.info("[Ubisoft] Triggering library sync after 2FA auth")
                asyncio.create_task(self.plugin_instance.force_sync_libraries())

        return result

    async def logout(self) -> Dict[str, Any]:
        """Logout from Ubisoft Connect, clearing all state."""
        return self.api.logout()

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
        Install a game via upc.exe uplay://install protocol.

        Flow:
          1. Bootstrap per-game prefix (template clone or fresh install)
          2. Resolve space_id → install_id
          3. Inject UPC auth session + registry keys
          4. Launch upc.exe with uplay://install/{install_id}
          5. Monitor file system for progress
          6. Detect completion via uplay_install.state

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

            # Step 2: Resolve install_id
            install_id = self.resolve_install_id(game_id)
            if not install_id:
                # Try to populate ID map from configurations binary
                await self._refresh_id_map(game_id)
                install_id = self.resolve_install_id(game_id)

            if not install_id:
                return {
                    "success": False,
                    "error": "Could not resolve install_id for this game",
                }

            prefix_path = self.get_prefix_path(game_id)
            game_name = self._get_game_name(game_id)

            # Resolve install directory — use caller's install_path (SD card support)
            # or fall back to default internal storage
            install_base = install_path or DEFAULT_INSTALL_BASE
            install_dir = os.path.join(install_base, game_name or game_id)

            # Step 3: Inject session + registry
            self.inject_upc_session(prefix_path)
            self._inject_install_registry(prefix_path, install_id, install_dir)

            # Step 4: Launch upc.exe with install protocol
            upc_path = os.path.join(prefix_path, UPC_RELATIVE_PATH)
            if not os.path.exists(upc_path):
                # Try pfx/ subdirectory
                upc_path = os.path.join(prefix_path, "pfx", UPC_RELATIVE_PATH)
            if not os.path.exists(upc_path):
                return {"success": False, "error": "upc.exe not found in prefix"}

            umu_run = self._find_umu_run()
            if not umu_run:
                return {"success": False, "error": "umu-run not found"}

            python_bin = self._find_python()

            env = os.environ.copy()
            env["WINEPREFIX"] = prefix_path
            env["GAMEID"] = f"umu-ubisoft-{game_id}"
            env["STORE"] = "ubisoft"
            env["PROTON_VERB"] = "waitforexitandrun"
            env["STEAM_COMPAT_INSTALL_PATH"] = install_dir

            install_url = f"uplay://install/{install_id}"
            logger.info(f"[Ubisoft] Launching upc.exe with {install_url}")

            proc = await asyncio.create_subprocess_exec(
                python_bin, umu_run, upc_path, install_url,
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            # Step 5: Monitor file system for progress
            os.makedirs(install_dir, exist_ok=True)
            success = await self._monitor_install_progress(
                game_id, install_dir, proc, progress_callback
            )

            # Ensure process is terminated
            if proc.returncode is None:
                try:
                    proc.terminate()
                    await asyncio.wait_for(proc.wait(), timeout=10)
                except Exception:
                    try:
                        proc.kill()
                    except Exception:
                        pass

            if success:
                # Write ownership marker
                exe = self.find_game_executable(install_dir)
                await self.write_install_marker(game_id, install_dir, exe or "", game_name or "")
                final_size = self._get_directory_size(install_dir)
                logger.info(f"[Ubisoft] Game {game_id} installed successfully ({final_size / 1024 / 1024:.0f} MB)")
                return {
                    "success": True,
                    "install_path": install_dir,
                    "executable": exe,
                    "install_size": final_size,
                }
            else:
                return {"success": False, "error": "Installation timed out or failed"}

        except Exception as e:
            logger.exception(f"[Ubisoft] Install error for {game_id}: {e}")
            return {"success": False, "error": str(e)}

    async def uninstall_game(self, game_id: str) -> Dict[str, Any]:
        """
        Uninstall a game.

        Tries uplay://uninstall protocol first, falls back to direct deletion.

        Args:
            game_id: The game's space_id.

        Returns:
            {"success": True} or {"success": False, "error": "..."}
        """
        try:
            logger.info(f"[Ubisoft] Uninstalling game {game_id}")

            # Find the game's install directory
            game_info = self._detect_installed_game(game_id, self.get_prefix_path(game_id))
            install_path = game_info.get("install_path") if game_info else None

            # Try protocol-based uninstall first
            install_id = self.resolve_install_id(game_id)
            prefix_path = self.get_prefix_path(game_id)
            upc_path = os.path.join(prefix_path, UPC_RELATIVE_PATH)

            if install_id and os.path.exists(upc_path):
                try:
                    umu_run = self._find_umu_run()
                    python_bin = self._find_python()

                    if umu_run:
                        env = os.environ.copy()
                        env["WINEPREFIX"] = prefix_path
                        env["GAMEID"] = f"umu-ubisoft-{game_id}"
                        env["STORE"] = "ubisoft"
                        env["PROTON_VERB"] = "waitforexitandrun"

                        uninstall_url = f"uplay://uninstall/{install_id}"
                        logger.info(f"[Ubisoft] Trying protocol uninstall: {uninstall_url}")

                        proc = await asyncio.create_subprocess_exec(
                            python_bin, umu_run, upc_path, uninstall_url,
                            env=env,
                            stdout=asyncio.subprocess.PIPE,
                            stderr=asyncio.subprocess.PIPE,
                        )
                        try:
                            await asyncio.wait_for(proc.wait(), timeout=120)
                        except asyncio.TimeoutError:
                            proc.kill()
                            logger.warning("[Ubisoft] Protocol uninstall timed out")
                except Exception as e:
                    logger.warning(f"[Ubisoft] Protocol uninstall failed: {e}")

            # Fallback: Direct deletion of game directory
            if install_path and os.path.isdir(install_path):
                logger.info(f"[Ubisoft] Deleting game directory: {install_path}")
                max_retries = 3
                for attempt in range(max_retries):
                    try:
                        shutil.rmtree(install_path)
                        logger.info(f"[Ubisoft] Game directory removed")
                        break
                    except OSError as e:
                        if attempt < max_retries - 1:
                            logger.warning(f"[Ubisoft] Retry {attempt + 1}/{max_retries}: {e}")
                            await asyncio.sleep(2)
                        else:
                            # File-by-file fallback
                            logger.warning("[Ubisoft] Trying file-by-file deletion")
                            for root, dirs, files in os.walk(install_path, topdown=False):
                                for name in files:
                                    try:
                                        os.remove(os.path.join(root, name))
                                    except Exception:
                                        pass
                                for name in dirs:
                                    try:
                                        os.rmdir(os.path.join(root, name))
                                    except Exception:
                                        pass
                            try:
                                os.rmdir(install_path)
                            except Exception:
                                pass

            # Clean up registry keys from prefix
            self._clean_install_registry(prefix_path, install_id or "")

            logger.info(f"[Ubisoft] Game {game_id} uninstalled")
            return {"success": True}

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
            upc_path = os.path.join(prefix_path, UPC_RELATIVE_PATH)

            if not os.path.exists(upc_path):
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

            env = os.environ.copy()
            env["WINEPREFIX"] = prefix_path
            env["GAMEID"] = f"umu-ubisoft-{game_id}"
            env["STORE"] = "ubisoft"
            env["PROTON_VERB"] = "waitforexitandrun"

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

        return self._detect_installed_game(game_id, prefix_path)

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
        """Resolve spaceId to installId from cache."""
        entry = self._id_map_cache.get(space_id, {})
        return entry.get("install_id")

    def resolve_launch_id(self, space_id: str) -> Optional[str]:
        """Resolve spaceId to launchId from cache."""
        entry = self._id_map_cache.get(space_id, {})
        return entry.get("launch_id")

    def update_id_map(self, space_id: str, install_id: str, launch_id: str) -> None:
        """Add or update a mapping entry and persist."""
        self._id_map_cache[space_id] = {
            "install_id": install_id,
            "launch_id": launch_id,
        }
        self._save_id_map()

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

            env = os.environ.copy()
            env["WINEPREFIX"] = TEMPLATE_DIR
            env["GAMEID"] = "umu-ubisoft-template"
            env["STORE"] = "ubisoft"
            env["PROTON_VERB"] = "waitforexitandrun"

            python_bin = self._find_python()
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

            # Step 4: Verify upc.exe exists
            upc_path = os.path.join(TEMPLATE_DIR, UPC_RELATIVE_PATH)
            if not os.path.exists(upc_path):
                logger.error("[Ubisoft] upc.exe not found after install")
                return

            # Step 5: Write bootstrap marker
            marker_path = os.path.join(TEMPLATE_DIR, BOOTSTRAP_MARKER)
            with open(marker_path, "w") as f:
                f.write(f"template\ncreated={__import__('datetime').datetime.now().isoformat()}\n")

            logger.info("[Ubisoft] Template prefix created successfully")

        except Exception as e:
            logger.exception(f"[Ubisoft] Template prefix creation failed: {e}")

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
            upc_path = os.path.join(prefix_path, UPC_RELATIVE_PATH)
            if os.path.exists(upc_path):
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

            env = os.environ.copy()
            env["WINEPREFIX"] = prefix_path
            env["GAMEID"] = f"umu-ubisoft-{space_id}"
            env["STORE"] = "ubisoft"
            env["PROTON_VERB"] = "waitforexitandrun"

            python_bin = self._find_python()
            proc = await asyncio.create_subprocess_exec(
                python_bin, umu_run, installer_path, "/S",
                env=env,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
            await proc.communicate()

            upc_path = os.path.join(prefix_path, UPC_RELATIVE_PATH)
            if os.path.exists(upc_path):
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

        Writes the current ticket into the UPC settings file inside the prefix.
        """
        if not self.api.has_tokens():
            logger.warning("[Ubisoft] No tokens available for session injection")
            return False

        try:
            # UPC settings path within the prefix
            settings_dir = os.path.join(
                prefix_path,
                "drive_c", "users", "deck", "AppData", "Roaming",
                "Ubisoft", "Ubisoft Connect",
            )
            os.makedirs(settings_dir, exist_ok=True)
            settings_file = os.path.join(settings_dir, "settings.yml")

            # Build minimal YAML config (avoid PyYAML dependency)
            ticket = self.api.get_ticket() or ""
            user_id = self.api.get_user_id() or ""

            # Simple YAML write (no complex nested structures needed)
            config_lines = [
                "user:",
                "  remember_me: true",
                f"  restore_session: \"{ticket}\"",
                f"  userId: \"{user_id}\"",
                "",
            ]

            # If file exists, try to preserve other settings
            existing_content = ""
            if os.path.exists(settings_file):
                try:
                    with open(settings_file, "r") as f:
                        existing_content = f.read()
                except Exception:
                    pass

            # Write the config
            with open(settings_file, "w") as f:
                if existing_content and "user:" in existing_content:
                    # Replace user section in existing config
                    import re
                    new_user_section = "\n".join(config_lines)
                    result = re.sub(
                        r"user:.*?(?=\n\w|\Z)",
                        new_user_section,
                        existing_content,
                        flags=re.DOTALL,
                    )
                    f.write(result)
                else:
                    f.write("\n".join(config_lines))

            logger.info("[Ubisoft] UPC session injected into prefix")
            return True

        except Exception as e:
            logger.warning(f"[Ubisoft] Session injection failed: {e}")
            return False

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
        # Method 1: Check .unifideck_ubisoft marker in common install locations
        for base_dir in [DEFAULT_INSTALL_BASE, SDCARD_INSTALL_BASE]:
            if not os.path.exists(base_dir):
                continue
            for folder in os.listdir(base_dir):
                marker_path = os.path.join(base_dir, folder, ".unifideck_ubisoft")
                if os.path.exists(marker_path):
                    try:
                        with open(marker_path, "r") as f:
                            marker_data = json.load(f)
                        if marker_data.get("space_id") == space_id:
                            return {
                                "space_id": space_id,
                                "executable": marker_data.get("executable", ""),
                                "install_path": marker_data.get("install_path", ""),
                                "work_dir": marker_data.get("install_path", ""),
                                "title": marker_data.get("game_title", ""),
                            }
                    except Exception:
                        continue

        # Method 2: Check uplay_install.state binary in game directories
        for base_dir in [DEFAULT_INSTALL_BASE, SDCARD_INSTALL_BASE]:
            if not os.path.exists(base_dir):
                continue
            for folder in os.listdir(base_dir):
                state_file = os.path.join(base_dir, folder, "uplay_install.state")
                if os.path.exists(state_file):
                    try:
                        with open(state_file, "rb") as f:
                            first_byte = f.read(1)
                        if first_byte == b"\x0a":
                            game_dir = os.path.join(base_dir, folder)
                            exe = self.find_game_executable(game_dir)
                            return {
                                "space_id": space_id,
                                "executable": exe or "",
                                "install_path": game_dir,
                                "work_dir": game_dir,
                                "title": folder,
                            }
                    except Exception:
                        continue

        return None

    # ========================================================================
    # Utility Methods
    # ========================================================================

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
    # Install/Uninstall Helpers
    # ========================================================================

    def _get_game_name(self, space_id: str) -> Optional[str]:
        """Get human-readable game name from ID map cache."""
        entry = self._id_map_cache.get(space_id, {})
        return entry.get("name")

    async def _refresh_id_map(self, space_id: str) -> None:
        """
        Try to populate the ID map from the configurations binary.

        Scans all existing per-game prefixes for the configurations file
        and parses it to build the spaceId → installId/launchId mapping.
        """
        try:
            from .ubisoft_parser import build_id_map_from_configurations

            # Check template prefix first
            configs_path = os.path.join(TEMPLATE_DIR, CONFIGURATIONS_RELATIVE_PATH)
            if os.path.isfile(configs_path):
                new_map = build_id_map_from_configurations(configs_path)
                if new_map:
                    self._id_map_cache.update(new_map)
                    self._save_id_map()
                    return

            # Check existing per-game prefixes
            if os.path.isdir(PREFIXES_DIR):
                for entry in os.listdir(PREFIXES_DIR):
                    if entry.startswith("."):
                        continue
                    configs_path = os.path.join(
                        PREFIXES_DIR, entry, CONFIGURATIONS_RELATIVE_PATH
                    )
                    if os.path.isfile(configs_path):
                        new_map = build_id_map_from_configurations(configs_path)
                        if new_map:
                            self._id_map_cache.update(new_map)
                            self._save_id_map()
                            return

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
