"""
GOG Store connector using direct OAuth API and gogdl binary.

This module handles all GOG.com operations including authentication,
library fetching, and game installation via gogdl binary.
"""
import asyncio
import glob
import io
import json
import locale
import logging
import os
import re
import shutil
import ssl
import time
import subprocess
from typing import Dict, Any, List, Optional, Tuple

try:
    import aiohttp
except ImportError:
    aiohttp = None

from .base import Store, Game
from ..auth.browser import CDPOAuthMonitor

logger = logging.getLogger(__name__)

# ── Auth shortcut constants ───────────────────────────────────────────
DATA_DIR = os.path.join(os.path.expanduser("~"), ".local", "share", "unifideck")
GOG_AUTH_SHORTCUT_STORE_ID = "gog:gog-auth"
GOG_AUTH_SHORTCUT_LAUNCH_WAIT_MS = 2000


class GOGAPIClient:
    """Handles GOG via direct API calls using OAuth and gogdl binary"""

    # OAuth constants
    BASE_URL = "https://embed.gog.com"
    AUTH_URL = "https://auth.gog.com"
    CLIENT_ID = "46899977096215655"
    CLIENT_SECRET = "9d85c43b1482497dbbce61f6e4aa173a433796eeae2ca8c5f6129f2dc4de46d9"
    REDIRECT_URI = "https://embed.gog.com/on_login_success?origin=client"  # GOG's registered redirect URI

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        self.plugin_dir = plugin_dir  # Plugin root directory for finding bundled binaries
        self.plugin_instance = plugin_instance  # Reference to parent Plugin for auto-sync
        self.token_file = os.path.expanduser("~/.config/unifideck/gog_token.json")
        
        # GOGDL configuration directory - completely separate from Heroic
        # This is where gogdl stores manifests, auth, and support files
        self.gogdl_config_dir = os.path.expanduser("~/.config/unifideck/gogdl")
        self.gogdl_config_path = os.path.join(self.gogdl_config_dir, "auth.json")
        self.download_dir = os.path.expanduser("~/GOG Games")
        
        # Locate gogdl binary
        self.gogdl_bin = None
        if self.plugin_dir:
            self.gogdl_bin = os.path.join(self.plugin_dir, 'bin', 'gogdl')
        
        if not self.gogdl_bin or not os.path.exists(self.gogdl_bin):
            logger.warning(f"[GOG] gogdl binary NOT found at {self.gogdl_bin}. Installation will fail.")
        else:
            logger.info(f"[GOG] Found gogdl binary at {self.gogdl_bin}")

        self.access_token = None
        self.refresh_token = None
        self._load_tokens()
        
        # Cache for supported GOG languages (subset of what GOG API supports)
        self._gog_supported_languages = ['en', 'de', 'fr', 'pl', 'ru', 'pt', 'es', 'it', 'zh', 'ko', 'ja']
        
        logger.info("GOG API client initialized")
    
    def _get_unifideck_language(self) -> str:
        """Get language code for GOG API from Unifideck settings.

        Delegates to the shared locale utility so GOG and all other store connectors
        use a single source of truth (settings.json -> system locale -> 'en-US').
        """
        from ..utils.locale import get_unifideck_locale
        lang = get_unifideck_locale()
        logger.debug(f"[GOG] Language preference: {lang}")
        return lang    
    def _get_token_age(self) -> float:
        """Return age of token in seconds based on file modification time.
        
        Based on Lutris pattern: proactive token refresh before expiry.
        """
        if os.path.exists(self.token_file):
            try:
                return time.time() - os.path.getmtime(self.token_file)
            except OSError:
                pass
        return float('inf')
    
    async def _ensure_fresh_token(self) -> bool:
        """Proactively refresh token if older than 43 minutes (before 1hr expiry).
        
        This prevents token expiry mid-operation, inspired by Lutris pattern.
        """
        token_age = self._get_token_age()
        if token_age > 2600:  # ~43 minutes
            logger.info(f"[GOG] Token is old ({token_age:.0f}s), refreshing proactively...")
            return await self._refresh_access_token()
        return True

    def _load_tokens(self):
        """Load stored OAuth tokens"""
        try:
            if os.path.exists(self.token_file):
                with open(self.token_file, 'r') as f:
                    data = json.load(f)
                    self.access_token = data.get('access_token')
                    self.refresh_token = data.get('refresh_token')
                    logger.info("Loaded GOG tokens from file")
        except Exception as e:
            logger.error(f"Error loading GOG tokens: {e}")

    def _save_tokens(self, access_token: str, refresh_token: str):
        """Save OAuth tokens to file"""
        try:
            os.makedirs(os.path.dirname(self.token_file), exist_ok=True)
            
            # Load existing user info to preserve if API fails
            existing_username = ""
            existing_user_id = ""
            try:
                if os.path.exists(self.token_file):
                    with open(self.token_file, 'r') as f:
                        existing = json.load(f)
                        existing_username = existing.get('username', '')
                        existing_user_id = existing.get('user_id', '')
            except Exception:
                pass
            
            # Fetch user info from GOG API for Comet integration
            username = existing_username
            user_id = existing_user_id
            try:
                import urllib.request
                import ssl as ssl_module
                # SSL context for Steam Deck compatibility
                ctx = ssl_module.create_default_context()
                ctx.check_hostname = False
                ctx.verify_mode = ssl_module.CERT_NONE
                
                req = urllib.request.Request(
                    "https://embed.gog.com/userData.json",
                    headers={
                        "Authorization": f"Bearer {access_token}",
                        "User-Agent": "Unifideck/1.0"
                    }
                )
                with urllib.request.urlopen(req, timeout=10, context=ctx) as response:
                    user_data = json.loads(response.read().decode())
                    username = user_data.get("username", "") or existing_username
                    user_id = user_data.get("galaxyUserId", "") or existing_user_id
                    logger.info(f"Fetched GOG user info: {username}")
            except Exception as e:
                logger.warning(f"Could not fetch GOG user info: {e} - preserving existing: {existing_username}")
            
            with open(self.token_file, 'w') as f:
                json.dump({
                    'access_token': access_token,
                    'refresh_token': refresh_token,
                    'username': username,
                    'user_id': user_id
                }, f)
            self.access_token = access_token
            self.refresh_token = refresh_token
            logger.info("Saved GOG tokens to file")
            
            # Auto-sync to gogdl config format immediately
            self._ensure_auth_config()
            
        except Exception as e:
            logger.error(f"Error saving GOG tokens: {e}")
    
    def _ensure_auth_config(self) -> bool:
        """
        Convert Unifideck's gog_token.json format to gogdl's expected gog_credentials.json format.
        Called before any gogdl operation.
        
        Unifideck: {"access_token": "...", "refresh_token": "..."}
        gogdl: {"46899977096215655": {"access_token": "...", "refresh_token": "...", "token_type": "Bearer", "expires_in": 3600, ...}}
        """
        if not self.access_token:
            return False
            
        try:
            # Prepare gogdl format
            # Using standard GOG Client ID as key
            gogdl_data = {
                self.CLIENT_ID: {
                    "access_token": self.access_token,
                    "refresh_token": self.refresh_token,
                    "token_type": "Bearer",
                    "expires_in": 3600, # Dummy value, gogdl handles refresh if needed
                    "scope": "openid",
                    "created_at": time.time(),
                    "loginTime": time.time()  # Required by gogdl auth.py logic
                }
            }
            
            os.makedirs(os.path.dirname(self.gogdl_config_path), exist_ok=True)
            with open(self.gogdl_config_path, 'w') as f:
                json.dump(gogdl_data, f, indent=2)
            
            logger.debug("[GOG] Synced auth tokens to gogdl config")
            return True
        except Exception as e:
            logger.error(f"[GOG] Failed to sync auth tokens to gogdl config: {e}")
            return False

    def _get_gogdl_env(self) -> dict:
        """Get environment dict with GOGDL_CONFIG_PATH set.
        
        This ensures gogdl uses Unifideck's own configuration directory
        for manifests, support files, etc. - completely separate from Heroic.
        
        IMPORTANT: GOGDL_CONFIG_PATH must point to PARENT directory.
        gogdl creates 'heroic_gogdl/manifests/' inside this path.
        """
        env = os.environ.copy()
        # Point to parent dir - gogdl creates subdirectories inside
        env['GOGDL_CONFIG_PATH'] = os.path.dirname(self.gogdl_config_dir)
        # CRITICAL: Force unbuffered Python output in gogdl
        # Without this, gogdl (a Python script) buffers output when stdout is piped,
        # causing the asyncio output reading loop to hang/timeout and downloads to fail
        env['PYTHONUNBUFFERED'] = '1'
        return env

    async def is_available(self) -> bool:
        """Check if GOG is authenticated"""
        logger.info("[GOG] Checking availability")

        if not self.access_token:
            logger.info("[GOG] No access token found - not authenticated")
            return False

        logger.info(f"[GOG] Access token present (length: {len(self.access_token)})")

        try:
            # Try to get user data to verify token is valid
            import aiohttp
            import ssl

            # Create SSL context that doesn't verify certificates (needed on Steam Deck)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Add timeout
            timeout = aiohttp.ClientTimeout(total=5.0)
            # logger.info("[GOG] Requesting: GET https://embed.gog.com/userData.json")

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                async with session.get(
                    'https://embed.gog.com/userData.json',
                    headers={'Authorization': f'Bearer {self.access_token}'}
                ) as response:
                    # logger.info(f"[GOG] Response status: {response.status}")

                    if response.status == 200:
                        # data = await response.text()
                        logger.info("[GOG] Status: Connected (authenticated)")
                        return True
                    elif response.status == 401:
                        logger.warning("[GOG] Token expired (401), attempting refresh")
                        return await self._refresh_access_token()
                    else:
                        error_text = await response.text()
                        logger.warning(f"[GOG] Auth check failed (status: {response.status})")
                        return False

        except asyncio.TimeoutError:
            logger.error("[GOG] Status check timed out after 5 seconds")
            return False
        except Exception as e:
            logger.error(f"[GOG] Exception checking status: {e}", exc_info=True)
            return False

    async def _refresh_access_token(self) -> bool:
        """Refresh the access token using refresh token"""
        if not self.refresh_token:
            return False

        try:
            import aiohttp
            import ssl

            # Create SSL context that doesn't verify certificates (needed on Steam Deck)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f'https://auth.gog.com/token?client_id={self.CLIENT_ID}&client_secret={self.CLIENT_SECRET}&grant_type=refresh_token&refresh_token={self.refresh_token}'
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._save_tokens(data['access_token'], data['refresh_token'])
                        logger.info("Refreshed GOG access token")
                        return True
                    else:
                        logger.error(f"Failed to refresh GOG token: {response.status}")
                        return False
        except Exception as e:
            logger.error(f"Error refreshing GOG token: {e}")
            return False

    async def start_auth(self) -> Dict[str, Any]:
        """Start GOG OAuth flow via auth shortcut + CDP interception on port 9222."""
        auth_url = f"https://auth.gog.com/auth?client_id={self.CLIENT_ID}&redirect_uri={self.REDIRECT_URI}&response_type=code&layout=client2"

        # Check if compatible browser is available (reuse Microsoft's detection)
        try:
            ms = self.plugin_instance.microsoft
            if not ms._browser.is_installed:
                return {
                    'success': True,
                    'needs_chromium': True,
                    'message': 'microsoft.chromiumRequired',
                }
        except Exception:
            pass

        # Cancel stale auth monitor
        if hasattr(self, '_auth_monitor_task') and self._auth_monitor_task and not self._auth_monitor_task.done():
            self._auth_monitor_task.cancel()

        # Write auth URL for launcher to read
        url_file = os.path.join(DATA_DIR, "gog_auth_url.txt")
        os.makedirs(DATA_DIR, exist_ok=True)
        with open(url_file, "w") as f:
            f.write(auth_url)

        # Ensure auth shortcut exists in VDF
        shortcut_appid = await self._ensure_gog_auth_shortcut()

        # Start CDP monitor on port 9222
        self._auth_monitor_task = asyncio.create_task(self._monitor_and_complete_auth())

        return {
            'success': True,
            'chromium_auth': True,
            'shortcut_launch': True,
            'message': 'gog.signInMessage',
        }

    async def _monitor_and_complete_auth(self):
        """Background task: intercept OAuth redirect via CDP on port 9222."""
        try:
            from ..auth.cdp_interceptor import intercept_oauth_code, close_cdp_auth_browser

            logger.info("[GOG] Auth monitor started — polling CDP port 9222")
            code = await intercept_oauth_code(store='gog', timeout=300, cdp_port=9222)

            if code:
                logger.info("[GOG] ✓ Received OAuth code via CDP interception")
                result = await self.complete_auth(code)
                if result['success']:
                    logger.info("[GOG] ✓ Authentication completed successfully — tokens saved")
                    try:
                        closed = await close_cdp_auth_browser(cdp_port=9222, store="gog")
                        if closed:
                            logger.info("[GOG] ✓ Closed auth browser after successful sign-in")
                        else:
                            logger.debug("[GOG] No auth browser targets to close")
                    except Exception as close_err:
                        logger.warning(f"[GOG] Could not close auth browser: {close_err}")
                    if self.plugin_instance:
                        logger.info("[GOG] Queueing automatic library sync...")
                        asyncio.create_task(
                            self.plugin_instance.request_auth_sync(source='auth:gog')
                        )
                else:
                    logger.error(f"[GOG] ✗ complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[GOG] ✗ CDP interception timed out — no code received")
        except Exception as e:
            logger.error(f"[GOG] ✗ Auth monitor error: {e}", exc_info=True)
        finally:
            url_file = os.path.join(DATA_DIR, "gog_auth_url.txt")
            try:
                os.remove(url_file)
            except OSError:
                pass

    async def complete_auth(self, auth_code: str) -> Dict[str, Any]:
        """Complete GOG OAuth flow with authorization code"""
        try:
            import aiohttp
            import ssl

            # Create SSL context that doesn't verify certificates (needed on Steam Deck)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            # Exchange authorization code for tokens
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                async with session.get(
                    f'https://auth.gog.com/token?client_id={self.CLIENT_ID}&client_secret={self.CLIENT_SECRET}&grant_type=authorization_code&code={auth_code}&redirect_uri={self.REDIRECT_URI}'
                ) as response:
                    if response.status == 200:
                        data = await response.json()
                        self._save_tokens(data['access_token'], data['refresh_token'])
                        logger.info("GOG authentication successful")
                        
                        # Sync auth to gogdl config
                        self._ensure_auth_config()
                        
                        return {'success': True, 'message': 'Successfully authenticated with GOG'}
                    else:
                        error_text = await response.text()
                        logger.error(f"GOG auth failed: {response.status} - {error_text}")
                        return {'success': False, 'error': f'Authentication failed: {error_text}'}

        except Exception as e:
            logger.error(f"Error completing GOG auth: {e}")
            return {'success': False, 'error': str(e)}

    async def _ensure_gog_auth_shortcut(self) -> Optional[int]:
        """Create or repair the persistent VDF shortcut for GOG OAuth."""
        if not self.plugin_instance or not hasattr(self.plugin_instance, 'shortcuts_manager'):
            logger.error("[GOG] No shortcuts_manager available")
            return None

        try:
            from py_modules.unifideck.shortcuts.shortcuts_manager import (
                load_shortcuts_registry, register_shortcut
            )
            from py_modules.unifideck.shortcuts.launch_options import get_full_id

            sm = self.plugin_instance.shortcuts_manager
            launcher_path = os.path.join(self.plugin_dir or "", "bin", "unifideck-launcher")
            if not os.path.isfile(launcher_path):
                logger.error(f"[GOG] Launcher not found at {launcher_path}")
                return None

            expected_appid = sm.generate_app_id("GOG Sign-In", launcher_path)
            unsigned_id = expected_appid if expected_appid >= 0 else expected_appid + 2**32

            expected_launch_options = (
                f"{GOG_AUTH_SHORTCUT_STORE_ID} "
                "UNIFIDECK_GOG_ACTION=auth"
            )

            shortcuts_data = await sm.read_shortcuts()
            shortcuts = shortcuts_data.get('shortcuts', {})

            matching_indices = [
                idx for idx, s in shortcuts.items()
                if get_full_id(s.get('LaunchOptions', '')) == GOG_AUTH_SHORTCUT_STORE_ID
            ]

            correct_idx = None
            for idx in matching_indices:
                sc = shortcuts[idx]
                if (sc.get('appid') == expected_appid
                        and sc.get('AppName') == 'GOG Sign-In'
                        and 'UNIFIDECK_GOG_ACTION=auth' in sc.get('LaunchOptions', '')):
                    correct_idx = idx
                    break

            vdf_dirty = False
            for idx in matching_indices:
                if idx != correct_idx:
                    logger.warning(f"[GOG] Removing malformed auth VDF entry idx={idx}")
                    del shortcuts[idx]
                    vdf_dirty = True

            if correct_idx is None:
                existing_indices = [int(k) for k in shortcuts.keys() if k.isdigit()]
                next_idx = max(existing_indices, default=-1) + 1
                shortcuts[str(next_idx)] = {
                    'appid': expected_appid,
                    'AppName': 'GOG Sign-In',
                    'exe': f'"{launcher_path}"',
                    'StartDir': f'"{os.path.dirname(launcher_path)}"',
                    'LaunchOptions': expected_launch_options,
                    'IsHidden': 1,
                    'AllowDesktopConfig': 1,
                    'OpenVR': 0,
                    'tags': {'0': 'GOG'},
                }
                vdf_dirty = True
                logger.info(f"[GOG] Created auth shortcut in VDF: appid={expected_appid} unsigned={unsigned_id}")

            if vdf_dirty:
                await sm.write_shortcuts(shortcuts_data)

            register_shortcut(GOG_AUTH_SHORTCUT_STORE_ID, expected_appid, "GOG Sign-In")
            await sm._clear_proton_compatibility(expected_appid)
            await self._fetch_auth_shortcut_artwork(unsigned_id, force=(vdf_dirty and correct_idx is None))

            return unsigned_id

        except Exception as e:
            logger.error(f"[GOG] Failed to create auth shortcut: {e}", exc_info=True)
            return None

    async def get_gog_auth_shortcut_context(self) -> Dict[str, Any]:
        """Return the auth shortcut appid so the frontend can call RunGame()."""
        unsigned_id = await self._ensure_gog_auth_shortcut()
        launcher_path = os.path.join(self.plugin_dir or "", "bin", "unifideck-launcher")
        launch_options = f"{GOG_AUTH_SHORTCUT_STORE_ID} UNIFIDECK_GOG_ACTION=auth"

        if not unsigned_id:
            logger.error("[GOG] Auth shortcut creation/validation failed")
            return {"success": False, "error": "Auth shortcut not ready"}

        logger.info(f"[GOG] Auth shortcut context: appid={unsigned_id}")
        return {
            "success": True,
            "appid_unsigned": unsigned_id,
            "launch_wait_ms": GOG_AUTH_SHORTCUT_LAUNCH_WAIT_MS,
            "launcher_path": launcher_path,
            "launch_options": launch_options,
        }

    async def _fetch_auth_shortcut_artwork(self, unsigned_id: int, force: bool = False) -> None:
        """Download SteamGridDB artwork for the GOG auth shortcut."""
        try:
            plugin = self.plugin_instance
            if not plugin or not hasattr(plugin, 'steamgriddb') or not plugin.steamgriddb:
                logger.debug("[GOG] SteamGridDB client not available, skipping artwork")
                return

            if not force:
                if hasattr(plugin, 'has_artwork') and await plugin.has_artwork(unsigned_id):
                    logger.debug("[GOG] Auth shortcut artwork already exists")
                    return

            only_types = None
            if not force and hasattr(plugin, 'get_missing_artwork_types'):
                missing = await plugin.get_missing_artwork_types(unsigned_id)
                if missing:
                    only_types = missing
                    logger.info(f"[GOG] Auth shortcut artwork gap-fill: {missing}")

            logger.info(f"[GOG] Fetching SteamGridDB artwork for GOG Galaxy (force={force})")
            await plugin.steamgriddb.fetch_game_art(
                title="GOG Galaxy",
                app_id=unsigned_id,
                only_types=only_types,
            )
        except Exception as e:
            logger.warning(f"[GOG] Auth shortcut artwork fetch failed: {e}")

    async def logout(self) -> Dict[str, Any]:
        """Logout from GOG"""
        try:
            # Remove token file
            if os.path.exists(self.token_file):
                os.remove(self.token_file)
                logger.info(f"Removed GOG token file: {self.token_file}")

            # Also remove gogdl config if it exists
            if os.path.exists(self.gogdl_config_path):
                os.remove(self.gogdl_config_path)

            # Clear in-memory tokens
            self.access_token = None
            self.refresh_token = None

            logger.info("Logged out from GOG")

            # Clear browser cookies for GOG
            monitor = CDPOAuthMonitor()
            await monitor.clear_cookies_for_domain('gog.com')

            return {'success': True, 'message': 'Logged out from GOG'}

        except Exception as e:
            logger.error(f"Error logging out from GOG: {e}")
            return {'success': False, 'error': str(e)}

    async def get_game_slug(self, game_id: str) -> Optional[str]:
        """Fetch game slug from GOG API for store URL generation.

        The GOG products endpoint returns a 'slug' field that can be used
        to construct the store page URL: https://www.gog.com/en/game/{slug}

        Args:
            game_id: GOG game ID (numeric string)

        Returns:
            The game slug (e.g., 'the_witcher_3_wild_hunt'), or None if unavailable
        """
        await self._ensure_fresh_token()

        if not self.access_token:
            return None

        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                from ..utils.locale import get_unifideck_locale
                url = f'https://api.gog.com/products/{game_id}?locale={get_unifideck_locale()}'
                headers = {'Authorization': f'Bearer {self.access_token}'}

                async with session.get(url, headers=headers, timeout=aiohttp.ClientTimeout(total=10)) as response:
                    if response.status == 200:
                        data = await response.json()
                        slug = data.get('slug')
                        if slug:
                            logger.debug(f"[GOG] Got slug for {game_id}: {slug}")
                            return slug

                        # Alternative: extract from links.product_card if available
                        links = data.get('links', {})
                        product_card = links.get('product_card', '')
                        if product_card and '/game/' in product_card:
                            extracted_slug = product_card.split('/game/')[-1].rstrip('/')
                            logger.debug(f"[GOG] Extracted slug from product_card for {game_id}: {extracted_slug}")
                            return extracted_slug

            return None

        except Exception as e:
            logger.warning(f"[GOG] Could not fetch slug for {game_id}: {e}")
            return None

    async def get_library(self) -> List[Game]:
        """Get GOG library via API with pagination support"""
        if not self.access_token:
            logger.warning("GOG not authenticated")
            return []

        try:
            import aiohttp
            import ssl

            # Create SSL context that doesn't verify certificates (needed on Steam Deck)
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                games = []
                current_page = 1
                total_pages = 1  # Will be updated from first response

                while current_page <= total_pages:
                    # Get owned games with pagination
                    url = f'https://embed.gog.com/account/getFilteredProducts?mediaType=1&page={current_page}'
                    logger.info(f"[GOG] Fetching library page {current_page}...")
                    
                    async with session.get(
                        url,
                        headers={'Authorization': f'Bearer {self.access_token}'}
                    ) as response:
                        if response.status != 200:
                            logger.error(f"Failed to get GOG library page {current_page}: {response.status}")
                            break

                        data = await response.json()
                        
                        # Update total pages from response
                        total_pages = data.get('totalPages', 1)
                        total_results = data.get('totalGamesFound', 0)
                        
                        if current_page == 1:
                            logger.info(f"[GOG] Total games in library: {total_results}, total pages: {total_pages}")

                        for product in data.get('products', []):
                            game = Game(
                                id=str(product.get('id', '')),
                                title=product.get('title', ''),
                                store='gog',
                                is_installed=False
                            )
                            games.append(game)
                        
                        logger.info(f"[GOG] Fetched page {current_page}/{total_pages}: {len(data.get('products', []))} games (total so far: {len(games)})")
                        current_page += 1

                logger.info(f"Found {len(games)} GOG games (complete library)")
                return games

        except Exception as e:
            logger.error(f"Error fetching GOG library: {e}")
            return None

    async def get_installed(self) -> List[str]:
        """
        Get installed GOG games by checking download directory
        """
        if not os.path.exists(self.download_dir):
            return []

        try:
            installed_ids = []
            for item in os.listdir(self.download_dir):
                item_path = os.path.join(self.download_dir, item)
                if os.path.isdir(item_path):
                    # Check if directory contains game files
                    if self._is_gog_game_installed(item_path):
                        # Try to extract game ID from goggame-*.info file
                        game_id = self._get_game_id_from_dir(item_path)
                        if game_id:
                            installed_ids.append(game_id)

            logger.info(f"Found {len(installed_ids)} installed GOG games")
            return installed_ids

        except Exception as e:
            logger.error(f"Error checking GOG installed games: {e}")
            return []

    def _is_gog_game_installed(self, game_dir: str) -> bool:
        """Check if a directory contains an installed GOG game.
        
        .unifideck-id marker = 100% installed status.
        This marker is written ONLY after fully successful installation.
        """
        return os.path.exists(os.path.join(game_dir, '.unifideck-id'))

    def _get_game_id_from_dir(self, game_dir: str) -> Optional[str]:
        """Extract game ID from .unifideck-id marker file.
        
        Handles both old format (plain game ID) and new format (JSON with game_id).
        Only returns ID if marker exists (= 100% installed).
        """
        marker_path = os.path.join(game_dir, '.unifideck-id')
        if os.path.exists(marker_path):
            try:
                with open(marker_path, 'r') as f:
                    content = f.read().strip()
                    
                # Try to parse as JSON first (new format)
                try:
                    data = json.loads(content)
                    if isinstance(data, dict):
                        return data.get('game_id') or data.get('gameId')
                    elif isinstance(data, (str, int)):
                        # Old format: just the ID as JSON string/int
                        return str(data)
                except json.JSONDecodeError:
                    # Old format: plain text game ID
                    return content if content else None
                    
            except Exception as e:
                logger.warning(f"[GOG] Error reading .unifideck-id: {e}")
        return None

    def migrate_old_markers(self) -> Dict[str, Any]:
        """Migrate old .unifideck-id files to new JSON format with goggame data.
        
        Called during Force Sync to upgrade old installations.
        """
        migrated = 0
        skipped = 0
        
        if not os.path.exists(self.download_dir):
            return {'migrated': 0, 'skipped': 0}
        
        try:
            for item in os.listdir(self.download_dir):
                item_path = os.path.join(self.download_dir, item)
                if not os.path.isdir(item_path):
                    continue
                    
                marker_path = os.path.join(item_path, '.unifideck-id')
                if not os.path.exists(marker_path):
                    continue
                    
                try:
                    with open(marker_path, 'r') as f:
                        content = f.read().strip()
                    
                    # Check if already in new format
                    try:
                        data = json.loads(content)
                        if isinstance(data, dict) and 'game_id' in data:
                            skipped += 1
                            continue
                    except json.JSONDecodeError:
                        pass
                    
                    # Get game ID from old format
                    old_game_id = None
                    try:
                        data = json.loads(content)
                        if isinstance(data, (str, int)):
                            old_game_id = str(data)
                    except json.JSONDecodeError:
                        old_game_id = content
                    
                    if not old_game_id:
                        skipped += 1
                        continue
                    
                    # Build new marker with goggame info
                    new_data = {"game_id": old_game_id}
                    for d in [item_path, os.path.join(item_path, 'game')]:
                        if not os.path.exists(d):
                            continue
                        for f in os.listdir(d):
                            if f.startswith('goggame-') and f.endswith('.info'):
                                try:
                                    with open(os.path.join(d, f), 'r') as info_f:
                                        new_data = json.load(info_f)
                                        new_data['game_id'] = old_game_id
                                except Exception:
                                    pass
                                break
                    
                    with open(marker_path, 'w') as f:
                        json.dump(new_data, f, indent=2)
                    migrated += 1
                    logger.info(f"[GOG] Migrated marker for {item}")
                except Exception as e:
                    logger.warning(f"[GOG] Failed to migrate {item}: {e}")
        except Exception as e:
            logger.error(f"[GOG] Error during marker migration: {e}")
        
        logger.info(f"[GOG] Migration: {migrated} upgraded, {skipped} current")
        return {'migrated': migrated, 'skipped': skipped}

    def get_installed_game_info(self, game_id: str) -> Optional[Dict[str, str]]:
        """Get install path and executable for an installed GOG game.
        
        Checks both .unifideck-id marker and goggame-*.info files for robustness.
        """
        if not os.path.exists(self.download_dir):
            return None
            
        try:
            for item in os.listdir(self.download_dir):
                item_path = os.path.join(self.download_dir, item)
                if not os.path.isdir(item_path):
                    continue
                    
                # Method 1: Check .unifideck-id marker
                found_id = self._get_game_id_from_dir(item_path)
                if found_id == game_id:
                    exe_path = self._find_game_executable(item_path)
                    return {'install_path': item_path, 'executable': exe_path}
                
                # Method 2: Fallback to goggame-*.info files (for old/external installs)
                if found_id is None:
                    for search_dir in [item_path, os.path.join(item_path, 'game')]:
                        if not os.path.exists(search_dir):
                            continue
                        for f in os.listdir(search_dir):
                            if f.startswith('goggame-') and f.endswith('.info'):
                                info_id = f.replace('goggame-', '').replace('.info', '')
                                if info_id == game_id:
                                    exe_path = self._find_game_executable(item_path)
                                    logger.info(f"[GOG] Found {game_id} via goggame info fallback at {item_path}")
                                    return {'install_path': item_path, 'executable': exe_path}
        except Exception as e:
            logger.error(f"[GOG] Error getting installed game info for {game_id}: {e}")
        return None

    async def get_game_size(self, game_id: str, session=None) -> Optional[int]:
        """Get game download size using GOG API directly.
        
        Uses the GOG products API which returns installer sizes for ALL games,
        including legacy games that don't support the content system API.
        
        Args:
            game_id: GOG product ID
            session: Optional aiohttp session for connection reuse
        
        Returns:
            Total size in bytes (installers + bonus content), or None if unavailable
        """
        await self._ensure_fresh_token()
        
        if not self.access_token:
            logger.warning(f"[GOG] No access token for size fetch")
            return None
        
        try:
            import ssl
            import aiohttp
            
            # Use provided session or create new one
            owns_session = session is None
            if owns_session:
                ssl_context = ssl.create_default_context()
                ssl_context.check_hostname = False
                ssl_context.verify_mode = ssl.CERT_NONE
                connector = aiohttp.TCPConnector(ssl=ssl_context)
                session = aiohttp.ClientSession(connector=connector)
            
            try:
                # Query GOG API for product details with downloads expanded
                from ..utils.locale import get_unifideck_locale
                url = f'https://api.gog.com/products/{game_id}?expand=downloads&locale={get_unifideck_locale()}'
                headers = {'Authorization': f'Bearer {self.access_token}'}
                
                async with session.get(url, headers=headers) as response:
                    if response.status != 200:
                        logger.warning(f"[GOG] API returned {response.status} for game {game_id}")
                        return None
                    
                    data = await response.json()
                    
                    # Calculate total size from all installers
                    total_size = 0
                    downloads = data.get('downloads', {})
                    
                    # Get installers - prioritize Linux, then Windows
                    installers = downloads.get('installers', [])
                    
                    # Find the best installer (prefer linux, then windows)
                    linux_installers = [i for i in installers if i.get('os') == 'linux']
                    windows_installers = [i for i in installers if i.get('os') == 'windows']
                    
                    # Use linux if available, else windows
                    target_installers = linux_installers if linux_installers else windows_installers
                    
                    if target_installers:
                        # Use only the first installer (typically English) to avoid 
                        # counting multiple language packs - user only downloads one
                        first_installer = target_installers[0]
                        for file_info in first_installer.get('files', []):
                            file_size = file_info.get('size', 0)
                            if isinstance(file_size, str):
                                # Convert string like "1.2 GB" to bytes
                                file_size = self._parse_size_string(file_size)
                            total_size += file_size
                    
                    if total_size > 0:
                        logger.debug(f"[GOG] Game {game_id} size from API: {total_size} bytes ({total_size/1024/1024:.1f} MB)")
                        return total_size
                    else:
                        logger.debug(f"[GOG] No installer size found in API for {game_id}")
                        return None
                    
            finally:
                if owns_session:
                    await session.close()
                    
        except Exception as e:
            logger.error(f"[GOG] Error getting size for {game_id}: {e}")
            return None
    
    def _parse_size_string(self, size_str: str) -> int:
        """Parse size string like '1.2 GB' or '500 MB' to bytes."""
        try:
            parts = size_str.strip().split()
            if len(parts) != 2:
                return 0
            value = float(parts[0])
            unit = parts[1].upper()
            
            if unit == 'GB':
                return int(value * 1024 * 1024 * 1024)
            elif unit == 'MB':
                return int(value * 1024 * 1024)
            elif unit == 'KB':
                return int(value * 1024)
            else:
                return int(value)
        except:
            return 0

    def _smart_match_language(self, target: str, choices: list[str]) -> str | None:
        """Finds the best match for a target language in a list of choices.
        
        Handles:
        1. Exact match ('en-US' == 'en-US')
        2. Prefix match ('en-US' matches 'en')
        3. Reverse prefix match ('en' matches 'en-US')
        """
        if not target or not choices:
            return None
            
        # 1. Exact match
        if target in choices:
            return target
            
        # 2. Base language match
        target_base = target.split('-')[0].lower()
        
        for choice in choices:
            choice_base = choice.split('-')[0].lower()
            if target_base == choice_base:
                return choice
                
        return None

    async def _determine_install_mode(self, game_id: str, target_folder: str | None, platform: str) -> str:
        """Determine whether to use 'download' or 'repair' based on folder state.
        
        This prevents gogdl from deleting existing game data (which happens when
        download command sees files it doesn't expect from its manifest).
        
        Args:
            game_id: GOG game ID
            target_folder: Expected game folder path (may not exist yet)
            platform: 'windows' or 'linux'
            
        Returns:
            'download' for fresh install, 'repair' for existing installation
        """
        # Check 1: Does folder exist?
        if not target_folder or not os.path.exists(target_folder):
            logger.info(f"[GOG] Mode selection: folder doesn't exist - using 'download'")
            return 'download'
        
        # Check 2: Folder size - significant data present?
        folder_size = self._get_folder_size(target_folder)
        has_significant_data = folder_size > 100_000_000  # > 100MB
        
        # Check 3: Actual file count
        actual_files = self._count_files_in_folder(target_folder)
        
        # Check 4: Has goggame info file?
        has_goggame_info = any(
            f.startswith(f'goggame-{game_id}') and f.endswith('.info')
            for f in os.listdir(target_folder) if os.path.isfile(os.path.join(target_folder, f))
        )
        
        logger.info(f"[GOG] Mode selection: folder_size={folder_size/1024/1024:.1f}MB, "
                    f"files={actual_files}, has_info={has_goggame_info}")
        
        # Decision logic (order matters!):
        # 1. If has goggame.info BUT nearly empty (<100MB): CORRUPT install - clean up and download
        # 2. If has goggame.info AND substantial data (>100MB): use repair
        # 3. If no goggame.info but has significant data: use repair (safer)
        # 4. Otherwise: use download
        
        if has_goggame_info:
            if folder_size < 100_000_000:  # Less than 100MB with manifest = corrupt
                logger.warning(f"[GOG] Mode selection: has goggame.info but only {folder_size/1024/1024:.1f}MB - corrupt install detected")
                logger.info(f"[GOG] Cleaning up corrupt install at {target_folder}")
                try:
                    shutil.rmtree(target_folder)
                    logger.info(f"[GOG] Deleted corrupt install folder")
                except Exception as e:
                    logger.error(f"[GOG] Failed to clean corrupt install: {e}")
                
                # CRITICAL: Also delete gogdl's cached manifest in support_dir
                # Otherwise gogdl will still think game is installed and say "Nothing to do"
                support_dir = os.path.join(self.gogdl_config_dir, "gog-support", game_id)
                if os.path.exists(support_dir):
                    try:
                        shutil.rmtree(support_dir)
                        logger.info(f"[GOG] Deleted cached manifest at {support_dir}")
                    except Exception as e:
                        logger.warning(f"[GOG] Could not delete support dir: {e}")
                
                return 'download'
            else:
                logger.info(f"[GOG] Mode selection: found goggame.info with {folder_size/1024/1024:.0f}MB - using 'repair'")
                return 'repair'
        
        # No goggame.info = incomplete/corrupt install, cannot reliably repair.
        # Clean up everything and use fresh download.
        if has_significant_data or actual_files > 0:
            logger.warning(f"[GOG] Mode selection: no goggame.info with {folder_size/1024/1024:.1f}MB data - cleaning up orphaned install")
            try:
                shutil.rmtree(target_folder)
                logger.info(f"[GOG] Deleted orphaned game folder")
            except Exception as e:
                logger.error(f"[GOG] Failed to clean orphaned folder: {e}")
            
            # Also clean up stale manifests from both old and new locations
            manifest_locations = [
                os.path.join(self.gogdl_config_dir, "heroic_gogdl", "manifests", game_id),
                os.path.join(os.path.dirname(self.gogdl_config_dir), "heroic_gogdl", "manifests", game_id),
                os.path.join(self.gogdl_config_dir, "manifests", game_id),
                os.path.join(os.path.dirname(self.gogdl_config_dir), "gogdl", "manifests", game_id),
            ]
            for manifest_path in manifest_locations:
                if os.path.exists(manifest_path):
                    try:
                        os.remove(manifest_path)
                        logger.info(f"[GOG] Cleaned stale manifest: {manifest_path}")
                    except Exception as e:
                        logger.warning(f"[GOG] Could not clean manifest: {e}")
        
        logger.info(f"[GOG] Mode selection: using 'download' mode")
        return 'download'

    async def _verify_installation(self, game_id: str, install_path: str, platform: str) -> Dict[str, Any]:
        """Verify installation completeness after download/repair.
        
        Performs a sweep to check:
        - Folder size vs expected size
        - Required files present (goggame.info, executable)
        
        Args:
            game_id: GOG game ID
            install_path: Path where game was installed
            platform: 'windows' or 'linux'
            
        Returns:
            Dict with 'complete' bool and verification details
        """
        try:
            # Get expected disk size from gogdl info
            expected_size = await self._get_expected_disk_size(game_id, platform)
            
            # Get actual state
            actual_size = self._get_folder_size(install_path)
            actual_files = self._count_files_in_folder(install_path)
            
            # Check for goggame.info (required)
            has_info = False
            try:
                for f in os.listdir(install_path):
                    if f.startswith('goggame-') and f.endswith('.info'):
                        has_info = True
                        break
            except Exception:
                pass
            
            # Check for executable
            exe_result = self._find_game_executable_with_workdir(install_path)
            has_exe = exe_result is not None
            
            # Calculate completeness
            size_ratio = actual_size / expected_size if expected_size > 0 else 1.0
            
            logger.info(f"[GOG] Verification: size={actual_size/1024/1024:.1f}MB ({size_ratio*100:.1f}% of expected), "
                        f"files={actual_files}, has_info={has_info}, has_exe={has_exe}")
            
            # Determine result
            if expected_size > 0 and size_ratio < 0.8:  # Less than 80% of expected size
                return {
                    'complete': False,
                    'issue': f'Installation may be incomplete: only {size_ratio*100:.0f}% of expected size',
                    'actual_size': actual_size,
                    'expected_size': expected_size,
                    'has_info': has_info,
                    'has_exe': has_exe
                }
            
            if not has_info:
                return {
                    'complete': False,
                    'issue': 'Missing goggame.info file',
                    'actual_size': actual_size,
                    'actual_files': actual_files,
                    'has_exe': has_exe
                }
            
            if not has_exe:
                return {
                    'complete': False,
                    'issue': 'Could not find game executable',
                    'actual_size': actual_size,
                    'actual_files': actual_files,
                    'has_info': has_info
                }
            
            return {
                'complete': True,
                'actual_size': actual_size,
                'expected_size': expected_size,
                'actual_files': actual_files,
                'size_ratio': size_ratio,
                'has_info': has_info,
                'has_exe': has_exe
            }
            
        except Exception as e:
            logger.error(f"[GOG] Verification error: {e}")
            return {'complete': False, 'issue': f'Verification failed: {str(e)}'}

    async def _get_expected_disk_size(self, game_id: str, platform: str) -> int:
        """Get expected disk size from gogdl info."""
        try:
            cmd = [
                self.gogdl_bin, '--auth-config-path', self.gogdl_config_path,
                'info', '--platform', platform, game_id
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                env=self._get_gogdl_env()
            )
            stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            
            for line in stdout.decode().strip().split('\n'):
                try:
                    data = json.loads(line)
                    if 'size' in data:
                        # Try language-specific size first, then fallback
                        size_info = data['size']
                        for lang_key in ['en-US', 'en', '*']:
                            if lang_key in size_info:
                                return size_info[lang_key].get('disk_size', 0)
                        # If no matching language, use first available
                        if size_info:
                            first_lang = next(iter(size_info))
                            return size_info[first_lang].get('disk_size', 0)
                except json.JSONDecodeError:
                    continue
        except asyncio.TimeoutError:
            logger.warning(f"[GOG] Timeout getting expected disk size")
        except Exception as e:
            logger.warning(f"[GOG] Could not get expected disk size: {e}")
        
        return 0  # Unknown

    async def install_game(self, game_id: str, base_path: str = None, progress_callback=None, language: str = None) -> Dict[str, Any]:
        """Install GOG game using gogdl binary

        Args:
            game_id: GOG game ID
            base_path: Installation directory
            progress_callback: Callback for progress updates
            language: Language code to download (e.g., 'en-US', 'de-DE'). If None, uses system preference.
        """
        if not self.gogdl_bin:
            return {'success': False, 'error': 'gogdl binary not found'}

        # 1. Ensure Auth (refreshes token if needed)
        if not await self.is_available():
             return {'success': False, 'error': 'Not authenticated with GOG or token expired'}

        # Proactively refresh token if old (Lutris pattern)
        await self._ensure_fresh_token()

        # Force sync fresh token to gogdl config
        if not self._ensure_auth_config():
             return {'success': False, 'error': 'Failed to configure GOG authentication'}

        # Get preferred language - use explicit selection or fall back to system preference
        preferred_lang = language if language else self._get_unifideck_language()
        # Track if user explicitly selected this language via the modal
        # (vs. falling back to system preference)
        explicit_language_selection = language is not None
        logger.info(f"[GOG] Using language preference: {preferred_lang} (explicit={explicit_language_selection})")

        # 2. Determine Install Path
        if not base_path:
            base_path = os.path.expanduser("~/GOG Games")
        
        # We need game title for the folder but gogdl creates its own folder name
        # We'll pass base_path and let gogdl create the game directory inside it
        os.makedirs(base_path, exist_ok=True)
        
        logger.info(f"[GOG] Starting installation of {game_id} via gogdl to {base_path}")
        
        # CRITICAL: Always clear stale manifests before ANY install attempt
        # This handles cases where manifests exist from:
        # - Previous failed install attempts
        # - External sources (Heroic, etc.)
        # - Incomplete uninstalls
        # NOTE: gogdl creates manifests in BOTH 'heroic_gogdl/manifests/' AND 'gogdl/manifests/'
        manifest_locations = [
            os.path.join(self.gogdl_config_dir, "heroic_gogdl", "manifests", game_id),
            os.path.join(os.path.dirname(self.gogdl_config_dir), "heroic_gogdl", "manifests", game_id),
            # Also clean gogdl/ subdirectory (gogdl creates manifests in both locations)
            os.path.join(self.gogdl_config_dir, "manifests", game_id),
            os.path.join(os.path.dirname(self.gogdl_config_dir), "gogdl", "manifests", game_id),
        ]
        for manifest_path in manifest_locations:
            if os.path.exists(manifest_path):
                try:
                    os.remove(manifest_path)
                    logger.info(f"[GOG] Pre-install: cleared stale manifest: {manifest_path}")
                except Exception as e:
                    logger.warning(f"[GOG] Pre-install: could not clear manifest {manifest_path}: {e}")
        
        # CRITICAL: Also clear gog-support cache directory
        # This contains install state that makes gogdl think game is already installed
        support_dir = os.path.join(self.gogdl_config_dir, "gog-support", game_id)
        if os.path.exists(support_dir):
            try:
                shutil.rmtree(support_dir)
                logger.info(f"[GOG] Pre-install: cleared stale support cache: {support_dir}")
            except Exception as e:
                logger.warning(f"[GOG] Pre-install: could not clear support cache: {e}")

        # Re-running platform check properly
        platform = 'linux'
        folder_name = None
        
        info_cmd = [
            self.gogdl_bin, '--auth-config-path', self.gogdl_config_path,
            'info', '--platform', 'linux', game_id
        ]
        proc = await asyncio.create_subprocess_exec(*info_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._get_gogdl_env())
        stdout, stderr = await proc.communicate()
        
        if proc.returncode != 0:
            logger.info(f"[GOG] Linux version not found for {game_id}, trying Windows")
            platform = 'windows'
            info_cmd = [
                self.gogdl_bin, '--auth-config-path', self.gogdl_config_path,
                'info', '--platform', 'windows', game_id
            ]
            proc = await asyncio.create_subprocess_exec(*info_cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, env=self._get_gogdl_env())
            stdout, stderr = await proc.communicate()
            
        # 3. Parse Info Output (Folder Name + Supported Languages)
        supported_languages = []
        
        if proc.returncode == 0:
            try:
                # Find JSON in output
                output_lines = stdout.decode().strip().split('\n')
                for line in reversed(output_lines):
                    try:
                        data = json.loads(line)
                        # Extract folder name
                        if 'folder_name' in data and not folder_name:
                            folder_name = data['folder_name']
                            logger.info(f"[GOG] Predicted folder name: {folder_name}")
                        
                        # Extract supported languages
                        if 'languages' in data:
                            supported_languages = data['languages']
                            logger.info(f"[GOG] Found supported languages in manifest: {supported_languages}")
                            
                        if folder_name and supported_languages:
                            break
                    except json.JSONDecodeError:
                        continue
            except Exception as e:
                logger.warning(f"[GOG] Could not parse info: {e}")
        
        # 4. Start Download using 'repair' command
        # IMPORTANT: We use 'repair' instead of 'download' because gogdl V2 has a bug
        # where 'download' sees an empty manifest and reports "Nothing to do" even when
        # no files exist. The 'repair' command always verifies and downloads missing files.
        # Command: gogdl ... download/repair [id] --platform [plat] --path [path] --with-dlcs
        
        # IMPORTANT: Snapshot existing directories BEFORE gogdl runs
        # This prevents detecting games installed by Heroic or other launchers
        existing_dirs = set()
        try:
            if os.path.exists(base_path):
                existing_dirs = set(os.listdir(base_path))
        except Exception as e:
            logger.warning(f"[GOG] Could not snapshot existing dirs: {e}")
        
        # Create support directory for gogdl (stores metadata/cache)
        support_dir = os.path.join(self.gogdl_config_dir, "gog-support", game_id)
        os.makedirs(support_dir, exist_ok=True)
        
        # PHASE 2: Smart mode selection - choose download vs repair based on folder state
        # This prevents gogdl from deleting existing game data
        target_folder = os.path.join(base_path, folder_name) if folder_name else None
        install_mode = await self._determine_install_mode(game_id, target_folder, platform)
        
        # Determine the path to pass to gogdl based on mode
        if install_mode == 'download':
            # Fresh install: pass base_path, gogdl creates subfolder
            gogdl_path = base_path
            
            # CRITICAL: Clear stale manifest before fresh download
            # This prevents gogdl from finding old manifest and saying "Nothing to do"
            # Check BOTH old location (from buggy versions) and current location
            manifest_locations = [
                # Old buggy location: gogdl created heroic_gogdl/ inside gogdl_config_dir
                os.path.join(self.gogdl_config_dir, "heroic_gogdl", "manifests", game_id),
                # Current correct location: parent of gogdl_config_dir
                os.path.join(os.path.dirname(self.gogdl_config_dir), "heroic_gogdl", "manifests", game_id),
                # Also check gogdl/ subdirectory (gogdl creates manifests in both locations)
                os.path.join(self.gogdl_config_dir, "manifests", game_id),
                os.path.join(os.path.dirname(self.gogdl_config_dir), "gogdl", "manifests", game_id),
            ]
            for manifest_path in manifest_locations:
                if os.path.exists(manifest_path):
                    try:
                        os.remove(manifest_path)
                        logger.info(f"[GOG] Cleared stale manifest: {manifest_path}")
                    except Exception as e:
                        logger.warning(f"[GOG] Could not clear stale manifest {manifest_path}: {e}")
            
            # CRITICAL: Delete incomplete game folder if it exists
            # A folder without .unifideck-id marker is incomplete/corrupt and needs to be removed
            # Otherwise gogdl may skip downloading files that partially exist
            if target_folder and os.path.exists(target_folder):
                marker_path = os.path.join(target_folder, '.unifideck-id')
                if not os.path.exists(marker_path):
                    logger.info(f"[GOG] Found incomplete folder (no .unifideck-id): {target_folder}")
                    try:
                        shutil.rmtree(target_folder)
                        logger.info(f"[GOG] Deleted incomplete folder for fresh download")
                    except Exception as e:
                        logger.warning(f"[GOG] Could not delete incomplete folder: {e}")
        else:
            # Repair: pass the specific game folder
            gogdl_path = target_folder if target_folder and os.path.exists(target_folder) else base_path
        
        cmd = [
            self.gogdl_bin,
            '--auth-config-path', self.gogdl_config_path,
            install_mode,  # 'download' or 'repair' based on folder state
            game_id,
            '--platform', platform,
            '--path', gogdl_path,
            '--support', support_dir,
            '--with-dlcs',  # Automatically install all owned DLCs
        ]
        
        # STRATEGY:
        # - If user explicitly selected a language (via modal), download ONLY that language
        # - If no explicit selection, use system preference with English fallback
        languages_to_download = []

        # 1. Determine Primary Language (User Preference or English)
        primary_lang = preferred_lang or 'en-US'

        if explicit_language_selection:
            # User explicitly chose via language selection modal - download ONLY their choice
            if supported_languages:
                matched = self._smart_match_language(primary_lang, supported_languages)
                if matched:
                    languages_to_download.append(matched)
                else:
                    # Selected language not available, use first supported
                    languages_to_download.append(supported_languages[0])
                    logger.warning(f"[GOG] Selected language {primary_lang} not available, using {supported_languages[0]}")
            else:
                languages_to_download.append(primary_lang)
            logger.info(f"[GOG] User explicitly selected language: {languages_to_download}")
        else:
            # No explicit selection - use system preference with fallback
            if supported_languages:
                matched = self._smart_match_language(primary_lang, supported_languages)
                if matched:
                    languages_to_download.append(matched)
                else:
                    # Fallback to English or first available
                    matched_english = self._smart_match_language('en-US', supported_languages)
                    if matched_english:
                        languages_to_download.append(matched_english)
                    else:
                        languages_to_download.append(supported_languages[0])
            else:
                languages_to_download.append(primary_lang)
                if 'en-US' not in languages_to_download:
                    languages_to_download.append('en-US')
            logger.info(f"[GOG] Using system language preference: {languages_to_download}")

        for lang in languages_to_download:
            cmd.extend(['--lang', lang])
        # This ensures we always attempted to download at least the English version.
        
        # CRITICAL FIX: Delete manifest AGAIN right before download
        # gogdl info creates a manifest that can override our --lang flags
        # Delete for BOTH download and repair modes to ensure correct language
        manifest_locations = [
            os.path.join(self.gogdl_config_dir, "heroic_gogdl", "manifests", game_id),
            os.path.join(os.path.dirname(self.gogdl_config_dir), "heroic_gogdl", "manifests", game_id),
            os.path.join(self.gogdl_config_dir, "manifests", game_id),
            os.path.join(os.path.dirname(self.gogdl_config_dir), "gogdl", "manifests", game_id),
        ]
        for manifest_path in manifest_locations:
            if os.path.exists(manifest_path):
                try:
                    os.remove(manifest_path)
                    logger.info(f"[GOG] Pre-download: cleared manifest recreated by gogdl info: {manifest_path}")
                except Exception as e:
                    logger.warning(f"[GOG] Pre-download: could not clear manifest {manifest_path}: {e}")
        
        logger.info(f"[GOG] Using {install_mode} mode with path: {gogdl_path}")
        
        # DEBUG: Log the full command to diagnose language issues
        logger.info(f"[GOG] Full command: {' '.join(cmd)}")
        
        # Redirect stderr to stdout to capture logging output from gogdl
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._get_gogdl_env()
        )
        
        # 5. Monitor Progress
        # gogdl outputs logs to stderr/stdout depending on config.
        # Progress format (from progressbar.py):
        #   = Progress: 86.40 5645360434/6534344139, Running for: 00:01:30, ETA: 00:00:15
        #   = Downloaded: 123.45 MiB, Written: 456.78 MiB
        #   + Download - 12.34 MiB/s (raw) / 45.67 MiB/s (decompressed)
        
        # Track progress state
        current_progress = {
            'progress_percent': 0,
            'downloaded_bytes': 0,
            'total_bytes': 0,
            'speed_bps': 0.0,  # Speed in bytes/sec (download_manager expects this)
            'eta_seconds': 0,
            'phase_message': 'Starting download...'
        }
        
        while True:
            try:
                line = await asyncio.wait_for(proc.stdout.readline(), timeout=120.0)
            except asyncio.TimeoutError:
                logger.warning(f"[GOG] Download stalled (no output for 120s) - terminating gogdl")
                try:
                    proc.terminate()
                    # Give it a moment to die gracefully
                    await asyncio.sleep(1)
                    if proc.returncode is None:
                        proc.kill()
                except Exception as e:
                    logger.error(f"[GOG] Error terminating stalled process: {e}")
                
                return {'success': False, 'error': 'Download stalled (connection timeout)'}

            if not line:
                break
                
            line_str = line.decode().strip()
            if not line_str:
                continue
                
            # Log non-progress lines
            if 'Progress:' not in line_str and 'Download' not in line_str:
                if not line_str.startswith('[gogdl]'):
                    logger.info(f"[gogdl] {line_str}")
            
            if progress_callback:
                try:
                    # Parse main progress line: "= Progress: 86.40 5645360434/6534344139, Running for: 00:01:30, ETA: 00:00:15"
                    if 'Progress:' in line_str:
                        # Extract percentage and bytes
                        part = line_str.split('Progress:')[1].strip()
                        # "86.40 5645360434/6534344139, Running for: ..."
                        tokens = part.split()
                        if len(tokens) >= 2:
                            percent = float(tokens[0])
                            current_progress['progress_percent'] = percent
                            
                            # Parse bytes: "5645360434/6534344139,"
                            bytes_part = tokens[1].rstrip(',')
                            if '/' in bytes_part:
                                written, total = bytes_part.split('/')
                                current_progress['downloaded_bytes'] = int(written)
                                current_progress['total_bytes'] = int(total)
                        
                        # Parse ETA: "ETA: 00:00:15"
                        if 'ETA:' in line_str:
                            eta_part = line_str.split('ETA:')[1].strip()
                            # "00:00:15" or similar
                            eta_time = eta_part.split()[0] if eta_part else "00:00:00"
                            # Convert HH:MM:SS to seconds
                            try:
                                parts = eta_time.split(':')
                                if len(parts) == 3:
                                    h, m, s = int(parts[0]), int(parts[1]), int(parts[2])
                                    current_progress['eta_seconds'] = h * 3600 + m * 60 + s
                                elif len(parts) == 2:
                                    m, s = int(parts[0]), int(parts[1])
                                    current_progress['eta_seconds'] = m * 60 + s
                            except:
                                pass
                        
                        # Update phase message
                        current_progress['phase_message'] = f'Downloading... {percent:.1f}%'
                        
                        # Send progress update
                        await progress_callback(current_progress)
                    
                    # Parse download speed line: "+ Download - 12.34 MiB/s (raw) / ..."
                    if '+ Download' in line_str and 'MiB/s' in line_str:
                        # Extract speed: "12.34 MiB/s"
                        try:
                            # Find speed value before "MiB/s"
                            speed_match = line_str.split('Download')[1]
                            # "- 12.34 MiB/s (raw) / ..."
                            speed_part = speed_match.split('MiB/s')[0].strip()
                            # "- 12.34 " -> take last token
                            speed_tokens = speed_part.split()
                            if speed_tokens:
                                speed_mib = float(speed_tokens[-1])
                                # Convert MiB/s to bytes/sec (download_manager will convert to MB/s for display)
                                # 1 MiB = 1024 * 1024 bytes
                                current_progress['speed_bps'] = speed_mib * 1024 * 1024
                                
                                # Also send update when speed changes (so UI updates immediately)
                                await progress_callback(current_progress)
                        except:
                            pass
                        
                except Exception as e:
                    logger.debug(f"[GOG] Progress parse error: {e}")

        # Wait for finish
        await proc.wait()
        
        if proc.returncode != 0:
            logger.error(f"[GOG] gogdl failed with return code {proc.returncode}")
            # Cleanup partial files
            if folder_name:
                partial_path = os.path.join(base_path, folder_name)
                if os.path.exists(partial_path):
                    logger.info(f"[GOG] Cleaning up partial install at {partial_path}")
                    shutil.rmtree(partial_path, ignore_errors=True)
            return {'success': False, 'error': f'Installation failed (code {proc.returncode})'}
        
        # 5.5. VERIFICATION STEP: Run 'repair' to verify and fix any missing files
        # This provides reliability for large game downloads that may have issues
        logger.info(f"[GOG] Running verification (repair) to check for missing files...")
        if progress_callback:
            await progress_callback({
                'phase': 'verifying',
                'phase_message': ''
            })
        
        # Determine install path for repair command - MUST use game folder, not base_path
        # Otherwise repair will extract files to base_path root
        repair_path = None
        
        # Try 1: Use predicted folder_name
        if folder_name:
            potential_path = os.path.join(base_path, folder_name)
            if os.path.exists(potential_path):
                repair_path = potential_path
                logger.info(f"[GOG] Repair will use predicted folder: {repair_path}")
        
        # Try 2: Scan for the goggame info file to find the actual folder
        if not repair_path:
            for item in os.listdir(base_path):
                item_path = os.path.join(base_path, item)
                if os.path.isdir(item_path):
                    goggame_file = os.path.join(item_path, f"goggame-{game_id}.info")
                    if os.path.exists(goggame_file):
                        repair_path = item_path
                        logger.info(f"[GOG] Found game folder via goggame.info: {repair_path}")
                        break
        
        # Fallback: use base_path (not ideal but better than failing)
        if not repair_path:
            repair_path = base_path
            logger.warning(f"[GOG] Could not find game folder, using base_path for repair")
        
        repair_cmd = [
            self.gogdl_bin,
            '--auth-config-path', self.gogdl_config_path,
            'repair',
            game_id,
            '--platform', platform,
            '--path', repair_path,  # Use game folder path so any downloaded files go there
            '--lang', preferred_lang,
            '--with-dlcs',  # Automatically install all owned DLCs
        ]
        
        repair_proc = await asyncio.create_subprocess_exec(
            *repair_cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._get_gogdl_env()
        )
        
        # Log repair output but don't track progress (it's usually quick)
        while True:
            line = await repair_proc.stdout.readline()
            if not line:
                break
            line_str = line.decode().strip()
            if line_str and not line_str.startswith('[gogdl]'):
                logger.info(f"[gogdl-verify] {line_str}")
        
        await repair_proc.wait()
        
        if repair_proc.returncode != 0:
            logger.warning(f"[GOG] Verification had issues (code {repair_proc.returncode}), but installation may still work")
        else:
            logger.info(f"[GOG] Verification passed - installation complete")
            
        # 6. Verify and Locate Game
        logger.info(f"[GOG] Verifying installation in {base_path}")
        found_path = None
        
        # Priority 0: Check if gogdl V2 repair extracted files directly to base_path
        # This happens with the repair command - files go to base_path, not a subfolder
        goggame_in_base = None
        for f in os.listdir(base_path):
            if f.startswith('goggame-') and f.endswith('.info'):
                info_id = f.replace('goggame-', '').replace('.info', '')
                if info_id == game_id:
                    goggame_in_base = f
                    logger.info(f"[GOG] Found {f} directly in base_path - gogdl V2 repair behavior")
                    break
        
        if goggame_in_base:
            # Files were extracted directly to base_path - need to move them to subfolder
            target_folder = folder_name or f"GOG_{game_id}"
            target_path = os.path.join(base_path, target_folder)
            
            logger.info(f"[GOG] Moving extracted files to {target_path}")
            os.makedirs(target_path, exist_ok=True)
            
            # Get current contents and move all NEW files to subfolder
            try:
                current_files = set(os.listdir(base_path))
                new_files = current_files - existing_dirs
                
                for item in new_files:
                    if item == target_folder:
                        continue  # Skip the target folder itself
                    src = os.path.join(base_path, item)
                    dst = os.path.join(target_path, item)
                    try:
                        shutil.move(src, dst)
                    except Exception as e:
                        logger.warning(f"[GOG] Could not move {item}: {e}")
                
                found_path = target_path
                logger.info(f"[GOG] Organized game files into {found_path}")
            except Exception as e:
                logger.error(f"[GOG] Error organizing files: {e}")
                # Fall back to using base_path as install path
                found_path = base_path
        
        # Priority 1: Check predicted folder name (gogdl just created it)
        if not found_path and folder_name:
            predicted_path = os.path.join(base_path, folder_name)
            if os.path.exists(predicted_path) and os.path.isdir(predicted_path):
                found_path = predicted_path
                logger.info(f"[GOG] Found game at predicted path: {found_path}")
        
        # Priority 2: Scan if predicted path failed
        # ONLY check directories that are NEW (not in pre-download snapshot)
        # This prevents detecting games installed by Heroic or other launchers
        if not found_path:
            logger.info("[GOG] Predicted path failed, scanning for NEW directories...")
            candidates = []
            try:
                current_dirs = set(os.listdir(base_path))
                new_dirs = current_dirs - existing_dirs
                logger.info(f"[GOG] Pre-existing dirs: {len(existing_dirs)}, New dirs: {list(new_dirs)}")
                
                for item in new_dirs:
                    item_path = os.path.join(base_path, item)
                    if os.path.isdir(item_path):
                        candidates.append(item)
                        # Check for goggame-*.info file in root and game/ subdirectory
                        # Windows games via gogdl typically have files in game/ subdirectory
                        search_dirs = [item_path]
                        game_subdir = os.path.join(item_path, 'game')
                        if os.path.exists(game_subdir) and os.path.isdir(game_subdir):
                            search_dirs.append(game_subdir)
                        
                        for search_dir in search_dirs:
                            try:
                                for f in os.listdir(search_dir):
                                    if f.startswith('goggame-') and f.endswith('.info'):
                                        info_id = f.replace('goggame-', '').replace('.info', '')
                                        if info_id == game_id:
                                            found_path = item_path
                                            logger.info(f"[GOG] Found game via goggame info in {search_dir}")
                                            break
                            except PermissionError:
                                continue
                            if found_path:
                                break
                        if found_path:
                            break
            except Exception as e:
                logger.error(f"[GOG] Error listing directories: {e}")
            
        if found_path:
            # Write .unifideck-id marker LAST with goggame info data
            # This marker = 100% installed status
            info_data = {"game_id": game_id}
            try:
                # Read goggame info if available
                for item in os.listdir(found_path):
                    if item.startswith('goggame-') and item.endswith('.info'):
                        info_file = os.path.join(found_path, item)
                        with open(info_file, 'r') as f:
                            info_data = json.load(f)
                            info_data['game_id'] = game_id  # Ensure our ID is present
                        break
                
                # Also check game/ subdirectory
                game_subdir = os.path.join(found_path, 'game')
                if 'name' not in info_data and os.path.exists(game_subdir):
                    for item in os.listdir(game_subdir):
                        if item.startswith('goggame-') and item.endswith('.info'):
                            info_file = os.path.join(game_subdir, item)
                            with open(info_file, 'r') as f:
                                info_data = json.load(f)
                                info_data['game_id'] = game_id
                            break
            except Exception as e:
                logger.warning(f"[GOG] Could not read goggame info: {e}")
            
            # Write marker (this marks 100% complete)
            # Include language for setup script to use during first launch
            info_data['language'] = preferred_lang
            marker_path = os.path.join(found_path, '.unifideck-id')
            try:
                with open(marker_path, 'w') as f:
                    json.dump(info_data, f, indent=2)
                logger.info(f"[GOG] Wrote .unifideck-id marker at {marker_path} (language: {preferred_lang})")
            except Exception as e:
                logger.error(f"[GOG] Failed to write marker: {e}")
                # Cleanup on failure to write marker
                shutil.rmtree(found_path, ignore_errors=True)
                return {'success': False, 'error': 'Failed to complete installation'}
            
            # PHASE 3: Post-install verification sweep
            verification = await self._verify_installation(game_id, found_path, platform)
            if not verification['complete']:
                logger.warning(f"[GOG] Verification issue: {verification.get('issue', 'Unknown')}")
                # Don't fail the install, but log the warning
            
            logger.info(f"[GOG] Installation successful at {found_path}")
            
            # PHASE 4: Regenerate manifest for setup script
            # Since we deleted the manifest before download to force language selection,
            # we MUST recreate it now so gog_setup.py can run scriptinterpreter.exe
            logger.info(f"[GOG] Regenerating manifest for {game_id} via gogdl info")
            try:
                info_cmd = [
                    self.gogdl_bin,
                    '--auth-config-path', self.gogdl_config_path,
                    'info', '--platform', 'windows' if platform == 'windows' else 'linux', game_id
                ]
                info_proc = await asyncio.create_subprocess_exec(
                    *info_cmd,
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.PIPE,
                    env=self._get_gogdl_env()
                )
                await info_proc.wait()
                logger.info(f"[GOG] Manifest regenerated (code {info_proc.returncode})")
            except Exception as e:
                logger.error(f"[GOG] Failed to regenerate manifest: {e}")
                
            return {
                'success': True,
                'install_path': found_path,
                'verification': verification
            }
        else:
            logger.warning(f"[GOG] Could not locate game {game_id} in {base_path}. Candidates: {candidates if 'candidates' in locals() else 'unknown'}")
            # Cleanup any partial files
            if folder_name:
                partial_path = os.path.join(base_path, folder_name)
                if os.path.exists(partial_path):
                    shutil.rmtree(partial_path, ignore_errors=True)
            return {
                'success': False,
                'error': 'Installation completed but could not locate game directory'
            }

    async def _get_game_details(self, game_id: str, session=None):
        """Legacy compatibility method - returns dummy data to satisfy main.py"""
        # Return an object that looks like it has no installers, so is_multipart becomes False
        return {} 

    def _find_linux_installer(self, game_details):
        """Legacy compatibility method"""
        return []

    def _find_windows_installer(self, game_details):
        """Legacy compatibility method"""
        return []

    async def uninstall_game(self, game_id: str, install_path: Optional[str] = None) -> Dict[str, Any]:
        """Uninstall game with retry loop and fallback cleanup.
        
        Args:
            game_id: GOG game ID
            install_path: Optional install path from games.map (avoids filesystem scan)
        """
        # Use provided install_path or fall back to filesystem scan
        if install_path and os.path.exists(install_path):
            logger.info(f"[GOG] Using provided install path: {install_path}")
        else:
            info = self.get_installed_game_info(game_id)
            if not info:
                # Game folder doesn't exist - consider it already uninstalled
                logger.info(f"[GOG] Game {game_id} not found - already uninstalled")
                return {'success': True, 'message': 'Game already uninstalled'}
            install_path = info['install_path']
        
        # Retry loop for uninstall
        max_attempts = 3
        for attempt in range(max_attempts):
            try:
                if os.path.exists(install_path):
                    shutil.rmtree(install_path)
                    
                # Verify deletion
                if not os.path.exists(install_path):
                    logger.info(f"[GOG] Successfully uninstalled {game_id} from {install_path}")
                    break
                else:
                    remaining = self._count_files_in_folder(install_path)
                    logger.warning(f"[GOG] Attempt {attempt+1}: Folder still exists ({remaining} files remaining)")
                    
            except PermissionError as e:
                logger.warning(f"[GOG] Attempt {attempt+1} permission error: {e}")
            except Exception as e:
                logger.warning(f"[GOG] Attempt {attempt+1} failed: {e}")
            
            # Fallback on last attempt: file-by-file deletion
            if attempt == max_attempts - 1 and os.path.exists(install_path):
                logger.info(f"[GOG] Using fallback file-by-file cleanup")
                await self._force_cleanup_folder(install_path)
        
        # Clean up support/manifest directory
        support_dir = os.path.join(self.gogdl_config_dir, "gog-support", game_id)
        if os.path.exists(support_dir):
            try:
                shutil.rmtree(support_dir, ignore_errors=True)
                logger.info(f"[GOG] Cleaned up support directory: {support_dir}")
            except Exception as e:
                logger.warning(f"[GOG] Could not clean support dir: {e}")
        
        # Clean up gogdl manifest files (prevents "Nothing to do" on re-download)
        manifest_locations = [
            os.path.join(self.gogdl_config_dir, "heroic_gogdl", "manifests", game_id),
            os.path.join(os.path.dirname(self.gogdl_config_dir), "heroic_gogdl", "manifests", game_id),
            os.path.join(self.gogdl_config_dir, "manifests", game_id),
            os.path.join(os.path.dirname(self.gogdl_config_dir), "gogdl", "manifests", game_id),
        ]
        for manifest_path in manifest_locations:
            if os.path.exists(manifest_path):
                try:
                    os.remove(manifest_path)
                    logger.info(f"[GOG] Deleted manifest: {manifest_path}")
                except Exception as e:
                    logger.warning(f"[GOG] Could not delete manifest {manifest_path}: {e}")
        
        # Final verification
        if os.path.exists(install_path):
            remaining = self._count_files_in_folder(install_path)
            if remaining > 0:
                logger.error(f"[GOG] Uninstall incomplete: {remaining} files remaining in {install_path}")
                return {'success': False, 'error': f'Could not delete all files ({remaining} remaining)'}
        
        return {'success': True, 'message': f'Uninstalled from {install_path}'}
    
    async def _force_cleanup_folder(self, path: str):
        """Fallback: Delete files one by one, handling locked files."""
        deleted_count = 0
        error_count = 0
        
        for root, dirs, files in os.walk(path, topdown=False):
            for name in files:
                file_path = os.path.join(root, name)
                try:
                    os.remove(file_path)
                    deleted_count += 1
                except Exception as e:
                    logger.debug(f"[GOG] Could not delete {file_path}: {e}")
                    error_count += 1
            for name in dirs:
                dir_path = os.path.join(root, name)
                try:
                    os.rmdir(dir_path)
                except Exception:
                    pass
        
        # Try to remove root folder
        try:
            os.rmdir(path)
        except Exception:
            pass
        
        logger.info(f"[GOG] Force cleanup: deleted {deleted_count} files, {error_count} errors")
    
    def _count_files_in_folder(self, path: str) -> int:
        """Count total files in folder recursively."""
        count = 0
        try:
            for root, dirs, files in os.walk(path):
                count += len(files)
        except Exception:
            pass
        return count
    
    def _get_folder_size(self, path: str) -> int:
        """Get total size of folder in bytes."""
        total = 0
        try:
            for root, dirs, files in os.walk(path):
                for f in files:
                    try:
                        total += os.path.getsize(os.path.join(root, f))
                    except OSError:
                        pass
        except Exception:
            pass
        return total

    def _find_game_executable(self, install_path: str) -> Optional[str]:
        """Find the game executable using goggame info or start.sh"""
        result = self._find_game_executable_with_workdir(install_path)
        if result:
            return result[0]
        return None

    def _find_gog_wrapper_launch_target(
        self,
        install_path: str,
        root_dir: str,
        primary_task: Dict[str, Any],
    ) -> Optional[Tuple[str, str]]:
        """Prefer GOG's wrapper batch file when the manifest points at an internal launcher."""
        task_path = primary_task.get('path', '')
        if not task_path:
            return None

        task_basename = os.path.basename(task_path).lower()
        candidate_roots = [root_dir]
        if install_path not in candidate_roots:
            candidate_roots.append(install_path)

        for candidate_root in candidate_roots:
            wrapper_path = os.path.join(candidate_root, 'run-game.bat')
            if not os.path.exists(wrapper_path):
                continue

            try:
                wrapper_content = ''
                with open(wrapper_path, 'r', encoding='utf-8', errors='ignore') as f:
                    wrapper_content = f.read().lower()
            except OSError:
                wrapper_content = ''

            if task_basename in wrapper_content or task_basename in {'dosbox.exe', 'scummvm.exe'}:
                logger.info(f"[GOG] Using wrapper launch target: {wrapper_path}")
                return (wrapper_path, candidate_root)

        return None
    
    def _find_game_executable_with_workdir(self, install_path: str) -> Optional[Tuple[str, str]]:
        """Find game executable and working directory from install path."""
        try:
            # Look for info file either in root or in 'game/' subdir
            info_file = None
            search_dirs = [install_path]
            
            # Check nested game/ folder first if it exists, as it's the likely payload root
            game_subdir = os.path.join(install_path, 'game')
            if os.path.exists(game_subdir) and os.path.isdir(game_subdir):
                search_dirs.insert(0, game_subdir)
                
            root_dir = install_path
            
            for d in search_dirs:
                if not os.path.exists(d): continue
                for item in os.listdir(d):
                    if item.startswith('goggame-') and item.endswith('.info'):
                        info_file = os.path.join(d, item)
                        root_dir = d # Update root dir to where info file was found
                        break
                if info_file:
                    break
            
            if info_file:
                try:
                    with open(info_file, 'r') as f:
                        data = json.load(f)
                        play_tasks = data.get('playTasks', [])
                        
                        # Find primary task
                        primary = None
                        for task in play_tasks:
                            if task.get('isPrimary'):
                                primary = task
                                break
                        
                        if primary:
                            wrapper_launch = self._find_gog_wrapper_launch_target(
                                install_path, root_dir, primary
                            )
                            if wrapper_launch:
                                return wrapper_launch

                            exe_rel = primary.get('path')
                            work_rel = primary.get('workingDir', '')
                            
                            full_exe = os.path.join(root_dir, exe_rel)
                            full_work = os.path.join(root_dir, work_rel) if work_rel else os.path.dirname(full_exe)
                            
                            # Fix slashes for Linux if coming from Windows JSON
                            full_exe = full_exe.replace('\\', '/')
                            full_work = full_work.replace('\\', '/')
                            
                            # FIX: Some games have exe in a subdir but data files in the install root.
                            # Examples: Shadow of Mordor (.arch05), Prince of Persia (.forge)
                            # If data files exist in root, use root as work_dir
                            if full_work != install_path:
                                data_extensions = ('.arch05', '.forge')
                                data_files_in_root = any(
                                    any(f.endswith(ext) for ext in data_extensions)
                                    for f in os.listdir(install_path) if os.path.isfile(os.path.join(install_path, f))
                                )
                                if data_files_in_root:
                                    logger.info(f"[GOG] Data files found in install root, using install_path as work_dir")
                                    full_work = install_path
                            
                            if os.path.exists(full_exe):
                                logger.info(f"[GOG] Found EXE via info: {full_exe}")
                                return (full_exe, full_work)
                except Exception as e:
                    logger.warning(f"[GOG] Error reading info file: {e}")

            # PRIORITY 2: Look for 'start.sh' (Linux standard)
            # Check root and optional game/ subdir
            for d in search_dirs:
                if not os.path.exists(d): continue
                start_sh = os.path.join(d, 'start.sh')
                if os.path.exists(start_sh):
                    logger.info(f"[GOG] Found start.sh: {start_sh}")
                    return (start_sh, d)
                
            # PRIORITY 3: Robust fallback - find largest .exe (most likely the game)
            # Search recursively but skip known non-game executables
            import glob
            skip_patterns = ['unins', 'setup', 'install', 'crash', 'redist', 'vcredist', 
                             'vc_redist', 'dxsetup', 'physx', 'dotnet', 'directx']
            
            for d in search_dirs:
                if not os.path.exists(d): 
                    continue
                
                exe_candidates = []
                # Search recursively
                for pattern in ['*.exe', '**/*.exe']:
                    for exe_path in glob.glob(os.path.join(d, pattern), recursive=True):
                        basename = os.path.basename(exe_path).lower()
                        # Skip known non-game executables
                        if any(skip in basename for skip in skip_patterns):
                            continue
                        try:
                            size = os.path.getsize(exe_path)
                            exe_candidates.append((exe_path, size))
                        except OSError:
                            continue
                
                if exe_candidates:
                    # Sort by size descending - largest is most likely the game
                    exe_candidates.sort(key=lambda x: x[1], reverse=True)
                    best_exe = exe_candidates[0][0]
                    work_dir = os.path.dirname(best_exe)
                    logger.info(f"[GOG] Fallback: Found largest exe ({exe_candidates[0][1]/1024/1024:.1f}MB): {best_exe}")
                    return (best_exe, work_dir)
            
            logger.warning(f"[GOG] No executable found in any search path: {search_dirs}")
            return None

        except Exception as e:
            logger.error(f"[GOG] Error finding game executable: {e}", exc_info=True)
            return None

    async def get_game_dlcs(self, game_id: str) -> List[Dict[str, Any]]:
        """Return list of available DLCs for a game.
        
        Based on Lutris GOGService.get_game_dlcs() pattern.
        Returns list of DLC products with id, title, and installation info.
        """
        await self._ensure_fresh_token()
        
        if not self.access_token:
            logger.warning("[GOG] Cannot fetch DLCs: not authenticated")
            return []
        
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            async with aiohttp.ClientSession(connector=connector) as session:
                # Get game details to find DLC expand URL
                from ..utils.locale import get_unifideck_locale
                url = f'https://api.gog.com/products/{game_id}?expand=downloads&locale={get_unifideck_locale()}'
                async with session.get(url, headers={'Authorization': f'Bearer {self.access_token}'}) as response:
                    if response.status != 200:
                        logger.error(f"[GOG] Failed to get game details for DLCs: {response.status}")
                        return []
                    
                    data = await response.json()
                    dlcs_info = data.get('dlcs', {})
                    
                    if not dlcs_info:
                        logger.debug(f"[GOG] No DLCs for game {game_id}")
                        return []
                    
                    # Get expanded DLC list
                    expanded_url = dlcs_info.get('expanded_all_products_url')
                    if not expanded_url:
                        # Basic DLC list without expansion
                        return dlcs_info.get('products', [])
                    
                    async with session.get(expanded_url, headers={'Authorization': f'Bearer {self.access_token}'}) as dlc_response:
                        if dlc_response.status == 200:
                            dlc_list = await dlc_response.json()
                            logger.info(f"[GOG] Found {len(dlc_list)} DLCs for game {game_id}")
                            return dlc_list
                        else:
                            logger.warning(f"[GOG] Failed to fetch expanded DLC list: {dlc_response.status}")
                            return []
                            
        except Exception as e:
            logger.error(f"[GOG] Error fetching DLCs for {game_id}: {e}")
            return []

    async def get_available_languages(self, game_id: str) -> List[str]:
        """Return list of available installer languages for a game.
        
        Queries gogdl info to get available language options.
        """
        await self._ensure_fresh_token()
        
        if not self.gogdl_bin:
            return ['en']
        
        try:
            self._ensure_auth_config()
            
            # Run gogdl info to get available languages
            cmd = [
                self.gogdl_bin, '--auth-config-path', self.gogdl_config_path,
                'info', '--platform', 'windows', game_id
            ]
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=self._get_gogdl_env()
            )

            # Add timeout to prevent hanging
            try:
                stdout, _ = await asyncio.wait_for(proc.communicate(), timeout=30)
            except asyncio.TimeoutError:
                proc.kill()
                logger.warning(f"[GOG] Timeout getting languages for {game_id}")
                return ['en-US']

            if proc.returncode == 0:
                for line in stdout.decode().strip().split('\n'):
                    try:
                        data = json.loads(line)
                        # gogdl returns 'languages' not 'available_languages'
                        if 'languages' in data:
                            langs = data['languages']
                            # Handle empty array - fallback to English
                            if not langs or len(langs) == 0:
                                logger.warning(f"[GOG] Empty languages array for {game_id}, using fallback")
                                return ['en-US']
                            logger.info(f"[GOG] Available languages for {game_id}: {langs}")
                            return langs
                    except json.JSONDecodeError:
                        continue

            return ['en-US']

        except Exception as e:
            logger.error(f"[GOG] Error getting available languages: {e}")
            return ['en-US']

    async def install_dlc(self, game_id: str, dlc_id: str, base_path: str = None, progress_callback=None) -> Dict[str, Any]:
        """Install a DLC for a game using gogdl.
        
        DLCs are installed to the same location as the base game.
        """
        if not self.gogdl_bin:
            return {'success': False, 'error': 'gogdl binary not found'}
        
        await self._ensure_fresh_token()
        
        if not await self.is_available():
            return {'success': False, 'error': 'Not authenticated with GOG'}
        
        self._ensure_auth_config()
        
        # Find the base game install path
        if not base_path:
            game_info = self.get_installed_game_info(game_id)
            if game_info:
                base_path = game_info['install_path']
            else:
                base_path = os.path.expanduser("~/GOG Games")
        
        logger.info(f"[GOG] Installing DLC {dlc_id} for game {game_id} to {base_path}")
        
        # Get preferred language
        preferred_lang = self._get_unifideck_language()
        
        # Determine platform (same as base game)
        platform = 'windows'  # Default, could be detected from base game
        
        cmd = [
            self.gogdl_bin,
            '--auth-config-path', self.gogdl_config_path,
            'repair',
            dlc_id,
            '--platform', platform,
            '--path', base_path,
            '--lang', preferred_lang
        ]
        
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            env=self._get_gogdl_env()
        )
        
        # Monitor progress (similar to install_game)
        while True:
            line = await proc.stdout.readline()
            if not line:
                break
            
            line_str = line.decode().strip()
            if line_str and progress_callback and 'Progress:' in line_str:
                # Parse and report progress
                try:
                    part = line_str.split('Progress:')[1].strip()
                    tokens = part.split()
                    if tokens:
                        percent = float(tokens[0])
                        await progress_callback({
                            'progress_percent': percent,
                            'phase_message': f'Installing DLC... {percent:.1f}%'
                        })
                except:
                    pass
        
        await proc.wait()
        
        if proc.returncode == 0:
            logger.info(f"[GOG] DLC {dlc_id} installed successfully")
            return {'success': True, 'dlc_id': dlc_id}
        else:
            logger.error(f"[GOG] DLC installation failed with code {proc.returncode}")
            return {'success': False, 'error': f'Installation failed (code {proc.returncode})'}

    async def get_game_store_url(self, gog_id: str) -> Optional[str]:
        """Fetch GOG store page URL for a game.
        
        Args:
            gog_id: GOG product ID
            
        Returns:
            Store URL or None if not found
        """
        try:
            import aiohttp
            import ssl
            
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            timeout = aiohttp.ClientTimeout(total=10.0)
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                url = f"https://api.gog.com/products/{gog_id}?expand=description"
                async with session.get(url) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        product_card = data.get('links', {}).get('product_card')
                        if product_card:
                            logger.debug(f"[GOG] Found store URL for {gog_id}: {product_card}")
                            return product_card
                    else:
                        logger.debug(f"[GOG] API returned {resp.status} for product {gog_id}")
        except Exception as e:
            logger.warning(f"[GOG] Error fetching store URL for {gog_id}: {e}")
        
        return None

    def _get_local_build_id(self, install_path: str, game_id: str) -> Optional[str]:
        """Get local buildId from goggame-*.info file.
        
        Searches install_path root and game/ subdirectory for the goggame info file.
        
        Args:
            install_path: Path to the installed game directory.
            game_id: GOG product ID.
        
        Returns:
            buildId string, or None if not found.
        """
        search_dirs = [install_path, os.path.join(install_path, 'game')]
        
        for search_dir in search_dirs:
            info_file = os.path.join(search_dir, f'goggame-{game_id}.info')
            if os.path.exists(info_file):
                try:
                    with open(info_file, 'r') as f:
                        data = json.load(f)
                    build_id = data.get('buildId')
                    if build_id:
                        logger.debug(f"[GOG] Local buildId for {game_id}: {build_id}")
                        return str(build_id)
                except Exception as e:
                    logger.warning(f"[GOG] Error reading {info_file}: {e}")
        
        # Also check .unifideck-id marker for buildId
        marker_path = os.path.join(install_path, '.unifideck-id')
        if os.path.exists(marker_path):
            try:
                with open(marker_path, 'r') as f:
                    data = json.loads(f.read().strip())
                build_id = data.get('buildId')
                if build_id:
                    logger.debug(f"[GOG] Local buildId from marker for {game_id}: {build_id}")
                    return str(build_id)
            except Exception:
                pass
        
        return None

    async def check_for_game_update(self, game_id: str) -> Optional[bool]:
        """Check if a specific GOG game has an update available.
        
        Compares local buildId (from goggame-*.info) against the latest
        remote buildId from GOG's Content System API.
        
        Args:
            game_id: GOG product ID.
        
        Returns:
            True if update available, False if up-to-date, None if check failed.
        """
        await self._ensure_fresh_token()
        
        if not self.access_token:
            logger.warning(f"[GOG] No access token for update check")
            return None
        
        # Find install path for this game
        info = self.get_installed_game_info(game_id)
        if not info:
            logger.debug(f"[GOG] Game {game_id} not installed, skipping update check")
            return None
        
        install_path = info.get('install_path', '')
        
        # Get local build ID
        local_build_id = self._get_local_build_id(install_path, game_id)
        if not local_build_id:
            logger.warning(f"[GOG] No local buildId found for {game_id}")
            return None
        
        # Query Content System API for latest build
        try:
            import aiohttp
            import ssl
            
            ssl_context = ssl.create_default_context()
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE
            
            connector = aiohttp.TCPConnector(ssl=ssl_context)
            timeout = aiohttp.ClientTimeout(total=10.0)
            
            async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                url = f"https://content-system.gog.com/products/{game_id}/os/windows/builds?generation=2"
                headers = {'Authorization': f'Bearer {self.access_token}'}
                
                async with session.get(url, headers=headers) as resp:
                    if resp.status == 200:
                        data = await resp.json()
                        items = data.get('items', [])
                        
                        if items:
                            remote_build_id = str(items[0].get('build_id', ''))
                            logger.info(f"[GOG] {game_id}: local={local_build_id}, remote={remote_build_id}")
                            
                            if remote_build_id and remote_build_id != local_build_id:
                                logger.info(f"[GOG] Update available for {game_id}!")
                                return True
                            else:
                                return False
                        else:
                            logger.warning(f"[GOG] No builds found for {game_id}")
                            return None
                    else:
                        logger.warning(f"[GOG] Content System API returned {resp.status} for {game_id}")
                        return None
                        
        except Exception as e:
            logger.error(f"[GOG] Error checking update for {game_id}: {e}")
            return None

    async def check_for_updates(self) -> List[str]:
        """Check which installed GOG games have updates available.
        
        Iterates through all installed games and queries the Content System API
        for each one. This is slower than Epic/Amazon since there's no batch
        endpoint.
        
        Returns:
            List of game IDs that have updates available.
        """
        if not os.path.exists(self.download_dir):
            return []
        
        updates = []
        
        try:
            for item in os.listdir(self.download_dir):
                item_path = os.path.join(self.download_dir, item)
                if not os.path.isdir(item_path):
                    continue
                
                game_id = self._get_game_id_from_dir(item_path)
                if not game_id:
                    continue
                
                has_update = await self.check_for_game_update(game_id)
                if has_update:
                    updates.append(game_id)
        except Exception as e:
            logger.error(f"[GOG] Error checking for updates: {e}")
        
        logger.info(f"[GOG] Found {len(updates)} games with updates")
        return updates

    async def update_game(self, game_id: str, install_path: Optional[str] = None) -> Dict[str, Any]:
        """Update an installed GOG game using gogdl.
        
        Args:
            game_id: GOG product ID.
            install_path: Path to the installed game (required for gogdl).
            
        Returns:
            Dict with 'success' and optionally 'error'.
        """
        if not self.gogdl_bin:
            return {'success': False, 'error': 'gogdl binary not found'}
        
        # Resolve install_path if not provided
        if not install_path:
            info = self.get_installed_game_info(game_id)
            if info:
                install_path = info.get('install_path', '')
            if not install_path:
                return {'success': False, 'error': f'Could not find install path for {game_id}'}
        
        # Ensure auth tokens are synced
        await self._ensure_fresh_token()
        self._sync_auth_to_gogdl()
        
        try:
            logger.info(f"[GOG] Starting update for {game_id} at {install_path}")
            
            env = self._get_gogdl_env()
            proc = await asyncio.create_subprocess_exec(
                self.gogdl_bin, 'update', game_id, '--path', install_path,
                '--auth-config-path', self.gogdl_config_path,
                '--platform', 'windows',
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                env=env
            )
            
            while True:
                line = await proc.stdout.readline()
                if not line:
                    break
                line_str = line.decode().strip()
                if line_str:
                    logger.info(f"[GOG Update] {line_str}")
            
            await proc.wait()
            
            if proc.returncode == 0:
                logger.info(f"[GOG] Successfully updated {game_id}")
                return {'success': True, 'message': f'Successfully updated {game_id}'}
            else:
                logger.error(f"[GOG] Update failed for {game_id} (exit code {proc.returncode})")
                return {'success': False, 'error': 'Update failed - check logs'}
        
        except Exception as e:
            logger.error(f"[GOG] Error updating {game_id}: {e}")
            return {'success': False, 'error': str(e)}
