"""
Epic Games Store connector using legendary CLI.

This module handles all Epic Games Store operations including authentication,
library fetching, and game installation via the legendary CLI tool.
"""
import asyncio
import json
import logging
import os
import re
import shutil
import time
from typing import Dict, Any, List, Optional

from .base import Store, Game

logger = logging.getLogger(__name__)

# Global caches for legendary CLI results (performance optimization)
_legendary_installed_cache = {
    'data': None,
    'timestamp': 0,
    'ttl': 30  # 30 second cache
}

_legendary_info_cache = {}  # Per-game info cache

# ── Auth shortcut constants ───────────────────────────────────────────
DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "unifideck")
EPIC_AUTH_SHORTCUT_STORE_ID = "epic:epic-auth"
EPIC_AUTH_SHORTCUT_LAUNCH_WAIT_MS = 2000


class EpicConnector(Store):
    """Handles Epic Games Store via legendary CLI"""

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        self.plugin_dir = plugin_dir
        self.plugin_instance = plugin_instance  # Reference to parent Plugin for auto-sync
        self.legendary_bin = self._find_legendary()
        logger.info(f"Legendary binary: {self.legendary_bin}")
    
    @property
    def store_name(self) -> str:
        return 'epic'

    def _find_legendary(self) -> Optional[str]:
        """Find legendary executable - checks bundled binary first, then system"""
        # Priority 1: Check bundled legendary in plugin bin/ directory
        if self.plugin_dir:
            bundled_legendary = os.path.join(self.plugin_dir, 'bin', 'legendary')
            if os.path.isfile(bundled_legendary) and os.access(bundled_legendary, os.X_OK):
                logger.info(f"[EPIC] Using bundled legendary: {bundled_legendary}")
                return bundled_legendary

        # Priority 2: Check system PATH
        legendary_path = shutil.which("legendary")
        if legendary_path:
            logger.info(f"[EPIC] Using system legendary: {legendary_path}")
            return legendary_path

        # Priority 3: Check ~/.local/bin explicitly
        local_bin_legendary = os.path.expanduser("~/.local/bin/legendary")
        if os.path.exists(local_bin_legendary):
            logger.info(f"[EPIC] Using user legendary: {local_bin_legendary}")
            return local_bin_legendary

        logger.warning("[EPIC] Legendary not found - Epic features unavailable")
        logger.info("[EPIC] Install with: pip install --user legendary-gl")
        return None

    async def is_available(self) -> bool:
        """Check if legendary is installed and authenticated"""
        logger.info(f"[EPIC] Checking availability, legendary_bin={self.legendary_bin}")

        if not self.legendary_bin:
            logger.warning("[EPIC] Legendary CLI not found - not installed")
            return False

        try:
            # Check for user.json which contains Epic auth tokens
            legendary_config = os.path.expanduser("~/.config/legendary/user.json")

            if not os.path.exists(legendary_config):
                logger.info("[EPIC] No user.json found - not authenticated")
                return False

            # Verify the file has valid content with access token
            try:
                with open(legendary_config, 'r') as f:
                    data = json.load(f)
                    if not data:
                        logger.info("[EPIC] user.json empty - not authenticated")
                        return False

                    # Check for access_token to ensure it's a valid auth file
                    if 'access_token' not in data:
                        logger.info("[EPIC] user.json missing access_token - not authenticated")
                        return False

                    logger.info("[EPIC] Status: Connected (authenticated)")
                    return True

            except (json.JSONDecodeError, Exception) as e:
                logger.warning(f"[EPIC] Invalid user.json: {e}")
                return False

        except Exception as e:
            logger.error(f"[EPIC] Exception checking status: {e}", exc_info=True)
            return False

    async def start_auth(self) -> Dict[str, Any]:
        """Start Epic OAuth flow via auth shortcut + CDP interception on port 9222."""
        if not self.legendary_bin:
            return {'success': False, 'error': 'legendary not found'}

        try:
            # Run legendary auth to get the authorization URL
            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'auth',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            auth_url = None
            output_lines = []
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_text = line.decode().strip()
                output_lines.append(line_text)

                if 'https://' in line_text:
                    for word in line_text.split():
                        if word.startswith('https://') and ('epicgames.com' in word or 'epic' in word.lower()):
                            auth_url = word
                            break
                    if auth_url:
                        break

            if not auth_url:
                all_output = "\n".join(output_lines)
                logger.error(f"[EPIC] No auth URL found in output: {all_output}")
                return {'success': False, 'error': f'Could not get auth URL'}

            logger.info(f"[EPIC] Got Epic auth URL: {auth_url[:80]}...")

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
            url_file = os.path.join(DATA_DIR, "epic_auth_url.txt")
            os.makedirs(DATA_DIR, exist_ok=True)
            with open(url_file, "w") as f:
                f.write(auth_url)

            # Ensure auth shortcut exists
            shortcut_appid = await self._ensure_epic_auth_shortcut()

            # Start CDP monitor on port 9222
            self._auth_monitor_task = asyncio.create_task(self._monitor_and_complete_auth())

            return {
                'success': True,
                'chromium_auth': True,
                'shortcut_launch': True,
                'message': 'epic.signInMessage',
            }

        except Exception as e:
            logger.error(f"[EPIC] Error starting Epic auth: {e}")
            return {'success': False, 'error': str(e)}

    async def _monitor_and_complete_auth(self):
        """Background task: intercept OAuth redirect via CDP on port 9222."""
        try:
            from ..auth.cdp_interceptor import intercept_oauth_code, close_cdp_auth_browser

            logger.info("[EPIC] Auth monitor started — polling CDP port 9222")
            code = await intercept_oauth_code(store='epic', timeout=300, cdp_port=9222)

            if code:
                logger.info("[EPIC] ✓ Received OAuth code via CDP interception")
                result = await self.complete_auth(code)
                if result['success']:
                    logger.info("[EPIC] ✓ Authentication completed successfully!")
                    try:
                        closed = await close_cdp_auth_browser(cdp_port=9222, store="epic")
                        if closed:
                            logger.info("[EPIC] ✓ Closed auth browser after successful sign-in")
                        else:
                            logger.debug("[EPIC] No auth browser targets to close")
                    except Exception as close_err:
                        logger.warning(f"[EPIC] Could not close auth browser: {close_err}")
                    if self.plugin_instance:
                        logger.info("[EPIC] Queueing automatic library sync...")
                        asyncio.create_task(
                            self.plugin_instance.request_auth_sync(source='auth:epic')
                        )
                else:
                    logger.error(f"[EPIC] ✗ complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[EPIC] ✗ CDP interception timed out — no code received")
        except Exception as e:
            logger.error(f"[EPIC] ✗ Auth monitor error: {e}", exc_info=True)
        finally:
            url_file = os.path.join(DATA_DIR, "epic_auth_url.txt")
            try:
                os.remove(url_file)
            except OSError:
                pass

    async def complete_auth(self, auth_code: str) -> Dict[str, Any]:
        """Complete Epic OAuth flow with authorization code"""
        if not self.legendary_bin:
            return {'success': False, 'error': 'legendary not found'}

        try:
            # Run legendary auth with the code
            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'auth', '--code', auth_code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )

            stdout, stderr = await proc.communicate()

            if proc.returncode == 0:
                logger.info("Epic authentication successful")
                return {'success': True, 'message': 'Successfully authenticated with Epic Games'}
            else:
                error_msg = stderr.decode() or stdout.decode()
                logger.error(f"Epic auth failed: {error_msg}")
                return {'success': False, 'error': error_msg}

        except Exception as e:
            logger.error(f"Error completing Epic auth: {e}")
            return {'success': False, 'error': str(e)}

    async def _ensure_epic_auth_shortcut(self) -> Optional[int]:
        """Create or repair the persistent VDF shortcut for Epic OAuth."""
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            logger.error("[EPIC] No shortcuts_manager available")
            return None

        try:
            from py_modules.unifideck.shortcuts.shortcuts_manager import (
                load_shortcuts_registry, register_shortcut
            )
            from py_modules.unifideck.shortcuts.launch_options import get_full_id

            sm = self.plugin_instance.shortcuts_manager
            launcher_path = os.path.join(self.plugin_dir or "", "bin", "unifideck-launcher")
            if not os.path.isfile(launcher_path):
                logger.error(f"[EPIC] Launcher not found at {launcher_path}")
                return None

            expected_appid = sm.generate_app_id("Epic Games Sign-In", launcher_path)
            unsigned_id = expected_appid if expected_appid >= 0 else expected_appid + 2**32

            expected_launch_options = (
                f"{EPIC_AUTH_SHORTCUT_STORE_ID} "
                "UNIFIDECK_EPIC_ACTION=auth"
            )

            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})

            matching_indices = [
                idx for idx, s in shortcuts.items()
                if get_full_id(s.get('LaunchOptions', '')) == EPIC_AUTH_SHORTCUT_STORE_ID
            ]

            correct_idx = None
            for idx in matching_indices:
                sc = shortcuts[idx]
                if (sc.get('appid') == expected_appid
                        and sc.get('AppName') == 'Epic Games Sign-In'
                        and 'UNIFIDECK_EPIC_ACTION=auth' in sc.get('LaunchOptions', '')):
                    correct_idx = idx
                    break

            vdf_dirty = False
            for idx in matching_indices:
                if idx != correct_idx:
                    logger.warning(f"[EPIC] Removing malformed auth VDF entry idx={idx}")
                    del shortcuts[idx]
                    vdf_dirty = True

            if correct_idx is None:
                existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
                next_idx = max(existing_indices, default=-1) + 1
                shortcuts[str(next_idx)] = {
                    'appid': expected_appid,
                    'AppName': 'Epic Games Sign-In',
                    'exe': f'"{launcher_path}"',
                    'StartDir': f'"{os.path.dirname(launcher_path)}"',
                    'LaunchOptions': expected_launch_options,
                    'IsHidden': 1,
                    'AllowDesktopConfig': 1,
                    'OpenVR': 0,
                    'tags': {'0': 'Epic'},
                }
                vdf_dirty = True
                logger.info(f"[EPIC] Created auth shortcut in VDF: appid={expected_appid} unsigned={unsigned_id}")

            if vdf_dirty:
                await sm.write_shortcuts(shortcuts_data)

            register_shortcut(EPIC_AUTH_SHORTCUT_STORE_ID, expected_appid, "Epic Games Sign-In")
            await sm._clear_proton_compatibility(expected_appid)
            await self._fetch_auth_shortcut_artwork(unsigned_id, force=(vdf_dirty and correct_idx is None))

            return unsigned_id

        except Exception as e:
            logger.error(f"[EPIC] Failed to create auth shortcut: {e}", exc_info=True)
            return None

    async def get_epic_auth_shortcut_context(self) -> Dict[str, Any]:
        """Return the auth shortcut appid so the frontend can call RunGame()."""
        unsigned_id = await self._ensure_epic_auth_shortcut()
        launcher_path = os.path.join(self.plugin_dir or "", "bin", "unifideck-launcher")
        launch_options = f"{EPIC_AUTH_SHORTCUT_STORE_ID} UNIFIDECK_EPIC_ACTION=auth"

        if not unsigned_id:
            logger.error("[EPIC] Auth shortcut creation/validation failed")
            return {"success": False, "error": "Auth shortcut not ready"}

        logger.info(f"[EPIC] Auth shortcut context: appid={unsigned_id}")
        return {
            "success": True,
            "appid_unsigned": unsigned_id,
            "launch_wait_ms": EPIC_AUTH_SHORTCUT_LAUNCH_WAIT_MS,
            "launcher_path": launcher_path,
            "launch_options": launch_options,
        }

    async def _fetch_auth_shortcut_artwork(self, unsigned_id: int, force: bool = False) -> None:
        """Download SteamGridDB artwork for the Epic auth shortcut."""
        try:
            plugin = self.plugin_instance
            if not plugin or not hasattr(plugin, 'steamgriddb') or not plugin.steamgriddb:
                logger.debug("[EPIC] SteamGridDB client not available, skipping artwork")
                return

            if not force:
                if hasattr(plugin, 'has_artwork') and await plugin.has_artwork(unsigned_id):
                    logger.debug("[EPIC] Auth shortcut artwork already exists")
                    return

            only_types = None
            if not force and hasattr(plugin, 'get_missing_artwork_types'):
                missing = await plugin.get_missing_artwork_types(unsigned_id)
                if missing:
                    only_types = missing
                    logger.info(f"[EPIC] Auth shortcut artwork gap-fill: {missing}")

            logger.info(f"[EPIC] Fetching SteamGridDB artwork for Epic Games Store (force={force})")
            await plugin.steamgriddb.fetch_game_art(
                title="Epic Games Store",
                app_id=unsigned_id,
                only_types=only_types,
            )
        except Exception as e:
            logger.warning(f"[EPIC] Auth shortcut artwork fetch failed: {e}")

    async def logout(self) -> Dict[str, Any]:
        """Logout from Epic Games"""
        try:
            from ..auth.browser import CDPOAuthMonitor

            if self.legendary_bin:
                proc = await asyncio.create_subprocess_exec(
                    self.legendary_bin, 'auth', '--delete',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                await proc.communicate()
            else:
                logger.warning("[EPIC] Legendary not found during logout - clearing local state only")

            for path in (
                os.path.expanduser("~/.config/legendary"),
                os.path.expanduser("~/.cache/legendary"),
            ):
                if not os.path.exists(path):
                    continue
                try:
                    if os.path.isdir(path):
                        shutil.rmtree(path)
                    else:
                        os.remove(path)
                    logger.info(f"[EPIC] Cleared auth state: {path}")
                except Exception as e:
                    logger.warning(f"[EPIC] Could not clear {path}: {e}")

            logger.info("Logged out from Epic Games")

            # Clear browser cookies for Epic
            monitor = CDPOAuthMonitor()
            await monitor.clear_cookies_for_domain('epicgames.com')

            return {'success': True, 'message': 'Logged out from Epic Games'}

        except Exception as e:
            logger.error(f"Error logging out from Epic: {e}")
            return {'success': False, 'error': str(e)}

    def _should_filter_game(self, game_data: dict) -> bool:
        """Check if a game should be filtered from library.
        
        Filters out:
        - Unreal Engine assets/plugins/projects (namespace='ue' or matching categories)
        - Mods (category path='mods')
        - Mobile-only games (all platforms are Android/iOS)
        
        Based on Heroic Games Launcher's filtering logic.
        """
        metadata = game_data.get('metadata', {})
        
        # Filter 1: Unreal Engine namespace
        if metadata.get('namespace') == 'ue':
            logger.debug(f"[Epic] Filtered (UE namespace): {game_data.get('app_title')}")
            return True
        
        # Filter 2: UE categories (assets, plugins, projects)
        ue_categories = ['assets', 'asset-format', 'plugins', 'projects']
        categories = metadata.get('categories', [])
        for cat in categories:
            if cat.get('path') in ue_categories:
                logger.debug(f"[Epic] Filtered (UE category): {game_data.get('app_title')}")
                return True
        
        # Filter 3: Mods
        for cat in categories:
            if cat.get('path') == 'mods':
                logger.debug(f"[Epic] Filtered (mod): {game_data.get('app_title')}")
                return True
        
        # Filter 4: Mobile-only games
        release_info = metadata.get('releaseInfo', [])
        if release_info:
            all_mobile = all(
                info.get('platform', []) and
                all(p in ('Android', 'iOS') for p in info.get('platform', []))
                for info in release_info
            )
            if all_mobile:
                logger.debug(f"[Epic] Filtered (mobile-only): {game_data.get('app_title')}")
                return True
        
        return False

    async def get_library(self) -> List[Game]:
        """Get Epic Games library via legendary"""
        if not self.legendary_bin:
            logger.warning("Legendary CLI not found")
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'list', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(f"legendary list failed: {stderr.decode()}")
                return []

            games_data = json.loads(stdout.decode())
            games = []
            filtered_count = 0

            for game_data in games_data:
                # Filter out UE assets, plugins, mods, and mobile-only games
                if self._should_filter_game(game_data):
                    filtered_count += 1
                    continue
                
                game = Game(
                    id=game_data.get('app_name', ''),
                    title=game_data.get('app_title', ''),
                    store='epic',
                    is_installed=False  # legendary list shows all games, not just installed
                )
                games.append(game)

            logger.info(f"Found {len(games)} Epic games (filtered {filtered_count} UE/plugin/mod items)")
            return games

        except Exception as e:
            logger.error(f"Error fetching Epic library: {e}")
            return []

    async def get_installed(self) -> Dict[str, Any]:
        """
        Get installed Epic games with caching for performance
        Returns dict of {app_name: metadata_dict}
        """
        global _legendary_installed_cache

        if not self.legendary_bin:
            return {}

        # Check cache first
        current_time = time.time()
        if (_legendary_installed_cache['data'] is not None and
            current_time - _legendary_installed_cache['timestamp'] < _legendary_installed_cache['ttl']):
            logger.info("Returning cached legendary list-installed")
            return _legendary_installed_cache['data']

        # Cache miss - run legendary command
        logger.info("Cache miss - running legendary list-installed")
        try:
            # We strictly want the full JSON metadata
            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'list-installed', '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(f"legendary list-installed failed: {stderr.decode()}")
                return {}

            games_data = json.loads(stdout.decode())
            
            # Convert list to dict keyed by app_name
            installed_map = {}
            for g in games_data:
                app_name = g.get('app_name')
                if app_name:
                    installed_map[app_name] = g

            # Cache the result
            _legendary_installed_cache['data'] = installed_map
            _legendary_installed_cache['timestamp'] = current_time

            return installed_map

        except Exception as e:
            logger.error(f"Error fetching installed Epic games: {e}")
            return {}

    async def get_game_size(self, game_id: str) -> Optional[int]:
        """Get game download size in bytes from Epic/Legendary with caching

        Args:
            game_id: Epic game app_name (ID)

        Returns:
            Download size in bytes, or None if unable to determine
        """
        global _legendary_info_cache

        if not self.legendary_bin:
            return None

        # Check cache first
        if game_id in _legendary_info_cache:
            cache_entry = _legendary_info_cache[game_id]
            if time.time() - cache_entry['timestamp'] < 300:  # 5 minute cache
                logger.info(f"Returning cached size for {game_id}")
                return cache_entry['size']

        # Cache miss - run legendary info
        try:
            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'info', game_id, '--json',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            try:
                stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning(f"legendary info timed out for {game_id}")
                return None

            if proc.returncode == 0:
                info = json.loads(stdout.decode())
                # Parse size from legendary info output
                # legendary info returns manifest with download_size
                manifest = info.get('manifest', {})
                download_size = manifest.get('download_size', 0)
                logger.info(f"[Epic] Game {game_id} size: {download_size} bytes")

                # Cache the result
                _legendary_info_cache[game_id] = {
                    'size': download_size,
                    'timestamp': time.time()
                }

                return download_size
            else:
                logger.warning(f"legendary info failed for {game_id}: {stderr.decode()}")
                return None

        except Exception as e:
            logger.error(f"Error getting game size for {game_id}: {e}")
            return None

    def _find_executable_fallback(self, install_path: str) -> Optional[str]:
        """Scan install directory for likely game executable when manifest lacks launch_exe.
        
        Args:
            install_path: Game installation directory
            
        Returns:
            Path to likely game executable, or None if not found
        """
        if not os.path.isdir(install_path):
            return None
        
        import glob
        
        # Skip patterns - these are NOT game executables
        skip_patterns = [
            'unins', 'setup', 'install', 'crash', 'ue4prereq', 'redist',
            'vcredist', 'dxsetup', 'directx', 'launcher', 'easyanticheat',
            'battleye', 'eos_', 'eossdk', 'dotnet'
        ]
        
        # Common patterns for Epic/Unreal games, ordered by likelihood
        exe_patterns = [
            # Root level executables (most common)
            "*.exe",
            # Unreal Engine patterns
            "Binaries/Win64/*.exe",
            "Binaries/Win32/*.exe",
            "**/Binaries/Win64/*.exe",
            "**/Binaries/Win32/*.exe",
            # Shipping builds
            "**/Shipping/*.exe",
            # Game subfolder
            "Game/*.exe",
            "**/Game/*.exe",
        ]
        
        candidates = []
        
        for pattern in exe_patterns:
            try:
                full_pattern = os.path.join(install_path, pattern)
                matches = glob.glob(full_pattern, recursive=('**' in pattern))
                
                for match in matches:
                    basename = os.path.basename(match).lower()
                    
                    # Skip if matches any skip pattern
                    if any(skip in basename for skip in skip_patterns):
                        continue
                    
                    # Skip if in a redistributables folder
                    if any(skip in match.lower() for skip in ['redistributables', 'redist', '__installer']):
                        continue
                    
                    # Get file size to prioritize larger executables (actual game vs utilities)
                    try:
                        size = os.path.getsize(match)
                        candidates.append((match, size))
                    except OSError:
                        candidates.append((match, 0))
                        
            except Exception as e:
                logger.debug(f"[Epic] Error scanning pattern {pattern}: {e}")
        
        if not candidates:
            return None
        
        # Sort by size descending - larger executables are more likely to be the game
        candidates.sort(key=lambda x: x[1], reverse=True)
        
        logger.info(f"[Epic] Found {len(candidates)} executable candidates, selecting: {candidates[0][0]}")
        return candidates[0][0]

    async def install_game(self, game_id: str, base_path: str = None, progress_callback=None) -> Dict[str, Any]:
        """Install Epic game using legendary CLI

        Args:
            game_id: Epic game app_name (ID)
            base_path: Optional install directory (defaults to ~/Games/Epic)
            progress_callback: Optional async function to call with progress updates

        Returns:
            Dict with success status, install_path, and error if any
        """
        if not self.legendary_bin:
            return {
                'success': False,
                'error': 'Legendary CLI not found'
            }

        try:
            # legendary install GAME_ID --base-path ~/Games/Epic
            if not base_path:
                base_path = os.path.expanduser("~/Games/Epic")
            os.makedirs(base_path, exist_ok=True)

            logger.info(f"[Epic] Starting installation of {game_id} to {base_path}")

            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'install', game_id,
                '--base-path', base_path,
                '--with-dlcs',  # Automatically install all owned DLCs
                '--yes',  # Accept prompts automatically
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            # Stream output to track progress
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break

                line_str = line.decode().strip()

                # Parse progress from legendary output
                # Example: "Progress: [##########] 45.2% (1.2 GB / 2.5 GB)"
                if 'Progress:' in line_str:
                    match = re.search(r'(\d+\.?\d*)%', line_str)
                    if match:
                        percentage = float(match.group(1))
                        logger.info(f"[Epic Download] {game_id}: {percentage:.1f}%")

                        if progress_callback:
                            await progress_callback({
                                'progress': percentage,
                                'status': line_str
                            })
                    else:
                        logger.info(f"[Epic Install] {line_str}")
                elif line_str:  # Log other output
                    logger.info(f"[Epic Install] {line_str}")

            await proc.wait()

            if proc.returncode == 0:
                # Get actual install path from legendary (don't assume directory name)
                logger.info(f"[Epic] Installation complete, getting actual install path for {game_id}")

                info_proc = await asyncio.create_subprocess_exec(
                    self.legendary_bin, 'info', game_id, '--json',
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE
                )
                stdout, stderr = await info_proc.communicate()

                if info_proc.returncode == 0:
                    try:
                        info = json.loads(stdout.decode())
                        install_path = info.get('install', {}).get('install_path', '')
                        executable = info.get('manifest', {}).get('launch_exe', '')

                        if install_path and executable:
                            # Strip leading slash - legendary returns paths like '/Binaries/Win64/Game.exe'
                            # which causes os.path.join to treat it as absolute, ignoring install_path
                            executable = executable.lstrip('/')
                            exe_path = os.path.join(install_path, executable)
                            logger.info(f"[Epic] Successfully installed {game_id} to {install_path}")
                            logger.info(f"[Epic] Executable: {exe_path}")
                            
                            # FIX 3: Validate executable exists before returning success
                            if not os.path.isfile(exe_path):
                                logger.warning(f"[Epic] Manifest exe not found: {exe_path}, trying fallback")
                                fallback_exe = self._find_executable_fallback(install_path)
                                if fallback_exe and os.path.isfile(fallback_exe):
                                    exe_path = fallback_exe
                                    executable = os.path.relpath(exe_path, install_path)
                                    logger.info(f"[Epic] Using fallback exe: {exe_path}")
                                else:
                                    logger.error(f"[Epic] No valid executable found for {game_id}")
                            
                            # Write manifest for recovery after plugin reinstall
                            try:
                                from ..discovery.startup import write_game_manifest
                                game_title = info.get('game', {}).get('title', game_id)
                                write_game_manifest(
                                    install_path=install_path,
                                    store="epic",
                                    game_id=game_id,
                                    title=game_title,
                                    executable_relative=executable,  # Already stripped of leading slash
                                    platform="windows"
                                )
                            except Exception as e:
                                logger.warning(f"[Epic] Failed to write manifest: {e}")
                            
                            return {
                                'success': True,
                                'install_path': install_path,
                                'exe_path': exe_path,
                                'message': f'Successfully installed {game_id}'
                            }
                        elif install_path:
                            # Have install path but no executable info - try fallback scan
                            logger.warning(f"[Epic] Manifest missing launch_exe for {game_id}, scanning for executable...")
                            exe_path = self._find_executable_fallback(install_path)
                            if exe_path:
                                logger.info(f"[Epic] Found executable via fallback scan: {exe_path}")
                                
                                # Write manifest for recovery
                                try:
                                    from ..discovery.startup import write_game_manifest
                                    game_title = info.get('game', {}).get('title', game_id)
                                    exe_relative = os.path.relpath(exe_path, install_path)
                                    write_game_manifest(
                                        install_path=install_path,
                                        store="epic",
                                        game_id=game_id,
                                        title=game_title,
                                        executable_relative=exe_relative,
                                        platform="windows"
                                    )
                                except Exception as e:
                                    logger.warning(f"[Epic] Failed to write manifest: {e}")
                                
                                return {
                                    'success': True,
                                    'install_path': install_path,
                                    'exe_path': exe_path,
                                    'message': f'Successfully installed {game_id}'
                                }
                            else:
                                logger.warning(f"[Epic] Could not determine executable for {game_id}")
                                return {
                                    'success': True,
                                    'install_path': install_path,
                                    'message': f'Successfully installed {game_id} (executable unknown)'
                                }
                    except Exception as e:
                        logger.error(f"[Epic] Error parsing legendary info: {e}")

                # Fallback: try the assumed path
                logger.warning(f"[Epic] Could not get install path from legendary, using fallback")
                install_path = os.path.join(base_path, game_id)
                return {
                    'success': True,
                    'install_path': install_path,
                    'message': f'Successfully installed {game_id} (path uncertain)'
                }
            else:
                logger.error(f"[Epic] Installation failed for {game_id}")
                return {
                    'success': False,
                    'error': 'Installation failed - check logs for details'
                }

        except Exception as e:
            logger.error(f"Error installing game {game_id}: {e}")
            return {
                'success': False,
                'error': str(e)
            }

    async def check_for_updates(self) -> List[str]:
        """Check which installed Epic games have updates available.
        
        Uses `legendary list-installed --check-updates` which outputs update status in plaintext.
        (Note: the --json flag drops the update_available field due to a legendary bug).
        
        Returns:
            List of app_name IDs that have updates available.
        """
        if not self.legendary_bin:
            return []

        try:
            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'list-installed', '--check-updates',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()

            if proc.returncode != 0:
                logger.error(f"[EPIC] check_for_updates failed: {stderr.decode('utf-8', errors='replace')}")
                return []

            output = stdout.decode('utf-8', errors='replace')
            updates = []
            current_app = None
            
            # Parse legendary plaintext output
            # Example: 
            # * Bloons TD 6 (App name: 7786b355a13b47a6b3915335117cd0b2 | Version: 53.0.10320...)
            #  -> Update available! Installed: 53.0.10320, Latest: 53.2.10346
            for line in output.splitlines():
                line = line.strip()
                if line.startswith('*') and 'App name:' in line:
                    try:
                        current_app = line.split('App name:')[1].split('|')[0].strip()
                    except IndexError:
                        current_app = None
                elif line.startswith('-> Update available!') and current_app:
                    updates.append(current_app)
                    logger.info(f"[EPIC] Update available: {current_app}")
                    current_app = None

            logger.info(f"[EPIC] Found {len(updates)} games with updates")
            return updates

        except Exception as e:
            logger.error(f"[EPIC] Error checking for updates: {e}")
            return []

    async def update_game(self, game_id: str, install_path: Optional[str] = None) -> Dict[str, Any]:
        """Update an installed Epic game using legendary.
        
        Args:
            game_id: Epic game app_name (ID).
            install_path: Not used for Epic (legendary tracks install paths).
            
        Returns:
            Dict with 'success' and optionally 'error'.
        """
        if not self.legendary_bin:
            return {'success': False, 'error': 'Legendary CLI not found'}

        try:
            logger.info(f"[EPIC] Starting update for {game_id}")

            proc = await asyncio.create_subprocess_exec(
                self.legendary_bin, 'update', game_id, '--with-dlcs', '--yes',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT
            )

            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                if line_str:
                    logger.info(f"[EPIC Update] {line_str}")

            await proc.wait()

            if proc.returncode == 0:
                # Invalidate installed cache so next query reflects the update
                global _legendary_installed_cache
                _legendary_installed_cache['data'] = None
                _legendary_installed_cache['timestamp'] = 0

                logger.info(f"[EPIC] Successfully updated {game_id}")
                return {'success': True, 'message': f'Successfully updated {game_id}'}
            else:
                logger.error(f"[EPIC] Update failed for {game_id}")
                return {'success': False, 'error': 'Update failed - check logs'}

        except Exception as e:
            logger.error(f"[EPIC] Error updating {game_id}: {e}")
            return {'success': False, 'error': str(e)}
