"""
Chromium browser management for the Microsoft xCloud connector.

Handles launching, finding, killing, and installing Chromium on Steam Deck.
Manages the shared browser profile (``~/.local/share/unifideck/chromium-auth``),
environment variables needed for GUI apps under PluginLoader, and the virtual
keyboard injected via CDP on the auth page.

This module has no knowledge of OAuth, tokens, or the xCloud catalog — it
only manages the browser lifecycle.  The connector (``microsoft.py``) owns
the auth flow and delegates browser operations here.

Usage in ``microsoft.py``::

    from .microsoft_chromium import ChromiumBrowser
    self._browser = ChromiumBrowser(cdp_port=9222, locale_fn=self._get_locale)
    launched = self._browser.launch_auth(auth_url)
"""

import asyncio
import glob
import json
import logging
import os
import pathlib
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional

logger = logging.getLogger(__name__)

# ── Constants ─────────────────────────────────────────────────────────────────

PROFILE_DIR = os.path.expanduser("~/.local/share/unifideck/chromium-auth")
LOG_FILE    = os.path.expanduser("~/.local/share/unifideck/chromium-auth.log")

# Flatpak app IDs to search, in priority order
_FLATPAK_APPS = ("org.chromium.Chromium", "com.google.Chrome")

# Native binary names to search if no flatpak found
_NATIVE_BINS = ("chromium", "chromium-browser", "google-chrome")

# Domains whose cookies are cleared on logout
_MS_COOKIE_DOMAINS = (
    "%xbox.com%", "%microsoft.com%", "%live.com%", "%microsoftonline.com%",
)

# Chromium flags shared between auth and game launch
_BASE_FLAGS = [
    "--no-first-run",
    "--disable-translate",
    "--disable-infobars",
    "--disable-session-crashed-bubble",
    "--disable-features=TranslateUI",
    "--password-store=basic",
    "--disable-dev-shm-usage",
    "--disable-background-networking",
]


# ── Environment helpers ───────────────────────────────────────────────────────

def clean_env() -> dict:
    """Return a clean environment for launching Chromium/flatpak.

    - Strips ``LD_LIBRARY_PATH`` / ``LD_PRELOAD`` (PluginLoader bundles
      its own OpenSSL which conflicts with system libraries).
    - Detects the correct ``DISPLAY`` for gaming mode (gamescope) vs desktop.
    - Injects ``XDG_RUNTIME_DIR``, ``DBUS_SESSION_BUS_ADDRESS``
      (PluginLoader is a systemd service without a display session).
    - Finds the real ``XAUTHORITY`` file (SteamOS uses a randomly-named
      file in ``/run/user/<uid>/``).
    - Clears ``GTK_MODULES`` to suppress canberra-gtk-module warnings.
    """
    home = str(pathlib.Path.home())
    uid = os.stat(home).st_uid
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")
    }
    # Gaming mode: gamescope uses a different DISPLAY than :0
    if "DISPLAY" not in env:
        # Check for gamescope nested display
        gamescope_display = os.environ.get("GAMESCOPE_WAYLAND_DISPLAY")
        if gamescope_display:
            env["DISPLAY"] = ":1"  # gamescope typically exposes :1
        else:
            env["DISPLAY"] = ":0"
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("DBUS_SESSION_BUS_ADDRESS", f"unix:path=/run/user/{uid}/bus")
    # XAUTHORITY: SteamOS uses a randomly-named file in /run/user/<uid>/
    if "XAUTHORITY" not in env:
        xauth_files = glob.glob(f"/run/user/{uid}/xauth_*")
        if xauth_files:
            env["XAUTHORITY"] = xauth_files[0]
        elif os.path.exists(os.path.join(home, ".Xauthority")):
            env["XAUTHORITY"] = os.path.join(home, ".Xauthority")
    env["GTK_MODULES"] = ""
    return env


# ── ChromiumBrowser ───────────────────────────────────────────────────────────

class ChromiumBrowser:
    """Manages Chromium for Microsoft auth and xCloud on Steam Deck.

    Attributes:
        cdp_port:  CDP remote debugging port (default 9222).
        locale_fn: Callable that returns the BCP-47 locale string.
        process:   The ``subprocess.Popen`` handle, or ``None``.
    """

    def __init__(
        self,
        cdp_port: int = 9222,
        locale_fn: Optional[Callable[[], str]] = None,
    ):
        self.cdp_port  = cdp_port
        self.locale_fn = locale_fn or (lambda: "en-US")
        self.process: Optional[subprocess.Popen] = None

    # ── Detection & install ──────────────────────────────────────────────

    def find_cmd(self) -> Optional[List[str]]:
        """Find an available Chromium/Chrome command.

        Checks both ``--user`` and ``--system`` flatpak installations,
        then falls back to native binaries.

        Returns:
            Command as a list (for subprocess), or ``None``.
        """
        if shutil.which("flatpak"):
            for app_id in _FLATPAK_APPS:
                try:
                    for flag in ("--user", "--system"):
                        result = subprocess.run(
                            ["flatpak", "info", flag, app_id],
                            capture_output=True, timeout=5,
                            env=clean_env(),
                        )
                        if result.returncode == 0:
                            return ["flatpak", "run", app_id]
                except Exception:
                    pass
        for binary in _NATIVE_BINS:
            if shutil.which(binary):
                return [binary]
        return None

    @property
    def is_installed(self) -> bool:
        """Return True if Chromium or Chrome is available."""
        return self.find_cmd() is not None

    async def install(self) -> Dict[str, Any]:
        """Install Chromium via flatpak (``--user``).

        Returns:
            Dict with ``success`` and ``message`` or ``error`` keys.
        """
        if not shutil.which("flatpak"):
            return {"success": False, "error": "microsoft.flatpakNotFound"}
        if self.is_installed:
            return {"success": True, "message": "microsoft.chromiumAlreadyInstalled"}

        logger.info("[MS] Installing Chromium via flatpak...")
        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    ["flatpak", "install", "--user", "-y",
                     "flathub", "org.chromium.Chromium"],
                    capture_output=True, timeout=300,
                    env=clean_env(),
                ),
            )
            if proc.returncode == 0:
                logger.info("[MS] Chromium installed successfully")
                return {"success": True, "message": "microsoft.chromiumInstalled"}
            stderr = proc.stderr.decode("utf-8", errors="replace")[:200]
            logger.error(f"[MS] Chromium install failed: {stderr}")
            return {"success": False, "error": "microsoft.chromiumInstallFailed"}
        except subprocess.TimeoutExpired:
            return {"success": False, "error": "microsoft.chromiumInstallTimeout"}
        except Exception as e:
            logger.error(f"[MS] Chromium install error: {e}")
            return {"success": False, "error": "microsoft.chromiumInstallFailed"}

    # ── Launch / kill ────────────────────────────────────────────────────

    def launch_auth(self, auth_url: str) -> bool:
        """Launch Chromium for OAuth with remote debugging.

        Opens Chromium in fullscreen app mode with our CDP port.
        Uses a dedicated profile so a new instance is always created
        (even if another Chromium window is already open).

        Returns:
            True if Chromium was launched successfully.
        """
        self.kill()

        cmd = self.find_cmd()
        if not cmd:
            logger.warning("[MS] No Chromium/Chrome found for auth")
            return False

        os.makedirs(PROFILE_DIR, exist_ok=True)

        args = cmd + [
            f"--app={auth_url}",
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={PROFILE_DIR}",
        ] + _BASE_FLAGS + [
            "--start-fullscreen",
            "--enable-touch-events",
            "--window-size=1280,800",
            f"--lang={self.locale_fn().split('-')[0]}",
        ]

        logger.info(f"[MS] Launching Chromium: {' '.join(args[:6])}...")
        logger.info(f"[MS] Chromium env DISPLAY={clean_env().get('DISPLAY')}")

        stderr_fh = None
        try:
            stderr_fh = open(LOG_FILE, "w")
        except Exception:
            pass

        try:
            self.process = subprocess.Popen(
                args,
                stdout=subprocess.DEVNULL,
                stderr=stderr_fh if stderr_fh else subprocess.DEVNULL,
                env=clean_env(),
            )
            logger.info(f"[MS] Chromium PID: {self.process.pid}")
            return True
        except Exception as e:
            logger.error(f"[MS] Failed to launch Chromium: {e}", exc_info=True)
            return False
        finally:
            if stderr_fh is not None:
                stderr_fh.close()

    def kill(self) -> None:
        """Gracefully terminate the Chromium auth process.

        Waits 1 s before SIGTERM so cookies can flush to disk.
        Does NOT pkill by profile name — that would kill game sessions
        launched by the launcher sharing the same profile.
        """
        if self.process is None:
            return
        try:
            import time
            time.sleep(1)
            self.process.terminate()
            self.process.wait(timeout=10)
            logger.info("[MS] Chromium auth closed (cookies flushed)")
        except subprocess.TimeoutExpired:
            logger.debug("[MS] Chromium didn't exit — sending SIGKILL")
            try:
                self.process.kill()
                self.process.wait(timeout=3)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[MS] Chromium kill error (non-fatal): {e}")
        self.process = None

    # ── Cookie management ────────────────────────────────────────────────

    @staticmethod
    def has_xbox_session() -> bool:
        """Return True if xbox.com cookies exist in the Chromium profile.

        Returns True on error (assume logged in).
        Returns True if profile does not exist yet (no logout detected).
        """
        cookie_db = os.path.join(PROFILE_DIR, "Default", "Cookies")
        if not os.path.exists(cookie_db):
            return True
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as tmp:
                tmp_path = tmp.name
            shutil.copy2(cookie_db, tmp_path)
            conn = sqlite3.connect(tmp_path, timeout=5)
            try:
                cursor = conn.execute(
                    "SELECT COUNT(*) FROM cookies "
                    "WHERE host_key LIKE '%xbox.com%'"
                )
                count = cursor.fetchone()[0]
                return count > 0
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[MS] Could not read cookie DB: {e}")
            return True
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    @staticmethod
    def clear_cookies() -> None:
        """Delete Xbox / Microsoft cookies from the Chromium profile."""
        cookie_db = os.path.join(PROFILE_DIR, "Default", "Cookies")
        if not os.path.exists(cookie_db):
            return
        try:
            conn = sqlite3.connect(cookie_db, timeout=5)
            try:
                for pattern in _MS_COOKIE_DOMAINS:
                    conn.execute(
                        "DELETE FROM cookies WHERE host_key LIKE ?", (pattern,)
                    )
                conn.commit()
                logger.info("[MS] Cleared Xbox/MS cookies from Chromium profile")
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[MS] Could not clear Chromium cookies: {e}")

    # ── CDP helpers ──────────────────────────────────────────────────────

    async def inject_virtual_keyboard(self) -> None:
        """Inject the Unifideck virtual keyboard into the auth page via CDP.

        Uses ``Page.addScriptToEvaluateOnNewDocument`` so the keyboard
        persists across page navigations (email → password → 2FA).
        """
        try:
            import websockets
        except ImportError:
            logger.warning("[MS] websockets not available — no virtual keyboard")
            return

        import urllib.request as _req

        try:
            with _req.urlopen(
                f"http://127.0.0.1:{self.cdp_port}/json", timeout=3
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

        from ..utils.virtual_keyboard import get_keyboard_js
        locale = self.locale_fn()
        # Sanitize locale to prevent JS injection
        import re as _re
        if not _re.match(r'^[a-zA-Z]{2}(-[a-zA-Z]{2,4})?$', locale):
            locale = "en-US"
        kb_js = get_keyboard_js(locale)

        # Inject locale as a window variable FIRST, then the keyboard.
        # This ensures the locale is available even if the string
        # replacement in get_keyboard_js() is somehow not picked up.
        locale_script = f"window.__unifideck_locale = '{locale}';"
        logger.info(f"[MS] Injecting keyboard with locale={locale}")

        try:
            async with websockets.connect(ws_url, close_timeout=3) as ws:
                # 1. Set locale variable for all future documents
                await ws.send(json.dumps({
                    "id": 9000,
                    "method": "Page.addScriptToEvaluateOnNewDocument",
                    "params": {"source": locale_script},
                }))
                await asyncio.wait_for(ws.recv(), timeout=3)

                # 2. Set locale variable on the current page NOW
                await ws.send(json.dumps({
                    "id": 9001,
                    "method": "Runtime.evaluate",
                    "params": {"expression": locale_script},
                }))
                await asyncio.wait_for(ws.recv(), timeout=3)

                # 3. Register keyboard for future page loads
                await ws.send(json.dumps({
                    "id": 9002,
                    "method": "Page.addScriptToEvaluateOnNewDocument",
                    "params": {"source": kb_js},
                }))
                await asyncio.wait_for(ws.recv(), timeout=3)

                # 4. Inject keyboard on the current page NOW
                await ws.send(json.dumps({
                    "id": 9003,
                    "method": "Runtime.evaluate",
                    "params": {"expression": kb_js},
                }))
                await asyncio.wait_for(ws.recv(), timeout=3)

            logger.info("[MS] Virtual keyboard injected via CDP")
        except Exception as e:
            logger.warning(f"[MS] CDP keyboard injection failed: {e}")

    async def wait_and_check_crash(self) -> bool:
        """Wait for Chromium to start, return False if it crashed.

        Called at the start of the auth monitor task.  Polls every 0.5 s
        for up to 10 s to allow Chromium time to start on loaded systems.
        """
        if not self.process:
            return False
        for _ in range(20):  # 20 * 0.5 s = 10 s max
            await asyncio.sleep(0.5)
            if self.process.poll() is not None:
                err = ""
                try:
                    with open(LOG_FILE) as f:
                        err = f.read()[:300]
                except Exception:
                    pass
                logger.error(f"[MS] Chromium crashed before CDP. stderr: {err}")
                self.process = None
                return False
            # Check if CDP port is responsive
            try:
                import urllib.request
                with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=1
                ):
                    return True
            except Exception:
                continue
        logger.warning("[MS] Chromium started but CDP port not responding after 10 s")
        return True  # process is alive, let caller retry CDP
