"""
Microsoft / Xbox Cloud Gaming connector for Unifideck.

Authenticates via Microsoft OAuth + Xbox Live token chain, checks for an
active Game Pass subscription, then syncs the full xCloud catalog.  Games
are launched via Xbox Cloud Gaming (streaming) in the Steam CEF browser
at ``https://www.xbox.com/play/launch/{productId}``.

If the user has no Game Pass subscription, a warning notification is
shown and no games are synced.

Auth flow
---------
  1. Microsoft OAuth (microsoftonline.com) → access_token + refresh_token
  2. XBL user token  (user.auth.xboxlive.com)
  3. XSTS token      (xsts.auth.xboxlive.com, RP = xboxlive.com)
  4. Game Pass subscription check (catalog.gamepass.com, signed catalog)
  5. xCloud catalog  (catalog.gamepass.com, public ~500+ games)
  6. Title resolution (displaycatalog.mp.microsoft.com, batch title lookup)

Locale
------
API calls use the locale from Unifideck's central ``settings.json``;
see ``utils/locale.py``.
"""

import asyncio
import subprocess
import json
import logging
import os
import time
import urllib.parse
from typing import Dict, Any, List, Optional

from .base import Store, Game
from .microsoft_auth import (
    http_post, http_get, build_xbl_chain,
)
from .microsoft_cdp import intercept_oauth_code

logger = logging.getLogger(__name__)

# ──────────────────────────── constants ────────────────────────────────────

# OAuth endpoints, URLs, paths, and User-Agent strings are all read from
# settings.json via _get_required_setting() / _get_ms_setting().
# Only internal logic constants remain here.

MS_MARKER_FILE  = ".unifideck-ms-id"


# ──────────────────────────── connector ────────────────────────────────────

class MicrosoftConnector(Store):
    """
    Microsoft Store / Xbox Live library connector.

    Surfaces titles from the Xbox Title Hub that have a valid MS Store
    BigId and declare PC device compatibility — the subset most likely
    to work (or be attempted) via Proton on SteamOS.

    Win32 games can be downloaded and installed via the FE3 delivery API.
    UWP-only titles are surfaced but marked as not compatible.
    """

    def __init__(self, plugin_dir: Optional[str] = None, plugin_instance=None):
        """Initialise the Microsoft Store connector.

        Args:
            plugin_dir: Path to the Decky plugin directory (for settings.json lookup).
            plugin_instance: Reference to the main plugin (for sync_libraries callbacks).
        """
        self.plugin_dir      = plugin_dir
        self.plugin_instance = plugin_instance

        self._ms_access_token:  Optional[str] = None
        self._ms_refresh_token: Optional[str] = None
        self._token_saved_at:   float = 0.0

        self._xsts_token: Optional[str] = None
        self._user_hash:  Optional[str] = None
        self._xuid:       Optional[str] = None

        self._settings_cache: Optional[Dict[str, Any]] = None
        # Chromium subprocess for auth (CDP interception on port 9222)
        self._chromium_process = None
        self._chromium_cdp_port: int = 9222
        self._load_tokens()
        logger.info("[MS] MicrosoftConnector initialised")

    # ── Locale helpers ───────────────────────────────────────────────────

    def _get_locale(self) -> str:
        """Return the BCP-47 locale from Unifideck settings (e.g. 'fr-FR')."""
        from ..utils.locale import get_unifideck_locale
        return get_unifideck_locale()

    def _get_market(self) -> str:
        """Return the ISO 3166-1 alpha-2 market code (e.g. 'FR')."""
        from ..utils.locale import get_unifideck_market
        return get_unifideck_market()


    # ── Settings helpers ─────────────────────────────────────────────────

    def _load_settings(self) -> Dict[str, Any]:
        """Load and merge ``stores.microsoft`` from all settings.json files.

        Merges in reverse priority order (defaults → plugin root → user) so
        that user values override defaults.  The result is cached in
        ``_settings_cache`` to avoid re-reading files on every getter call.
        """
        merged: Dict[str, Any] = {}
        paths = []
        if self.plugin_dir:
            paths.append(os.path.join(self.plugin_dir, "defaults", "settings.json"))
            paths.append(os.path.join(self.plugin_dir, "settings.json"))
        paths.append(os.path.expanduser("~/.local/share/unifideck/settings.json"))

        for path in paths:
            try:
                if os.path.exists(path):
                    with open(path) as f:
                        data = json.load(f)
                    section = data.get("stores", {}).get("microsoft", {})
                    merged.update(section)
            except Exception as e:
                logger.debug(f"[MS] Could not read settings from {path}: {e}")

        self._settings_cache = merged
        logger.debug(f"[MS] Settings loaded ({len(merged)} keys)")
        return merged

    def _reload_settings(self) -> None:
        """Force re-read of settings.json on next access."""
        self._settings_cache = None

    def _get_ms_setting(self, key: str, default: str = "") -> str:
        """Read ``stores.microsoft.<key>`` from the cached settings.

        The cache is populated on first access via _load_settings().
        Call _reload_settings() to force a re-read from disk.
        """
        if self._settings_cache is None:
            self._load_settings()
        val = self._settings_cache.get(key, "")
        if val:
            return str(val)
        return default

    def _get_required_setting(self, key: str) -> str:
        """Read a required ``stores.microsoft.<key>`` — logs an error if missing."""
        val = self._get_ms_setting(key)
        if not val:
            label = key.replace("_", " ")
            logger.error(f"[MS] Missing '{key}' in settings.json — {label} will fail.")
        return val

    # Required settings — each reads stores.microsoft.<key> from settings.json.
    # See _get_required_setting() for the search order (user → plugin → defaults).

    def _get_client_id(self) -> str:         return self._get_required_setting("client_id")
    def _get_auth_url(self) -> str:          return self._get_required_setting("auth_url")
    def _get_token_url(self) -> str:         return self._get_required_setting("token_url")
    def _get_redirect_uri(self) -> str:      return self._get_required_setting("redirect_uri")
    def _get_scope(self) -> str:             return self._get_required_setting("scope")
    def _get_xbl_auth_url(self) -> str:      return self._get_required_setting("xbl_auth_url")
    def _get_xsts_url(self) -> str:          return self._get_required_setting("xsts_url")
    def _get_product_url(self) -> str:       return self._get_required_setting("product_url")
    def _get_xbl_user_agent(self) -> str:    return self._get_required_setting("xbl_user_agent")
    def _get_catalog_user_agent(self) -> str: return self._get_required_setting("catalog_user_agent")
    def _get_xcloud_catalog_id(self) -> str: return self._get_required_setting("xcloud_catalog_id")
    def _get_gamepass_catalog_url(self) -> str: return self._get_required_setting("gamepass_catalog_url")

    # Settings with special handling (path expansion, type conversion).

    def _get_token_file(self) -> str:
        """Filesystem path for persisted OAuth tokens (with ~ expansion)."""
        return os.path.expanduser(self._get_required_setting("token_file"))


    def _get_token_refresh_threshold(self) -> int:
        """Max token age (seconds) before proactive refresh.  Default 2400."""
        raw = self._get_ms_setting("token_refresh_threshold", "2400")
        try:
            return int(raw)
        except (ValueError, TypeError):
            logger.warning(f"[MS] Invalid token_refresh_threshold {raw!r}, using 2400")
            return 2400


    # ── Store interface ──────────────────────────────────────────────────

    @property
    def store_name(self) -> str:
        """Unique identifier for this store connector."""
        return "microsoft"

    async def is_available(self) -> bool:
        """Return True if we have a saved (and refreshable) token."""
        if not os.path.exists(self._get_token_file()):
            return False
        try:
            with open(self._get_token_file()) as f:
                data = json.load(f)
            return bool(data.get("refresh_token"))
        except Exception:
            return False


    # ── Chromium auth browser ────────────────────────────────────────────

    @staticmethod
    def _clean_env() -> dict:
        """Return a clean environment for launching Chromium/flatpak.

        - Strips LD_LIBRARY_PATH/LD_PRELOAD (PluginLoader bundles its own
          OpenSSL which conflicts with system libraries).
        - Injects DISPLAY, XDG_RUNTIME_DIR, DBUS_SESSION_BUS_ADDRESS
          (PluginLoader is a systemd service without a display session).
        - Finds the real XAUTHORITY file (randomly named in /run/user/).
        - Clears GTK_MODULES to suppress canberra-gtk-module warnings.
        """
        import glob
        uid = os.stat("/home/deck").st_uid
        env = {
            k: v for k, v in os.environ.items()
            if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")
        }
        env.setdefault("DISPLAY", ":0")
        env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
        env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
        # XAUTHORITY: SteamOS uses a randomly-named file in /run/user/<uid>/
        if "XAUTHORITY" not in env:
            xauth_files = glob.glob(f"/run/user/{uid}/xauth_*")
            if xauth_files:
                env["XAUTHORITY"] = xauth_files[0]
            elif os.path.exists("/home/deck/.Xauthority"):
                env["XAUTHORITY"] = "/home/deck/.Xauthority"
        env["GTK_MODULES"] = ""
        return env

    @staticmethod
    def _deck_cmd(cmd: list) -> list:
        """Prefix a command for the deck user if running as root.

        If running as root (unlikely on Steam Deck but possible),
        ``runuser -u deck --`` drops privileges to the ``deck`` user.
        Otherwise returns the command unchanged — env vars are handled
        by ``_clean_env()`` passed to subprocess via ``env=``.
        """
        if os.getuid() == 0:
            return ["runuser", "-u", "deck", "--"] + cmd
        return cmd

    def _find_chromium_cmd(self) -> Optional[list]:
        """Find available Chromium/Chrome command.

        Returns:
            Command as a list (for subprocess), or None if not found.
        """
        import shutil
        # Flatpak Chromium
        if shutil.which("flatpak"):
            for app_id in ("org.chromium.Chromium", "com.google.Chrome"):
                try:
                    # Check both --user and --system installations
                    for flag in ("--user", "--system"):
                        result = subprocess.run(
                            ["flatpak", "info", flag, app_id],
                            capture_output=True, timeout=5,
                            env=self._clean_env(),
                        )
                        if result.returncode == 0:
                            return ["flatpak", "run", app_id]
                except Exception:
                    pass
        # Native installs
        for binary in ("chromium", "chromium-browser", "google-chrome"):
            if shutil.which(binary):
                return [binary]
        return None

    def _launch_chromium_auth(self, auth_url: str) -> bool:
        """Launch Chromium with remote debugging for OAuth interception.

        Opens Chromium in app mode (no tabs/address bar) with
        ``--remote-debugging-port`` so CDP can intercept the OAuth redirect.
        Cookies persist in Chromium's default profile, so xbox.com/play
        will reuse the session for xCloud streaming.

        Returns:
            True if Chromium was launched successfully.
        """
        self._kill_chromium()  # Kill any lingering instance

        cmd = self._find_chromium_cmd()
        if not cmd:
            logger.warning("[MS] No Chromium/Chrome found for auth")
            return False

        # Use a dedicated profile dir so Chromium starts a NEW instance
        # even if another Chromium window is already open.  This ensures
        # our --remote-debugging-port is active on this process.
        auth_profile = os.path.expanduser("~/.local/share/unifideck/chromium-auth")
        os.makedirs(auth_profile, exist_ok=True)

        args = cmd + [
            f"--app={auth_url}",
            f"--remote-debugging-port={self._chromium_cdp_port}",
            f"--user-data-dir={auth_profile}",
            "--no-first-run",
            "--disable-translate",
            "--disable-infobars",
            "--disable-session-crashed-bubble",
            "--disable-features=TranslateUI",
            "--password-store=basic",
            "--disable-dev-shm-usage",
            "--disable-background-networking",
            "--start-fullscreen",
            "--window-size=1280,800",
        ]
        logger.info(f"[MS] Launching Chromium for auth: {' '.join(args[:4])}...")

        log_file = os.path.expanduser("~/.local/share/unifideck/chromium-auth.log")
        try:
            stderr_fh = open(log_file, "w")
        except Exception:
            stderr_fh = subprocess.DEVNULL

        logger.info(f"[MS] Chromium command: {' '.join(args[:6])}...")
        logger.info(f"[MS] Chromium env DISPLAY={self._clean_env().get('DISPLAY')}")

        try:
            self._chromium_process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=stderr_fh,
                env=self._clean_env(),
            )
            logger.info(f"[MS] Chromium PID: {self._chromium_process.pid}")
            # Don't block — CDP readiness is checked asynchronously
            # by _monitor_and_complete_auth which runs in a background task.
            return True
        except Exception as e:
            logger.error(f"[MS] Failed to launch Chromium: {e}", exc_info=True)
            return False

    def _kill_chromium(self) -> None:
        """Terminate the Chromium auth subprocess and any orphan instances."""
        if self._chromium_process is not None:
            try:
                self._chromium_process.terminate()
                self._chromium_process.wait(timeout=5)
                logger.info("[MS] Chromium auth browser closed")
            except Exception as e:
                logger.debug(f"[MS] Chromium kill error (non-fatal): {e}")
                try:
                    self._chromium_process.kill()
                except Exception:
                    pass
            self._chromium_process = None
        # Also kill any orphan Chromium using our auth profile
        try:
            subprocess.run(
                ["pkill", "-f", "chromium-auth"],
                capture_output=True, timeout=5,
            )
        except Exception:
            pass

    def is_chromium_installed(self) -> bool:
        """Check if Chromium or Chrome is available on the system."""
        return self._find_chromium_cmd() is not None

    async def install_chromium(self) -> Dict[str, Any]:
        """Install Chromium via flatpak.

        Runs ``flatpak install -y org.chromium.Chromium`` in the background.
        """
        import shutil
        if not shutil.which("flatpak"):
            return {"success": False, "error": "microsoft.flatpakNotFound"}
        if self.is_chromium_installed():
            return {"success": True, "message": "microsoft.chromiumAlreadyInstalled"}
        logger.info("[MS] Installing Chromium via flatpak...")
        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["flatpak", "install", "--user", "-y", "flathub", "org.chromium.Chromium"],
                    capture_output=True, timeout=300,
                    env=self._clean_env(),
                ),
            )
            if proc.returncode == 0:
                logger.info("[MS] Chromium installed successfully")
                return {"success": True, "message": "microsoft.chromiumInstalled"}
            else:
                stderr = proc.stderr.decode("utf-8", errors="replace")[:200]
                logger.error(f"[MS] Chromium install failed: {stderr}")
                return {"success": False, "error": "microsoft.chromiumInstallFailed"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "microsoft.chromiumInstallTimeout"}
        except Exception as e:
            logger.error(f"[MS] Chromium install error: {e}")
            return {"success": False, "error": "microsoft.chromiumInstallFailed"}

    async def _clear_ms_cookies(self) -> None:
        """Clear Microsoft login cookies from CEF via CDP."""
        try:
            from ..auth.browser import CDPOAuthMonitor
            monitor = CDPOAuthMonitor()
            for domain in ("login.live.com", "live.com", "microsoft.com", "login.microsoftonline.com"):
                await monitor.clear_cookies_for_domain(domain)
        except Exception as e:
            logger.debug(f"[MS] Cookie clear (non-fatal): {e}")

    async def start_auth(self) -> Dict[str, Any]:
        """Launch Chromium with the OAuth URL and start CDP monitoring.

        Chromium is used instead of Steam's CEF browser so that login
        cookies persist — xbox.com/play reuses the session for xCloud.
        CDP intercepts the OAuth redirect on the Chromium debugging port.

        Returns:
            Dict with ``success=True``.  No ``url`` is returned because
            Chromium handles the browser window directly.
        """
        auth_url = (
            f"{self._get_auth_url()}"
            f"?client_id={self._get_client_id()}"
            f"&redirect_uri={urllib.parse.quote(self._get_redirect_uri())}"
            f"&response_type=code"
            f"&scope={urllib.parse.quote(self._get_scope())}"
        )
        self._pending_auth_url = auth_url

        # Check if Chromium is available
        if not self.is_chromium_installed():
            logger.info("[MS] Chromium not installed — prompting user to install")
            return {
                "success": True,
                "needs_chromium": True,
                "message": "microsoft.chromiumRequired",
            }

        # Launch Chromium with remote debugging for CDP interception
        launched = self._launch_chromium_auth(auth_url)
        if not launched:
            logger.error("[MS] Failed to launch Chromium for auth")
            return {
                "success": False,
                "error": "microsoft.chromiumInstallFailed",
            }

        # Start CDP monitor targeting Chromium's debugging port
        if hasattr(self, "_auth_monitor_task") and self._auth_monitor_task and not self._auth_monitor_task.done():
            self._auth_monitor_task.cancel()
        self._auth_monitor_task = asyncio.create_task(self._monitor_and_complete_auth())

        return {
            "success":        True,
            "chromium_auth":  True,
            "message":        "microsoft.signInMessage",
        }

    async def complete_auth(self, auth_code: str) -> Dict[str, Any]:
        """Exchange the OAuth code for MS tokens and persist them."""
        try:
            token_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: http_post(
                    self._get_token_url(),
                    {
                        "client_id":    self._get_client_id(),
                        "redirect_uri": self._get_redirect_uri(),
                        "code":         auth_code,
                        "grant_type":   "authorization_code",
                        "scope":        self._get_scope(),
                    },
                    {"Content-Type": "application/x-www-form-urlencoded"},
                ),
            )
            if "access_token" not in token_data:
                return {"success": False, "error": "Token exchange failed: " + str(token_data)}

            self._ms_access_token  = token_data["access_token"]
            self._ms_refresh_token = token_data.get("refresh_token", "")
            self._token_saved_at   = time.time()
            self._save_tokens()

            logger.info("[MS] ✓ Authentication complete")
            return {"success": True, "message": "microsoft.accountConnected"}

        except Exception as e:
            logger.error(f"[MS] complete_auth error: {e}", exc_info=True)
            return {"success": False, "error": str(e)}

    async def logout(self) -> Dict[str, Any]:
        """Clear stored tokens and browser cookies."""
        self._ms_access_token  = None
        self._ms_refresh_token = None
        self._xsts_token       = None
        self._user_hash        = None
        self._xuid             = None
        try:
            if os.path.exists(self._get_token_file()):
                os.remove(self._get_token_file())
        except Exception as e:
            logger.warning(f"[MS] Could not remove token file: {e}")

        await self._clear_ms_cookies()

        return {"success": True, "message": "microsoft.loggedOut"}

    # ── Library sync ─────────────────────────────────────────────────────

    async def get_library(self) -> List[Game]:
        """Fetch xCloud-playable games from the public catalog.

        Flow:
          1. Refresh tokens and build XBL/XSTS chain.
          2. Fetch the full xCloud catalog (public API, ~500+ games).
          3. Batch-query displaycatalog for game titles.
          4. Return Game objects tagged "xcloud" (launchable via browser).

        Note: subscription validation is handled by xbox.com/play when
        the user clicks "Play on Cloud" — no client-side check needed.
        """

        if not await self.is_available():
            if not os.path.exists(self._get_token_file()):
                logger.error(
                    "[MS] Not authenticated — token file does not exist. "
                    "Authenticate via Quick Access Menu → Unifideck → Microsoft."
                )
            else:
                try:
                    with open(self._get_token_file()) as f:
                        data = json.load(f)
                    has_refresh = bool(data.get("refresh_token"))
                    logger.error(
                        f"[MS] Not authenticated — token file exists but "
                        f"refresh_token={'present' if has_refresh else 'MISSING'}. "
                        f"Re-authenticate to fix."
                    )
                except Exception as e:
                    logger.error(f"[MS] Not authenticated — token file unreadable: {e}")
            return []

        try:
            # ── 1. Refresh MS access token if stale ──────────────────────
            token_ok = await self._ensure_fresh_ms_token()
            if not token_ok:
                logger.error("[MS] Session expired — re-authenticate via Unifideck → Microsoft.")
                return []

            # ── 2. XBL / XSTS token chain ────────────────────────────────
            ok = await asyncio.get_event_loop().run_in_executor(
                None, self._build_xbl_chain
            )
            if not ok:
                logger.warning("[MS] Could not build XBL/XSTS token chain")

            # ── 3. Fetch xCloud catalog ───────────────────────────────
            xcloud_ids = await asyncio.get_event_loop().run_in_executor(
                None, self._fetch_xcloud_catalog
            )
            if not xcloud_ids:
                logger.warning("[MS] xCloud catalog is empty or unreachable")
                return []

            # ── 5. Batch-resolve titles from displaycatalog ──────────────
            titles = await asyncio.get_event_loop().run_in_executor(
                None, lambda: self._batch_get_titles(xcloud_ids)
            )

            # ── 6. Build Game objects ────────────────────────────────────
            games: List[Game] = []
            for pid in xcloud_ids:
                title = titles.get(pid, pid)
                game = Game(
                    id=pid,
                    title=title,
                    store="microsoft",
                    is_installed=False,
                    store_tags=["xcloud"],
                )
                games.append(game)

            logger.info(f"[MS] Returning {len(games)} xCloud games")
            return games

        except Exception as e:
            logger.error(f"[MS] Error fetching library: {e}", exc_info=True)
            return []

    # ── Token management ─────────────────────────────────────────────────

    def _load_tokens(self) -> None:
        """Load persisted OAuth tokens from self._get_token_file() into memory."""
        try:
            if os.path.exists(self._get_token_file()):
                with open(self._get_token_file()) as f:
                    data = json.load(f)
                self._ms_access_token  = data.get("access_token")
                self._ms_refresh_token = data.get("refresh_token")
                self._token_saved_at   = data.get("saved_at", 0.0)
                logger.info("[MS] Loaded tokens from disk")
        except Exception as e:
            logger.warning(f"[MS] Could not load tokens: {e} from disk")

    def _save_tokens(self) -> None:
        """Persist tokens to disk with restricted permissions (0o600)."""
        try:
            os.makedirs(os.path.dirname(self._get_token_file()), exist_ok=True)
            fd = os.open(self._get_token_file(), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w") as f:
                json.dump(
                    {
                        "access_token":  self._ms_access_token,
                        "refresh_token": self._ms_refresh_token,
                        "saved_at":      self._token_saved_at,
                        "scope":         self._get_scope(),
                    },
                    f,
                )
        except Exception as e:
            logger.warning(f"[MS] Could not save tokens: {e}")

    async def _ensure_fresh_ms_token(self) -> bool:
        """Proactively refresh the MS access token if it is near expiry.

        Returns True if the token is usable (fresh or successfully refreshed).
        Returns False and auto-logs-out if the session is unrecoverable
        (missing refresh_token or Microsoft rejected it).
        """
        age = time.time() - self._token_saved_at
        if age < self._get_token_refresh_threshold():
            return True
        if not self._ms_refresh_token:
            logger.error("[MS] No refresh token — session expired. Logging out.")
            await self.logout()
            return False
        try:
            logger.info(f"[MS] Refreshing MS access token (age={age:.0f}s)")
            token_data = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: http_post(
                    self._get_token_url(),
                    {
                        "client_id":     self._get_client_id(),
                        "redirect_uri":  self._get_redirect_uri(),
                        "refresh_token": self._ms_refresh_token,
                        "grant_type":    "refresh_token",
                        "scope":         self._get_scope(),
                    },
                    {"Content-Type": "application/x-www-form-urlencoded"},
                ),
            )
            if "access_token" in token_data:
                self._ms_access_token  = token_data["access_token"]
                self._ms_refresh_token = token_data.get("refresh_token", self._ms_refresh_token)
                self._token_saved_at   = time.time()
                self._save_tokens()
                logger.info("[MS] ✓ Access token refreshed")
                return True

            error_code = token_data.get("error", "unknown")
            logger.error(f"[MS] Token refresh rejected ({error_code}). Logging out.")
            await self.logout()
            return False

        except Exception as e:
            logger.error(f"[MS] Token refresh error: {e}", exc_info=True)
            return False

    # ── XBL token chain ──────────────────────────────────────────────────

    def _build_xbl_chain(self) -> bool:
        """Delegate to the pure function in microsoft_auth.

        Reads XBL/XSTS endpoint URLs from settings.json and passes them
        as explicit parameters to the pure function.
        """
        self._xsts_token = None
        self._user_hash  = None

        result = build_xbl_chain(
            self._ms_access_token,
            self._get_locale(),
            xbl_auth_url=self._get_xbl_auth_url(),
            xsts_url=self._get_xsts_url(),
            xbl_user_agent=self._get_xbl_user_agent(),
        )
        if result is None:
            return False

        self._user_hash  = result["user_hash"]
        self._xsts_token = result["xsts_token"]
        self._xuid       = result["xuid"]
        return True

    # ── Title Hub API (synchronous, run in executor) ─────────────────────


    # ── xCloud / Game Pass ───────────────────────────────────────────────

    def _fetch_xcloud_catalog(self) -> List[str]:
        """Fetch the list of product IDs available on Xbox Cloud Gaming.

        Uses the public Game Pass catalog API — no auth required.

        Returns:
            List of product IDs (BigIds) playable via xCloud.
        """
        catalog_url = self._get_gamepass_catalog_url()
        catalog_id  = self._get_xcloud_catalog_id()
        url = (
            f"{catalog_url}?id={catalog_id}"
            f"&language={self._get_locale()}"
            f"&market={self._get_market()}"
        )
        try:
            data = http_get(url, {"User-Agent": self._get_catalog_user_agent()})
            # First entry is catalog metadata, rest are game entries
            ids = [item["id"] for item in data if item.get("id")]
            logger.info(f"[MS] xCloud catalog: {len(ids)} games available")
            return ids
        except Exception as e:
            logger.error(f"[MS] Failed to fetch xCloud catalog: {e}")
            return []

    def _batch_get_titles(self, product_ids: List[str]) -> Dict[str, str]:
        """Batch-fetch game titles from the displaycatalog API.

        Args:
            product_ids: List of product IDs (BigIds) to look up.

        Returns:
            Dict mapping productId → title string.
        """
        result: Dict[str, str] = {}
        batch_size = 20

        for i in range(0, len(product_ids), batch_size):
            batch = product_ids[i: i + batch_size]
            ids_param = ",".join(batch)
            url = (
                f"{self._get_product_url()}"
                f"?bigIds={ids_param}"
                f"&market={self._get_market()}"
                f"&languages={self._get_locale()}"
                f"&fieldsTemplate=Browse"
            )
            try:
                data = http_get(url, {
                    "Accept":     "application/json",
                    "User-Agent": self._get_catalog_user_agent(),
                    "MS-CV":      "unifideck.xcloud",
                })
                for product in data.get("Products", []):
                    pid   = product.get("ProductId", "")
                    title = ""
                    for loc in product.get("LocalizedProperties", []):
                        title = loc.get("ProductTitle", "")
                        if title:
                            break
                    if pid and title:
                        result[pid] = title
            except Exception as e:
                logger.warning(
                    f"[MS] xCloud title batch {i // batch_size} failed: {e}"
                )

        logger.info(f"[MS] Resolved {len(result)} titles from {len(product_ids)} product IDs")
        return result


    async def uninstall_game(self, game_id: str) -> dict:
        """No-op — xCloud games are streamed, not installed locally."""
        return {"success": True, "message": "microsoft.xcloudNotInstalled"}


    async def _inject_virtual_keyboard(self) -> None:
        """Inject a touch-friendly virtual keyboard into the auth page via CDP.

        Steam's overlay keyboard is not available because Chromium auth
        runs outside of Steam.  This injects a minimal on-screen keyboard
        via ``Page.addScriptToEvaluateOnNewDocument`` so it persists across
        page navigations (email → password → 2FA).
        """
        try:
            import websockets
        except ImportError:
            logger.warning("[MS] websockets not available — no virtual keyboard")
            return

        import urllib.request as _req

        # Find a page to connect to
        try:
            with _req.urlopen(
                f"http://127.0.0.1:{self._chromium_cdp_port}/json", timeout=3
            ) as r:
                pages = json.loads(r.read().decode())
            ws_url = None
            for page in pages:
                ws_url = page.get("webSocketDebuggerUrl")
                if ws_url:
                    break
            if not ws_url:
                logger.warning("[MS] No CDP page found for keyboard injection")
                return
        except Exception as e:
            logger.warning(f"[MS] CDP keyboard: cannot list pages: {e}")
            return

        # JavaScript virtual keyboard — auto-shows on input focus
        kb_js = r"""
(function() {
  if (window.__unifideck_kb) return;
  window.__unifideck_kb = true;

  var KEYS = [
    ['1','2','3','4','5','6','7','8','9','0'],
    ['q','w','e','r','t','y','u','i','o','p'],
    ['a','s','d','f','g','h','j','k','l'],
    ['z','x','c','v','b','n','m','@','.'],
    ['SHIFT','SPACE','BACK','ENTER']
  ];

  var shifted = false;
  var target = null;

  var overlay = document.createElement('div');
  overlay.id = 'unifideck-kb';
  overlay.style.cssText = 'position:fixed;bottom:0;left:0;right:0;z-index:999999;' +
    'background:#1a1a2e;padding:6px;display:none;touch-action:manipulation;' +
    'border-top:2px solid #3a3a5a;';

  function type(ch) {
    if (!target) return;
    target.focus();
    if (ch === 'BACK') {
      var s = target.selectionStart || 0;
      if (s > 0) {
        var v = target.value;
        target.value = v.slice(0, s-1) + v.slice(s);
        target.selectionStart = target.selectionEnd = s - 1;
      }
    } else if (ch === 'SPACE') {
      document.execCommand('insertText', false, ' ');
    } else if (ch === 'ENTER') {
      var ev = new KeyboardEvent('keydown', {key:'Enter',code:'Enter',keyCode:13,bubbles:true});
      target.dispatchEvent(ev);
      var form = target.closest('form');
      if (form) form.dispatchEvent(new Event('submit',{bubbles:true}));
    } else if (ch === 'SHIFT') {
      shifted = !shifted;
      render();
      return;
    } else {
      var c = shifted ? ch.toUpperCase() : ch;
      document.execCommand('insertText', false, c);
    }
    target.dispatchEvent(new Event('input', {bubbles:true}));
    target.dispatchEvent(new Event('change', {bubbles:true}));
  }

  function render() {
    overlay.innerHTML = '';
    KEYS.forEach(function(row) {
      var r = document.createElement('div');
      r.style.cssText = 'display:flex;justify-content:center;gap:3px;margin:3px 0;';
      row.forEach(function(k) {
        var b = document.createElement('button');
        var label = k;
        if (k === 'SPACE') label = '⎵';
        else if (k === 'BACK') label = '⌫';
        else if (k === 'ENTER') label = '↵';
        else if (k === 'SHIFT') label = shifted ? '⬆' : '⇧';
        else label = shifted ? k.toUpperCase() : k;
        b.textContent = label;
        var wide = (k==='SPACE') ? 'flex:3;' : (k.length > 1) ? 'flex:1.5;' : '';
        b.style.cssText = wide + 'min-width:32px;height:42px;font-size:16px;' +
          'border:1px solid #3a3a5a;border-radius:4px;color:#e0e0e0;' +
          'background:' + (k==='SHIFT' && shifted ? '#4a4a7a' : '#2a2a4a') + ';' +
          'touch-action:manipulation;-webkit-tap-highlight-color:transparent;';
        b.addEventListener('touchstart', function(e) {
          e.preventDefault();
          type(k);
        }, {passive:false});
        b.addEventListener('mousedown', function(e) {
          e.preventDefault();
          type(k);
        });
        r.appendChild(b);
      });
      overlay.appendChild(r);
    });
  }

  render();
  document.body.appendChild(overlay);

  document.addEventListener('focusin', function(e) {
    var tag = e.target.tagName;
    var type = (e.target.type || '').toLowerCase();
    if (tag === 'INPUT' && type !== 'hidden' && type !== 'checkbox' && type !== 'radio'
        || tag === 'TEXTAREA') {
      target = e.target;
      overlay.style.display = 'block';
    }
  }, true);

  document.addEventListener('focusout', function(e) {
    setTimeout(function() {
      if (!document.activeElement || document.activeElement === document.body) {
        overlay.style.display = 'none';
        target = null;
      }
    }, 200);
  }, true);
})();
"""

        try:
            async with websockets.connect(ws_url, close_timeout=3) as ws:
                # Inject on every new page (survives navigation)
                await ws.send(json.dumps({
                    "id": 9001,
                    "method": "Page.addScriptToEvaluateOnNewDocument",
                    "params": {"source": kb_js},
                }))
                await asyncio.wait_for(ws.recv(), timeout=3)

                # Also inject now for the current page
                await ws.send(json.dumps({
                    "id": 9002,
                    "method": "Runtime.evaluate",
                    "params": {"expression": kb_js},
                }))
                await asyncio.wait_for(ws.recv(), timeout=3)

            logger.info("[MS] Virtual keyboard injected via CDP")
        except Exception as e:
            logger.warning(f"[MS] CDP keyboard injection failed: {e}")

    async def _monitor_and_complete_auth(self) -> None:
        """Background task: intercept the OAuth redirect via CDP Network events."""
        # Use Chromium's debugging port if Chromium is running, else CEF (8080)
        cdp_port = self._chromium_cdp_port if self._chromium_process else 8080

        # Give Chromium a moment to start and open its CDP port (non-blocking)
        if self._chromium_process:
            await asyncio.sleep(2)
            if self._chromium_process.poll() is not None:
                log_file = os.path.expanduser("~/.local/share/unifideck/chromium-auth.log")
                err = ""
                try:
                    with open(log_file) as f:
                        err = f.read()[:300]
                except Exception:
                    pass
                logger.error(f"[MS] Chromium crashed before CDP. stderr: {err}")
                self._chromium_process = None
                return

        # Inject virtual keyboard (Steam overlay not available outside Steam)
        await self._inject_virtual_keyboard()

        try:
            code = await intercept_oauth_code(
                pending_auth_url=getattr(self, "_pending_auth_url", ""),
                timeout=300,
                cdp_port=cdp_port,
            )
            if code:
                logger.info("[MS] ✓ Received OAuth code via Network interception")
                result = await self.complete_auth(code)
                if result["success"]:
                    logger.info("[MS] ✓ Authentication completed")
                else:
                    logger.error(f"[MS] complete_auth failed: {result.get('error')}")
            else:
                logger.warning("[MS] Network interception timed out — no code received")
        except Exception as e:
            logger.error(f"[MS] Auth monitor error: {e}", exc_info=True)
        finally:
            # Close Chromium auth window after auth completes or times out
            self._kill_chromium()
