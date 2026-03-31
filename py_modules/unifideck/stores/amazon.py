"""
Amazon Games Store connector using nile CLI.

This module handles all Amazon Games Store operations including authentication,
library fetching, and game installation via the nile CLI tool.
"""
import asyncio
import json
import logging
import os
import re
import shutil
from typing import Dict, Any, List, Optional

from .base import Store, Game

logger = logging.getLogger(__name__)

# ── Auth shortcut constants ───────────────────────────────────────────
DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "unifideck")
AMAZON_AUTH_SHORTCUT_STORE_ID = "amazon:amazon-auth"
AMAZON_AUTH_SHORTCUT_LAUNCH_WAIT_MS = 2000


class AmazonConnector(Store):
    """Handles Amazon Games via nile CLI"""

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        self.plugin_dir = plugin_dir
        self.plugin_instance = plugin_instance  # Reference to parent Plugin for auto-sync
        self.nile_bin = self._find_nile()
        self._pending_login_data = None  # Store login data during OAuth flow
        logger.info(f"Nile binary: {self.nile_bin}")
    
    @property
    def store_name(self) -> str:
        return 'amazon'

    def _find_nile(self) -> Optional[str]:
        """Find nile executable - checks bundled binary first, then system"""
        # Priority 1: Check bundled nile in plugin bin/ directory
        if self.plugin_dir:
            bundled_nile = os.path.join(self.plugin_dir, 'bin', 'nile')
            if os.path.isfile(bundled_nile) and os.access(bundled_nile, os.X_OK):
                logger.info(f"[Amazon] Using bundled nile: {bundled_nile}")
                return bundled_nile

        # Priority 2: Check system PATH
        nile_path = shutil.which("nile")
        if nile_path:
            logger.info(f"[Amazon] Using system nile: {nile_path}")
            return nile_path

        # Priority 3: Check ~/.local/bin explicitly
        local_bin_nile = os.path.expanduser("~/.local/bin/nile")
        if os.path.exists(local_bin_nile):
            logger.info(f"[Amazon] Using user nile: {local_bin_nile}")
            return local_bin_nile

        logger.warning("[Amazon] Nile not found - Amazon Games features unavailable")
        return None

    async def is_available(self) -> bool:
        """Check if nile is installed and authenticated"""
        logger.info(f"[Amazon] Checking availability, nile_bin={self.nile_bin}")

        if not self.nile_bin:
            logger.warning("[Amazon] Nile CLI not found - not installed")
            return False

        try:
            # Check for user.json which contains Amazon auth tokens
            nile_config = os.path.expanduser("~/.config/nile")
            user_file = os.path.join(nile_config, "user.json")

            if not os.path.exists(user_file):
                logger.info("[Amazon] No user.json found - not authenticated")
                return False

            # Verify the file has valid content
            try:
                with open(user_file, 'r') as f:
                    data = json.load(f)
                    if not data:
                        logger.info("[Amazon] user.json empty - not authenticated")
                        return False

                    # Check for customer_info which indicates valid auth
                    extensions = data.get('extensions', {})
                    if 'customer_info' not in extensions:
                        logger.info("[Amazon] user.json missing customer_info - not authenticated")
                        return False

                    logger.info("[Amazon] Status: Connected (authenticated)")
                    return True

            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"[Amazon] Invalid user.json: {e}")
                return False

        except Exception as e:
            logger.error(f"[Amazon] Exception checking status: {e}", exc_info=True)
            return False

    async def start_auth(self) -> Dict[str, Any]:
        """Start Amazon OAuth flow via auth shortcut + CDP interception on port 9222."""
        if not self.nile_bin:
            return {'success': False, 'error': 'nile not found'}

        try:
            logger.info("[Amazon] Starting OAuth flow...")

            # Call nile CLI to get login data
            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'auth', '--login', '--non-interactive',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                error_msg = stderr.decode() if stderr else 'Unknown error'
                logger.error(f"[Amazon] Auth failed: {error_msg}")
                return {'success': False, 'error': error_msg}

            try:
                login_data = json.loads(stdout.decode())
                self._pending_login_data = login_data
                auth_url = login_data.get('url', '')
                if not auth_url:
                    return {'success': False, 'error': 'No auth URL in nile response'}
                logger.info(f"[Amazon] Got login URL, preparing auth shortcut")
            except json.JSONDecodeError as e:
                logger.error(f"[Amazon] Failed to parse login data: {e}")
                return {'success': False, 'error': 'Failed to parse login response'}

            # Check if compatible browser is available (reuse Microsoft's detection)
            try:
                ms = self.plugin_instance.microsoft
                if not ms._browser.is_installed:
                    return {'success': True, 'needs_chromium': True, 'message': 'microsoft.chromiumRequired'}
            except Exception:
                pass

            # Cancel stale auth monitor
            if hasattr(self, '_auth_monitor_task') and self._auth_monitor_task and not self._auth_monitor_task.done():
                self._auth_monitor_task.cancel()

            # Write auth URL for launcher to read
            url_file = os.path.join(DATA_DIR, "amazon_auth_url.txt")
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(url_file, "w") as f:
                f.write(auth_url)

            # Ensure auth shortcut exists
            shortcut_appid = await self._ensure_amazon_auth_shortcut()

            # Start CDP monitor on port 9222
            self._auth_monitor_task = asyncio.create_task(self._monitor_and_complete_auth())

            return {
                'success': True,
                'chromium_auth': True,
                'shortcut_launch': True,
                'message': 'amazon.signInMessage',
            }

        except Exception as e:
            logger.error(f"[Amazon] Error starting auth: {e}")
            return {'success': False, 'error': str(e)}

    async def complete_auth(self, auth_code: str) -> Dict[str, Any]:
        """Complete Amazon OAuth with authorization code from browser"""
        if not self.nile_bin:
            return {'success': False, 'error': 'nile not found'}

        if not self._pending_login_data:
            return {'success': False, 'error': 'No pending login - call start_auth first'}

        try:
            login_data = self._pending_login_data
            logger.info(f"[Amazon] Completing auth with code...")

            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'register',
                '--code', auth_code,
                '--code-verifier', login_data.get('code_verifier', ''),
                '--serial', login_data.get('serial', ''),
                '--client-id', login_data.get('client_id', ''),
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            # Nile prints success message to stderr
            output = stderr.decode() if stderr else stdout.decode()
            
            if 'Succesfully registered' in output or 'Successfully registered' in output:
                self._pending_login_data = None  # Clear pending data
                logger.info("[Amazon] Authentication successful!")
                
                # Trigger auto-sync if plugin instance available
                if self.plugin_instance:
                    logger.info("[Amazon] Triggering library sync after auth")
                    asyncio.create_task(
                        self.plugin_instance.request_auth_sync(
                            source='auth:amazon',
                        )
                    )
                    
                return {'success': True, 'message': 'Authenticated successfully'}
            else:
                logger.error(f"[Amazon] Registration failed: {output}")
                return {'success': False, 'error': 'Authentication failed'}

        except Exception as e:
            logger.error(f"[Amazon] Error completing auth: {e}")
            return {'success': False, 'error': str(e)}

    async def _monitor_and_complete_auth(self):
        """Background task: intercept OAuth redirect via CDP on port 9222."""
        try:
            from ..auth.cdp_interceptor import intercept_oauth_code, close_cdp_auth_browser

            logger.info("[Amazon] Auth monitor started — polling CDP port 9222")
            code = await intercept_oauth_code(store='amazon', timeout=300, cdp_port=9222)

            if code:
                logger.info("[Amazon] ✓ Received OAuth code via CDP interception")
                result = await self.complete_auth(code)
                if result.get('success'):
                    logger.info("[Amazon] ✓ Authentication completed successfully!")
                    try:
                        closed = await close_cdp_auth_browser(cdp_port=9222, store="amazon")
                        if closed:
                            logger.info("[Amazon] ✓ Closed auth browser after successful sign-in")
                        else:
                            logger.debug("[Amazon] No auth browser targets to close")
                    except Exception as close_err:
                        logger.warning(f"[Amazon] Could not close auth browser: {close_err}")
                else:
                    logger.error(f"[Amazon] ✗ complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[Amazon] ✗ CDP interception timed out — no code received")
        except Exception as e:
            logger.error(f"[Amazon] ✗ Auth monitor error: {e}", exc_info=True)
        finally:
            url_file = os.path.join(DATA_DIR, "amazon_auth_url.txt")
            try:
                os.remove(url_file)
            except OSError:
                pass

    async def _ensure_amazon_auth_shortcut(self) -> Optional[int]:
        """Create or repair the persistent VDF shortcut for Amazon OAuth."""
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            logger.error("[Amazon] No shortcuts_manager available")
            return None

        try:
            from py_modules.unifideck.shortcuts.shortcuts_manager import (
                load_shortcuts_registry, register_shortcut
            )
            from py_modules.unifideck.shortcuts.launch_options import get_full_id

            sm = self.plugin_instance.shortcuts_manager
            launcher_path = os.path.join(self.plugin_dir or "", "bin", "unifideck-launcher")
            if not os.path.isfile(launcher_path):
                logger.error(f"[Amazon] Launcher not found at {launcher_path}")
                return None

            expected_appid = sm.generate_app_id("Amazon Games Sign-In", launcher_path)
            unsigned_id = expected_appid if expected_appid >= 0 else expected_appid + 2**32

            expected_launch_options = (
                f"{AMAZON_AUTH_SHORTCUT_STORE_ID} "
                "UNIFIDECK_AMAZON_ACTION=auth"
            )

            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})

            matching_indices = [
                idx for idx, s in shortcuts.items()
                if get_full_id(s.get('LaunchOptions', '')) == AMAZON_AUTH_SHORTCUT_STORE_ID
            ]

            correct_idx = None
            for idx in matching_indices:
                sc = shortcuts[idx]
                if (sc.get('appid') == expected_appid
                        and sc.get('AppName') == 'Amazon Games Sign-In'
                        and 'UNIFIDECK_AMAZON_ACTION=auth' in sc.get('LaunchOptions', '')):
                    correct_idx = idx
                    break

            vdf_dirty = False
            for idx in matching_indices:
                if idx != correct_idx:
                    logger.warning(f"[Amazon] Removing malformed auth VDF entry idx={idx}")
                    del shortcuts[idx]
                    vdf_dirty = True

            if correct_idx is None:
                existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
                next_idx = max(existing_indices, default=-1) + 1
                shortcuts[str(next_idx)] = {
                    'appid': expected_appid,
                    'AppName': 'Amazon Games Sign-In',
                    'exe': f'"{launcher_path}"',
                    'StartDir': f'"{os.path.dirname(launcher_path)}"',
                    'LaunchOptions': expected_launch_options,
                    'IsHidden': 1,
                    'AllowDesktopConfig': 1,
                    'OpenVR': 0,
                    'tags': {'0': 'Amazon'},
                }
                vdf_dirty = True
                logger.info(f"[Amazon] Created auth shortcut in VDF: appid={expected_appid} unsigned={unsigned_id}")

            if vdf_dirty:
                await sm.write_shortcuts(shortcuts_data)

            register_shortcut(AMAZON_AUTH_SHORTCUT_STORE_ID, expected_appid, "Amazon Games Sign-In")
            await sm._clear_proton_compatibility(expected_appid)
            await self._fetch_auth_shortcut_artwork(unsigned_id, force=(vdf_dirty and correct_idx is None))

            return unsigned_id

        except Exception as e:
            logger.error(f"[Amazon] Failed to create auth shortcut: {e}", exc_info=True)
            return None

    async def get_amazon_auth_shortcut_context(self) -> Dict[str, Any]:
        """Return the auth shortcut appid so the frontend can call RunGame()."""
        unsigned_id = await self._ensure_amazon_auth_shortcut()
        launcher_path = os.path.join(self.plugin_dir or "", "bin", "unifideck-launcher")
        launch_options = f"{AMAZON_AUTH_SHORTCUT_STORE_ID} UNIFIDECK_AMAZON_ACTION=auth"

        if not unsigned_id:
            logger.error("[Amazon] Auth shortcut creation/validation failed")
            return {"success": False, "error": "Auth shortcut not ready"}

        logger.info(f"[Amazon] Auth shortcut context: appid={unsigned_id}")
        return {
            "success": True,
            "appid_unsigned": unsigned_id,
            "launch_wait_ms": AMAZON_AUTH_SHORTCUT_LAUNCH_WAIT_MS,
            "launcher_path": launcher_path,
            "launch_options": launch_options,
        }

    async def _fetch_auth_shortcut_artwork(self, unsigned_id: int, force: bool = False) -> None:
        """Download artwork for the Amazon auth shortcut.

        Uses a bundled curated portrait grid and SGDB for remaining types.
        """
        try:
            plugin = self.plugin_instance
            if not plugin or not hasattr(plugin, 'steamgriddb') or not plugin.steamgriddb:
                logger.debug("[Amazon] SteamGridDB client not available, skipping artwork")
                return

            if not force:
                if hasattr(plugin, 'has_artwork') and await plugin.has_artwork(unsigned_id):
                    logger.debug("[Amazon] Auth shortcut artwork already exists")
                    return

            only_types = None
            if not force and hasattr(plugin, 'get_missing_artwork_types'):
                missing = await plugin.get_missing_artwork_types(unsigned_id)
                if missing:
                    only_types = missing
                    logger.info(f"[Amazon] Auth shortcut artwork gap-fill: {missing}")

            grid_path = plugin.steamgriddb.grid_path
            if not grid_path:
                logger.warning("[Amazon] Steam grid path not available")
                return

            need_types = only_types or {'grid', 'grid_l', 'hero', 'logo', 'icon'}

            # Portrait grid: use bundled curated image
            if 'grid' in need_types:
                bundled_grid = os.path.join(
                    self.plugin_dir or "", "assets", "amazon_games", "grid_p.png"
                )
                dest_grid = os.path.join(grid_path, f"{unsigned_id}p.jpg")
                if os.path.isfile(bundled_grid):
                    import shutil
                    shutil.copy2(bundled_grid, dest_grid)
                    logger.info("[Amazon] Copied bundled Amazon Games portrait grid")
                else:
                    logger.debug(f"[Amazon] Bundled portrait grid not found at {bundled_grid}")

            # Remaining types: fetch from SGDB
            sgdb_types = need_types - {'grid'}
            if sgdb_types:
                logger.info(f"[Amazon] Fetching SteamGridDB artwork for Amazon Games: {sgdb_types}")
                await plugin.steamgriddb.fetch_game_art(
                    title="Amazon Games",
                    app_id=unsigned_id,
                    only_types=sgdb_types,
                )
        except Exception as e:
            logger.warning(f"[Amazon] Auth shortcut artwork fetch failed: {e}")

    async def logout(self) -> Dict[str, Any]:
        """Logout from Amazon Games"""
        if not self.nile_bin:
            return {'success': False, 'error': 'nile not found'}

        try:
            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'auth', '--logout',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            await proc.communicate()

            logger.info("[Amazon] Logged out successfully")
            return {'success': True, 'message': 'Logged out successfully'}

        except Exception as e:
            logger.error(f"[Amazon] Error during logout: {e}")
            return {'success': False, 'error': str(e)}

    async def sync_library(self) -> bool:
        """Sync Amazon Games library from server"""
        if not self.nile_bin:
            return False

        try:
            logger.info("[Amazon] Syncing library from server...")
            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'library', 'sync',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info("[Amazon] Library sync complete")
                return True
            else:
                logger.warning(f"[Amazon] Library sync failed: {stderr.decode()}")
                return False

        except Exception as e:
            logger.error(f"[Amazon] Error syncing library: {e}")
            return False

    def get_game_official_url(self, game_id: str) -> Optional[str]:
        """Get official website URL from Amazon library metadata.

        Amazon games don't have individual store pages, but the library
        metadata includes a 'websites' dict with OFFICIAL, STEAM, etc.

        Args:
            game_id: Amazon game ID (e.g., 'amzn1.adg.product.xxx')

        Returns:
            The official website URL, or None if unavailable
        """
        nile_config = os.path.expanduser("~/.config/nile")
        library_file = os.path.join(nile_config, "library.json")

        if not os.path.exists(library_file):
            return None

        try:
            with open(library_file, 'r') as f:
                games_data = json.load(f)

            for game_data in games_data:
                product = game_data.get('product', {})
                if product.get('id') == game_id:
                    details = product.get('productDetail', {}).get('details', {})
                    websites = details.get('websites', {})

                    # Priority: OFFICIAL > STEAM
                    official = websites.get('OFFICIAL')
                    if official:
                        logger.debug(f"[Amazon] Got official URL for {game_id}: {official}")
                        return official

                    steam = websites.get('STEAM')
                    if steam:
                        logger.debug(f"[Amazon] Got Steam URL for {game_id}: {steam}")
                        return steam

            return None

        except Exception as e:
            logger.warning(f"[Amazon] Could not get official URL for {game_id}: {e}")
            return None

    async def get_library(self) -> List[Game]:
        """Get Amazon Games library via nile"""
        if not self.nile_bin:
            logger.warning("[Amazon] Nile CLI not found")
            return []

        try:
            # First sync library to get latest
            await self.sync_library()

            # Read library directly from nile's library.json file
            nile_config = os.path.expanduser("~/.config/nile")
            library_file = os.path.join(nile_config, "library.json")
            
            if not os.path.exists(library_file):
                logger.warning("[Amazon] library.json not found")
                return []
            
            with open(library_file, 'r') as f:
                games_data = json.load(f)

            games = []

            # Get installed games to mark install status
            installed = await self.get_installed()

            for game_data in games_data:
                product = game_data.get('product', {})
                game_id = product.get('id', '')
                title = product.get('title', 'Unknown')
                
                game = Game(
                    id=game_id,
                    title=title,
                    store='amazon',
                    is_installed=game_id in installed
                )
                games.append(game)

            logger.info(f"[Amazon] Found {len(games)} games")
            return games

        except Exception as e:
            logger.error(f"[Amazon] Error fetching library: {e}", exc_info=True)
            return []

    async def get_installed(self) -> Dict[str, Any]:
        """Get list of installed Amazon games from nile config"""
        nile_config = os.path.expanduser("~/.config/nile")
        installed_file = os.path.join(nile_config, "installed.json")

        if not os.path.exists(installed_file):
            return {}

        try:
            with open(installed_file, 'r') as f:
                installed_list = json.load(f)

            installed_dict = {}
            for game in installed_list:
                game_id = game.get('id', '')
                installed_dict[game_id] = {
                    'version': game.get('version', ''),
                    'path': game.get('path', '')
                }
            return installed_dict

        except Exception as e:
            logger.error(f"[Amazon] Error reading installed.json: {e}")
            return {}

    def get_installed_game_info(self, game_id: str) -> Optional[Dict[str, Any]]:
        """Get installed game info synchronously"""
        nile_config = os.path.expanduser("~/.config/nile")
        installed_file = os.path.join(nile_config, "installed.json")

        if not os.path.exists(installed_file):
            return None

        try:
            with open(installed_file, 'r') as f:
                installed_list = json.load(f)

            for game in installed_list:
                if game.get('id') == game_id:
                    install_path = game.get('path', '')
                    
                    # Parse fuel.json for executable
                    exe_path = self._get_executable_from_fuel(install_path)
                    
                    return {
                        'id': game_id,
                        'version': game.get('version', ''),
                        'path': install_path,
                        'executable': exe_path
                    }
            return None

        except Exception as e:
            logger.error(f"[Amazon] Error getting installed game info: {e}")
            return None

    def _get_executable_from_fuel(self, install_path: str) -> Optional[str]:
        """Get executable path from fuel.json
        
        FIX 5: Search subdirectories for fuel.json before giving up,
        then fallback to largest .exe heuristic.
        """
        if not install_path:
            return None

        # Search multiple locations for fuel.json
        search_paths = [
            install_path,
            os.path.join(install_path, 'game'),
            os.path.join(install_path, 'Game'),
        ]
        
        # Also check immediate subdirectories
        try:
            for item in os.listdir(install_path):
                subdir = os.path.join(install_path, item)
                if os.path.isdir(subdir) and subdir not in search_paths:
                    search_paths.append(subdir)
        except Exception:
            pass
        
        for search_dir in search_paths:
            fuel_path = os.path.join(search_dir, 'fuel.json')
            if not os.path.exists(fuel_path):
                continue
                
            try:
                # fuel.json might have comments, try json5 style parsing
                with open(fuel_path, 'r') as f:
                    content = f.read()
                    # Remove single-line comments
                    content = re.sub(r'//.*$', '', content, flags=re.MULTILINE)
                    fuel_data = json.loads(content)

                main_cmd = fuel_data.get('Main', {}).get('Command', '')
                if main_cmd:
                    exe_path = os.path.join(search_dir, main_cmd)
                    if os.path.isfile(exe_path):
                        logger.info(f"[Amazon] Found executable from fuel.json: {exe_path}")
                        return exe_path
                    else:
                        logger.warning(f"[Amazon] fuel.json Command not found: {exe_path}")

            except Exception as e:
                logger.warning(f"[Amazon] Error parsing {fuel_path}: {e}")
        
        # Fallback: Find largest .exe (similar to GOG/Epic fallback)
        logger.info(f"[Amazon] No fuel.json found, attempting .exe fallback for {install_path}")
        return self._find_largest_exe_fallback(install_path)
    
    def _find_largest_exe_fallback(self, install_path: str) -> Optional[str]:
        """Fallback: Find largest .exe in install directory"""
        import glob
        
        skip_patterns = ['unins', 'setup', 'install', 'crash', 'redist', 'vcredist']
        exe_candidates = []
        
        for pattern in ['*.exe', '**/*.exe']:
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
            logger.info(f"[Amazon] Fallback: Found largest exe ({exe_candidates[0][1]/1024/1024:.1f}MB): {exe_candidates[0][0]}")
            return exe_candidates[0][0]
        
        logger.warning(f"[Amazon] No executable found in {install_path}")
        return None

    async def get_game_size(self, game_id: str) -> Optional[int]:
        """Get game download size in bytes"""
        if not self.nile_bin:
            return None

        try:
            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'install', game_id, '--info', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                output = stdout.decode()
                # Find the JSON line (skip INFO/log lines)
                for line in output.strip().split('\n'):
                    if line.startswith('{'):
                        try:
                            info = json.loads(line)
                            download_size = info.get('download_size', 0)
                            logger.info(f"[Amazon] Game {game_id} size: {download_size} bytes")
                            return download_size
                        except json.JSONDecodeError:
                            continue
                
                logger.warning(f"[Amazon] Could not parse size info for {game_id}")
                return None

        except Exception as e:
            logger.error(f"[Amazon] Error getting game size: {e}")

        return None

    async def install_game(self, game_id: str, base_path: str = None, progress_callback=None) -> Dict[str, Any]:
        """Install Amazon game using nile CLI"""
        if not self.nile_bin:
            return {'success': False, 'error': 'Nile CLI not found'}

        try:
            if not base_path:
                base_path = os.path.expanduser("~/Games/Amazon")
            os.makedirs(base_path, exist_ok=True)

            logger.info(f"[Amazon] Starting installation of {game_id} to {base_path}")

            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'install', game_id,
                '--base-path', base_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            # Parse progress from output
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                logger.info(f"[Amazon Install] {line_str}")

                # Parse progress: [Installation] [XX%] message
                if '[Installation]' in line_str and '%' in line_str:
                    match = re.search(r'\[(\d+)%\]', line_str)
                    if match and progress_callback:
                        progress = int(match.group(1))
                        await progress_callback(progress)

            await proc.wait()

            if proc.returncode == 0:
                # Get install info
                info_proc = await asyncio.create_subprocess_exec(
                    self.nile_bin, 'install', game_id, '--info', '--json',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, _ = await info_proc.communicate()

                install_path = None
                exe_path = None

                if info_proc.returncode == 0:
                    try:
                        info = json.loads(stdout.decode())
                        install_path = info.get('game', {}).get('path', '')
                    except:
                        pass

                # Fallback: check installed.json
                if not install_path:
                    installed = await self.get_installed()
                    if game_id in installed:
                        install_path = installed[game_id].get('path', '')

                if install_path:
                    exe_path = self._get_executable_from_fuel(install_path)
                    logger.info(f"[Amazon] Successfully installed {game_id} to {install_path}")
                    
                    # Write manifest for recovery after plugin reinstall
                    try:
                        from ..discovery.startup import write_game_manifest
                        # Try to get game title from library
                        game_title = game_id  # Default to ID
                        try:
                            nile_config = os.path.expanduser("~/.config/nile")
                            library_file = os.path.join(nile_config, "library.json")
                            if os.path.exists(library_file):
                                with open(library_file, 'r') as f:
                                    games_data = json.load(f)
                                for game_data in games_data:
                                    product = game_data.get('product', {})
                                    if product.get('id') == game_id:
                                        game_title = product.get('title', game_id)
                                        break
                        except Exception:
                            pass
                        
                        exe_relative = os.path.relpath(exe_path, install_path) if exe_path else ""
                        write_game_manifest(
                            install_path=install_path,
                            store="amazon",
                            game_id=game_id,
                            title=game_title,
                            executable_relative=exe_relative,
                            platform="windows"
                        )
                    except Exception as e:
                        logger.warning(f"[Amazon] Failed to write manifest: {e}")
                    
                    return {
                        'success': True,
                        'install_path': install_path,
                        'exe_path': exe_path,
                        'message': f'Successfully installed {game_id}'
                    }
                else:
                    return {
                        'success': True,
                        'install_path': base_path,
                        'message': f'Successfully installed {game_id} (path uncertain)'
                    }
            else:
                logger.error(f"[Amazon] Installation failed for {game_id}")
                return {
                    'success': False,
                    'error': 'Installation failed - check logs for details'
                }

        except Exception as e:
            logger.error(f"[Amazon] Error installing game {game_id}: {e}")
            return {'success': False, 'error': str(e)}

    async def uninstall_game(self, game_id: str) -> Dict[str, Any]:
        """Uninstall Amazon game using nile CLI"""
        if not self.nile_bin:
            return {'success': False, 'error': 'Nile CLI not found'}

        try:
            logger.info(f"[Amazon] Starting uninstallation of {game_id}")

            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'uninstall', game_id, '--yes',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info(f"[Amazon] Successfully uninstalled {game_id}")
                return {
                    'success': True,
                    'message': f'Successfully uninstalled {game_id}'
                }
            else:
                error_msg = stderr.decode() if stderr else 'Unknown error'
                logger.error(f"[Amazon] Uninstallation failed: {error_msg}")
                return {
                    'success': False,
                    'error': f'Uninstallation failed: {error_msg}'
                }

        except Exception as e:
            logger.error(f"[Amazon] Error uninstalling game {game_id}: {e}")
            return {'success': False, 'error': str(e)}

    async def check_for_updates(self) -> List[str]:
        """Check which installed Amazon games have updates available.
        
        Uses `nile list-updates --json` which returns a JSON array of
        game IDs with available updates. Verified: ~1.2s for 8 games.
        
        Returns:
            List of game IDs that have updates available.
        """
        if not self.nile_bin:
            return []

        try:
            # Sync library first to get latest metadata
            await self.sync_library()

            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'list-updates', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(f"[Amazon] check_for_updates failed: {stderr.decode()}")
                return []

            output = stdout.decode().strip()
            if not output:
                logger.info("[Amazon] No updates available")
                return []

            updates = json.loads(output)
            if isinstance(updates, list):
                logger.info(f"[Amazon] Found {len(updates)} games with updates: {updates}")
                return updates
            else:
                logger.warning(f"[Amazon] Unexpected list-updates format: {type(updates)}")
                return []

        except Exception as e:
            logger.error(f"[Amazon] Error checking for updates: {e}")
            return []

    async def update_game(self, game_id: str, install_path: Optional[str] = None) -> Dict[str, Any]:
        """Update an installed Amazon game.
        
        Amazon updates via nile are full re-downloads (no delta patching).
        Uses `nile install <game_id>` which overwrites the existing install.
        
        Args:
            game_id: Amazon game ID.
            install_path: Not used (nile tracks install paths).
            
        Returns:
            Dict with 'success' and optionally 'error'.
        """
        if not self.nile_bin:
            return {'success': False, 'error': 'Nile CLI not found'}

        try:
            logger.info(f"[Amazon] Starting update (full re-download) for {game_id}")

            proc = await asyncio.create_subprocess_exec(
                self.nile_bin, 'install', game_id,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                if line_str:
                    logger.info(f"[Amazon Update] {line_str}")

            await proc.wait()

            if proc.returncode == 0:
                logger.info(f"[Amazon] Successfully updated {game_id}")
                return {'success': True, 'message': f'Successfully updated {game_id}'}
            else:
                logger.error(f"[Amazon] Update failed for {game_id}")
                return {'success': False, 'error': 'Update failed - check logs'}

        except Exception as e:
            logger.error(f"[Amazon] Error updating {game_id}: {e}")
            return {'success': False, 'error': str(e)}
