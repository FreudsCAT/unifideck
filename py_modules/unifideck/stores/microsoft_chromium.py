"""
Chromium-based browser management for the Microsoft xCloud connector.

Handles launching, finding, killing, and installing Microsoft Edge or another
compatible Chromium-based browser. Manages the shared browser profile
(``~/.local/share/unifideck/chromium-auth``), environment variables needed for
GUI apps under PluginLoader, and browser lifecycle for the auth page.

This module has no knowledge of OAuth, tokens, or the xCloud catalog — it
only manages the browser lifecycle. The connector (``microsoft.py``) owns
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

# Only Microsoft Edge is supported (native xCloud gamepad + Steam Deck controller support)
_FLATPAK_APPS = ("com.microsoft.Edge",)
_EDGE_FLATPAK_APP = "com.microsoft.Edge"
_FLATHUB_REMOTE = "flathub"
_FLATHUB_REMOTE_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"

# Native binary names to search if no flatpak found (Edge only)
_NATIVE_BINS = ("microsoft-edge", "microsoft-edge-stable")

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
    """Return a clean environment for launching the auth browser/flatpak.

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
    """Manages a Chromium-based browser for Microsoft auth and xCloud.

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
        """Return True when the auth browser instance is still alive."""
        if self.process is not None and self.process.poll() is None:
            return True
        return self._get_browser_ws_url() is not None

    def _singleton_paths(self) -> List[str]:
        """Return singleton artifacts for the shared auth profile."""
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
        """Remove stale profile lock artifacts after an unclean browser exit.

        Chromium-based browsers leave ``Singleton*`` symlinks in the shared profile. If the
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
                "[MS] Removed stale browser profile artifacts: "
                + ", ".join(sorted(removed))
            )

    def _get_browser_ws_url(self) -> Optional[str]:
        """Return the live CDP browser websocket URL, if the auth browser is up."""
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
        """Return the current CDP targets exposed by the auth browser."""
        import urllib.request as _req

        try:
            with _req.urlopen(
                f"http://127.0.0.1:{self.cdp_port}/json/list", timeout=1
            ) as r:
                data = json.loads(r.read().decode())
            return data if isinstance(data, list) else []
        except Exception:
            return []

    async def _close_all_cdp_targets(self, *, log_prefix: str) -> bool:
        """Close all live targets exposed on this browser's CDP port."""
        targets = self._list_cdp_targets()
        if not targets:
            return False

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
                        f"[MS] Could not close {log_prefix} target {target_id}: {e}"
                    )
            except Exception as e:
                logger.warning(
                    f"[MS] Could not close {log_prefix} target {target_id}: {e}"
                )

        if closed_any:
            for _ in range(20):
                await asyncio.sleep(0.25)
                if not self._get_browser_ws_url():
                    break
            logger.info(
                f"[MS] Closed {log_prefix} browser targets via DevTools HTTP"
            )
        return closed_any

    async def prepare_auth_launch(self) -> None:
        """Close any lingering CDP auth browser and clear broken lock files."""
        await self._close_all_cdp_targets(log_prefix="lingering auth")
        self.cleanup_stale_profile_state()

    async def close_auth_browser(self) -> bool:
        """Close the live Microsoft auth browser after OAuth succeeds."""
        closed = await self._close_all_cdp_targets(log_prefix="auth")
        if closed:
            self.cleanup_stale_profile_state()
        return closed

    # ── Detection & install ──────────────────────────────────────────────

    def _flatpak_remote_names(self, scope: str) -> set[str]:
        """Return configured flatpak remote names for the given scope."""
        if scope not in ("--user", "--system"):
            return set()
        try:
            result = subprocess.run(
                ["flatpak", "remotes", scope, "--columns=name"],
                capture_output=True,
                text=True,
                timeout=5,
                env=clean_env(),
            )
        except Exception:
            return set()
        if result.returncode != 0:
            return set()

        remotes: set[str] = set()
        for raw_line in result.stdout.splitlines():
            line = raw_line.strip()
            if not line or line.lower() == "name":
                continue
            remotes.add(line)
        return remotes

    async def _ensure_user_flathub_remote(self) -> bool:
        """Ensure the user Flatpak installation can see the Flathub remote."""
        if _FLATHUB_REMOTE in self._flatpak_remote_names("--user"):
            return True

        logger.info("[MS] Adding user flathub remote for browser installation")
        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "flatpak",
                        "remote-add",
                        "--if-not-exists",
                        "--user",
                        _FLATHUB_REMOTE,
                        _FLATHUB_REMOTE_URL,
                    ],
                    capture_output=True,
                    timeout=60,
                    env=clean_env(),
                ),
            )
        except Exception as e:
            logger.warning(f"[MS] Could not add user flathub remote: {e}")
            return False

        if proc.returncode != 0:
            stderr = proc.stderr.decode("utf-8", errors="replace")[:200]
            logger.warning(f"[MS] Adding user flathub remote failed: {stderr}")
            return False

        return _FLATHUB_REMOTE in self._flatpak_remote_names("--user")

    def find_cmd(self) -> Optional[List[str]]:
        """Find an available Microsoft Edge browser command.

        Checks both ``--user`` and ``--system`` flatpak installations,
        then falls back to native Edge binaries.

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
        """Return True if a compatible Chromium-based browser is available."""
        return self.find_cmd() is not None

    async def install(self) -> Dict[str, Any]:
        """Install Microsoft Edge via Flatpak in the user installation.

        Ensures the user Flathub remote exists first so this works on SteamOS
        variants, Bazzite, CachyOS, and other immutable Linux distros where
        Flatpak is present but only system remotes were preconfigured.

        Returns:
            Dict with ``success`` and ``message`` or ``error`` keys.
        """
        if not shutil.which("flatpak"):
            return {"success": False, "error": "microsoft.flatpakNotFound"}
        if self.is_installed:
            return {"success": True, "message": "microsoft.browserAlreadyInstalled"}

        if not await self._ensure_user_flathub_remote():
            return {"success": False, "error": "microsoft.browserInstallFailed"}

        logger.info("[MS] Attempting to install Microsoft Edge via flatpak...")
        try:
            proc = await asyncio.get_event_loop().run_in_executor(
                None,
                lambda: subprocess.run(
                    [
                        "flatpak",
                        "install",
                        "--user",
                        "--noninteractive",
                        "-y",
                        _FLATHUB_REMOTE,
                        _EDGE_FLATPAK_APP,
                    ],
                    capture_output=True,
                    timeout=300,
                    env=clean_env(),
                ),
            )
            if proc.returncode == 0:
                logger.info("[MS] Microsoft Edge installed successfully")
                return {"success": True, "message": "microsoft.browserInstalled"}

            stderr = proc.stderr.decode("utf-8", errors="replace")[:200]
            logger.warning(f"[MS] Microsoft Edge install failed: {stderr}")
            return {"success": False, "error": "microsoft.browserInstallFailed"}
        except subprocess.TimeoutExpired:
            logger.warning("[MS] Microsoft Edge install timed out")
            return {"success": False, "error": "microsoft.chromiumInstallTimeout"}
        except Exception as e:
            logger.warning(f"[MS] Microsoft Edge install error: {e}")
            return {"success": False, "error": "microsoft.browserInstallFailed"}

    # ── Launch / kill ────────────────────────────────────────────────────

    def launch_auth(self, auth_url: str) -> bool:
        """Launch the auth browser for OAuth with remote debugging.

        Opens the browser in fullscreen app mode with our CDP port.
        Reuses the shared Unifideck profile so xCloud sessions can keep
        their cookies, while cleaning up stale auth-browser state first.

        Returns:
            True if the browser was launched successfully.
        """
        self.kill()
        self.cleanup_stale_profile_state()

        cmd = self.find_cmd()
        if not cmd:
            logger.warning("[MS] No compatible browser found for auth")
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
        logger.info(f"[MS] Launching auth browser: {' '.join(args[:7])}...")
        logger.info(
            f"[MS] Auth browser env DISPLAY={env.get('DISPLAY')} "
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
            logger.info(f"[MS] Auth browser PID: {self.process.pid}")
            return True
        except Exception as e:
            logger.error(f"[MS] Failed to launch auth browser: {e}", exc_info=True)
            return False
        finally:
            if stderr_fh is not None:
                stderr_fh.close()

    def kill(self) -> None:
        """Gracefully terminate the auth browser process.

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
            logger.info("[MS] Auth browser closed (cookies flushed)")
        except subprocess.TimeoutExpired:
            logger.debug("[MS] Auth browser didn't exit -- sending SIGKILL")
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
            logger.debug(f"[MS] Auth browser kill error (non-fatal): {e}")
        self.process = None
        self.cleanup_stale_profile_state()

    # ── Cookie management ────────────────────────────────────────────────

    @staticmethod
    def has_xbox_session() -> bool:
        """Return True if xbox.com cookies exist in the shared browser profile.

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
        """Delete Xbox / Microsoft cookies from the shared browser profile."""
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
                logger.info("[MS] Cleared Xbox/MS cookies from shared browser profile")
            except Exception:
                conn.rollback()
                raise
            finally:
                conn.close()
        except Exception as e:
            logger.debug(f"[MS] Could not clear shared browser cookies: {e}")

    @staticmethod
    def clear_profile_data() -> None:
        """Delete the shared Chromium auth profile and log files."""
        removed: List[str] = []
        for path in (PROFILE_DIR, LOG_FILE):
            if not os.path.exists(path):
                continue
            try:
                if os.path.isdir(path) and not os.path.islink(path):
                    shutil.rmtree(path)
                else:
                    os.remove(path)
                removed.append(os.path.basename(path))
            except Exception as e:
                logger.warning(f"[MS] Could not clear auth profile path {path}: {e}")

        if removed:
            logger.info(
                "[MS] Cleared Chromium auth state: " + ", ".join(sorted(removed))
            )

    # ── CDP helpers ──────────────────────────────────────────────────────

    async def wait_and_check_crash(self) -> bool:
        """Wait for the auth browser to start, return False if it crashed.

        Called at the start of the auth monitor task.  Polls every 0.5 s
        for up to 10 s to allow the browser time to start on loaded systems.
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
                logger.error(f"[MS] Auth browser crashed before CDP. stderr: {err}")
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
        logger.warning("[MS] Auth browser started but CDP port not responding after 10 s")
        return True  # process is alive, let caller retry CDP
