"""
Chromium browser management for the Microsoft xCloud connector.

Handles launching, finding, killing, and installing Chromium on Steam Deck.
Manages the shared browser profile (``~/.local/share/unifideck/chromium-auth``),
environment variables needed for GUI apps under PluginLoader, and browser
lifecycle for the auth page.

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
import signal
import shutil
import sqlite3
import subprocess
import tempfile
from typing import Any, Callable, Dict, List, Optional

from ..cdp.page_inject import inject_scripts  # noqa: F401 – kept for future CDP use

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

_SESSION_ENV_KEYS = (
    "DISPLAY",
    "WAYLAND_DISPLAY",
    "GAMESCOPE_WAYLAND_DISPLAY",
    "DBUS_SESSION_BUS_ADDRESS",
    "DESKTOP_SESSION",
    "GTK_IM_MODULE",
    "QT_IM_MODULE",
    "XAUTHORITY",
    "XDG_RUNTIME_DIR",
    "XMODIFIERS",
    "XDG_SESSION_TYPE",
    "XDG_CURRENT_DESKTOP",
)


def _detect_session_env(uid: int, home: str) -> Dict[str, str]:
    """Detect the active graphical session env for Steam / gamescope.

    Decky's backend often runs as a service without the real gaming-mode
    display variables.  Mirror Ubisoft's proven strategy: seed from our own
    env, then fill missing values from a live Steam/gamescope process.
    """
    result: Dict[str, str] = {}

    for key in _SESSION_ENV_KEYS:
        value = os.environ.get(key)
        if value:
            result[key] = value

    runtime_dir = f"/run/user/{uid}"
    gamescope_env = os.path.join(runtime_dir, "gamescope-environment")
    if os.path.exists(gamescope_env):
        try:
            with open(gamescope_env, "r", encoding="utf-8", errors="replace") as f:
                for raw_line in f:
                    line = raw_line.strip()
                    if not line or "=" not in line:
                        continue
                    key, value = line.split("=", 1)
                    if key in _SESSION_ENV_KEYS and key not in result and value:
                        result[key] = value
        except OSError:
            pass

    try:
        for proc_name in ("steam", "gamescope-session", "gamescope"):
            pids = subprocess.run(
                ["pgrep", "-u", str(uid), "-x", proc_name],
                capture_output=True,
                text=True,
                timeout=5,
            ).stdout.strip().split("\n")
            for pid in pids:
                pid = pid.strip()
                if not pid:
                    continue
                try:
                    with open(f"/proc/{pid}/environ", "rb") as f:
                        env_bytes = f.read()
                    for entry in env_bytes.split(b"\x00"):
                        decoded = entry.decode("utf-8", errors="replace")
                        if "=" not in decoded:
                            continue
                        key, value = decoded.split("=", 1)
                        if key in _SESSION_ENV_KEYS and key not in result and value:
                            result[key] = value
                    if result.get("DISPLAY") or result.get("WAYLAND_DISPLAY"):
                        logger.info(
                            f"[MS] Session env detected from PID {pid} ({proc_name}): "
                            f"DISPLAY={result.get('DISPLAY')} "
                            f"WAYLAND_DISPLAY={result.get('WAYLAND_DISPLAY')}"
                        )
                        return result
                except (PermissionError, FileNotFoundError, OSError):
                    continue
    except Exception as e:
        logger.debug(f"[MS] Session env detection error: {e}")

    if not result.get("DISPLAY") and not result.get("WAYLAND_DISPLAY"):
        result["DISPLAY"] = ":0"
    if not result.get("XDG_RUNTIME_DIR"):
        result["XDG_RUNTIME_DIR"] = runtime_dir
    if (
        "DBUS_SESSION_BUS_ADDRESS" not in result
        and os.path.exists(f"{runtime_dir}/bus")
    ):
        result["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={runtime_dir}/bus"
    if "XAUTHORITY" not in result:
        xauth_files = glob.glob(f"{runtime_dir}/xauth_*")
        if xauth_files:
            result["XAUTHORITY"] = xauth_files[0]
        elif os.path.exists(os.path.join(home, ".Xauthority")):
            result["XAUTHORITY"] = os.path.join(home, ".Xauthority")

    if (
        not result.get("WAYLAND_DISPLAY")
        and result.get("GAMESCOPE_WAYLAND_DISPLAY")
        and result.get("XDG_RUNTIME_DIR")
    ):
        gamescope_socket = os.path.join(
            result["XDG_RUNTIME_DIR"], result["GAMESCOPE_WAYLAND_DISPLAY"]
        )
        if os.path.exists(gamescope_socket):
            result["WAYLAND_DISPLAY"] = result["GAMESCOPE_WAYLAND_DISPLAY"]

    if result.get("GTK_IM_MODULE") == "Steam" and not result.get("XMODIFIERS"):
        result["XMODIFIERS"] = "@im=Steam"

    return result


def clean_env() -> dict:
    """Return a clean environment for launching Chromium/flatpak.

    - Strips ``LD_LIBRARY_PATH`` / ``LD_PRELOAD``.
    - Detects the real Steam/gamescope session env when Decky lacks it.
    - Seeds Steam window env defaults so gaming mode can surface the window.
    - Clears ``GTK_MODULES`` to suppress canberra-gtk-module warnings.
    """
    home = str(pathlib.Path.home())
    uid = os.stat(home).st_uid
    env = {
        k: v for k, v in os.environ.items()
        if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD")
    }
    env.update(_detect_session_env(uid, home))
    env.setdefault("XDG_RUNTIME_DIR", f"/run/user/{uid}")
    env.setdefault("SteamGameId", "0")
    env.setdefault("STEAM_COMPAT_APP_ID", "0")
    env.setdefault("SteamAppId", "0")
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

    def is_running(self) -> bool:
        """Return True when the auth Chromium instance is still alive."""
        if self.process is not None and self.process.poll() is None:
            return True
        return self._get_browser_ws_url() is not None

    def _singleton_paths(self) -> List[str]:
        """Return Chromium singleton artifacts for the shared profile."""
        return [
            os.path.join(PROFILE_DIR, "SingletonLock"),
            os.path.join(PROFILE_DIR, "SingletonCookie"),
            os.path.join(PROFILE_DIR, "SingletonSocket"),
        ]

    def _has_stale_singleton_socket(self) -> bool:
        """Return True when the profile points at a missing singleton socket."""
        socket_path = os.path.join(PROFILE_DIR, "SingletonSocket")
        if not os.path.islink(socket_path):
            return False
        try:
            target = os.readlink(socket_path)
        except OSError:
            return False
        return not os.path.exists(target)

    def cleanup_stale_profile_state(self) -> None:
        """Remove stale profile lock artifacts after an unclean Chromium exit.

        Chromium leaves ``Singleton*`` symlinks in the shared profile.  If the
        socket target is already gone, relaunching with the same profile becomes
        unreliable and users end up deleting ``~/.local/share/unifideck``.
        Only remove these files when the singleton socket is clearly broken.
        """
        if not self._has_stale_singleton_socket():
            return

        removed: List[str] = []
        for path in self._singleton_paths():
            try:
                os.unlink(path)
                removed.append(os.path.basename(path))
            except FileNotFoundError:
                continue
            except OSError as e:
                logger.warning(f"[MS] Failed to remove stale profile artifact {path}: {e}")

        if removed:
            logger.info(
                "[MS] Removed stale Chromium profile artifacts: "
                + ", ".join(sorted(removed))
            )

    def _get_browser_ws_url(self) -> Optional[str]:
        """Return the live CDP browser websocket URL, if Chromium is up."""
        import urllib.request as _req

        try:
            with _req.urlopen(
                f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=1
            ) as r:
                data = json.loads(r.read().decode())
            ws_url = data.get("webSocketDebuggerUrl")
            return ws_url if ws_url else None
        except Exception:
            return None

    def _list_cdp_targets(self) -> List[Dict[str, Any]]:
        """Return the current CDP targets exposed by Chromium."""
        import urllib.request as _req

        try:
            with _req.urlopen(
                f"http://127.0.0.1:{self.cdp_port}/json/list", timeout=1
            ) as r:
                data = json.loads(r.read().decode())
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def prepare_auth_launch(self) -> None:
        """Close any lingering CDP auth browser and clear broken lock files."""
        targets = self._list_cdp_targets()
        if targets:
            import urllib.error as _err
            import urllib.request as _req

            closed_any = False
            for target in targets:
                target_id = target.get("id")
                if not target_id:
                    continue
                try:
                    with _req.urlopen(
                        f"http://127.0.0.1:{self.cdp_port}/json/close/{target_id}",
                        timeout=2,
                    ) as r:
                        r.read()
                    closed_any = True
                except _err.HTTPError as e:
                    if e.code != 404:
                        logger.warning(
                            f"[MS] Could not close lingering auth target {target_id}: {e}"
                        )
                except Exception as e:
                    logger.warning(
                        f"[MS] Could not close lingering auth target {target_id}: {e}"
                    )

            if closed_any:
                for _ in range(20):
                    await asyncio.sleep(0.25)
                    if not self._get_browser_ws_url():
                        break
                logger.info("[MS] Closed lingering Chromium auth browser via DevTools HTTP")

        self.cleanup_stale_profile_state()

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
        Reuses the shared Unifideck profile so xCloud sessions can keep
        their cookies, while cleaning up stale auth-browser state first.

        Returns:
            True if Chromium was launched successfully.
        """
        self.kill()
        self.cleanup_stale_profile_state()

        cmd = self.find_cmd()
        if not cmd:
            logger.warning("[MS] No Chromium/Chrome found for auth")
            return False

        os.makedirs(PROFILE_DIR, exist_ok=True)

        args = cmd + [
            f"--app={auth_url}",
            "--class=unifideck-auth",
            f"--remote-debugging-port={self.cdp_port}",
            f"--user-data-dir={PROFILE_DIR}",
        ] + _BASE_FLAGS + [
            "--start-fullscreen",
            "--enable-touch-events",
            "--window-size=1280,800",
            f"--lang={self.locale_fn().split('-')[0]}",
        ]

        env = clean_env()
        logger.info(f"[MS] Launching Chromium: {' '.join(args[:7])}...")
        logger.info(
            f"[MS] Chromium env DISPLAY={env.get('DISPLAY')} "
            f"WAYLAND_DISPLAY={env.get('WAYLAND_DISPLAY')} "
            f"SteamAppId={env.get('SteamAppId')}"
        )

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
                env=env,
                preexec_fn=os.setpgrp,
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
            self.cleanup_stale_profile_state()
            return
        try:
            import time
            time.sleep(1)
            pgid = None
            try:
                pgid = os.getpgid(self.process.pid)
            except Exception:
                pgid = None

            if pgid is not None and pgid != os.getpgrp():
                os.killpg(pgid, signal.SIGTERM)
            else:
                self.process.terminate()

            self.process.wait(timeout=10)
            logger.info("[MS] Chromium auth closed (cookies flushed)")
        except subprocess.TimeoutExpired:
            logger.debug("[MS] Chromium didn't exit -- sending SIGKILL")
            try:
                pgid = None
                try:
                    pgid = os.getpgid(self.process.pid)
                except Exception:
                    pgid = None
                if pgid is not None and pgid != os.getpgrp():
                    os.killpg(pgid, signal.SIGKILL)
                else:
                    self.process.kill()
                self.process.wait(timeout=3)
            except Exception:
                pass
        except Exception as e:
            logger.debug(f"[MS] Chromium kill error (non-fatal): {e}")
        self.process = None
        self.cleanup_stale_profile_state()

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
