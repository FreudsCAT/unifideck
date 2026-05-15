"""auth.edge_browser.installer — Edge flatpak install + system hooks.

Microsoft Edge installation via Flatpak, with default-browser
preservation, udev filesystem override for controller support,
and ensure-user-flathub-remote preflight. Read-only detection
helpers (``find_cmd``, ``is_installed``, remote names listing)
live in ``detection.py`` — this module focuses on the
mutate-the-system side.

Moved from ``auth/edge_installer.py`` and trimmed on 2026-04-18:
the 3 detection methods were extracted into ``detection.py`` as
free functions, leaving this class focused on install + system
modification concerns. Public API is preserved — the class still
exposes ``find_cmd`` / ``is_installed`` as thin wrappers so
existing callers (EdgeBrowser) don't have to change.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
import shutil
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from .detection import find_edge_cmd, flatpak_remote_names, is_edge_installed

if TYPE_CHECKING:
    from collections.abc import Callable

logger = logging.getLogger(__name__)

# ── Constants ────────────────────────────────────────────────────────
# Flatpak app identifiers. Only Microsoft Edge is supported because it
# is the only browser that ships native xCloud gamepad + Steam Deck
# controller support.
_FLATPAK_APPS = ("com.microsoft.Edge",)
_EDGE_FLATPAK_APP = "com.microsoft.Edge"
_FLATHUB_REMOTE = "flathub"
_FLATHUB_REMOTE_URL = "https://dl.flathub.org/repo/flathub.flatpakrepo"
# Native binary names (kept re-exported for backward compat)
_NATIVE_BINS = ("microsoft-edge", "microsoft-edge-stable")


class EdgeInstaller:
    """Install Microsoft Edge and configure it for controller support.

    All methods are safe to call on a system without flatpak. In that
    case ``find_cmd()`` probes the native PATH for ``microsoft-edge``
    or ``microsoft-edge-stable`` and ``install()`` returns a
    descriptive error.

    Usage::

        inst = EdgeInstaller(clean_env_fn=edge_browser.clean_env)
        if not inst.is_installed:
            result = await inst.install()
            if result["success"]:
                inst.ensure_controller_permissions()
    """

    def __init__(self, clean_env_fn: Callable[[], dict[str, Any]]) -> None:
        """Build an installer.

        Args:
          clean_env_fn: Zero-arg callable returning a scrubbed env dict.
            Injected rather than imported to avoid a circular import
            with the ``env`` module.

        """
        self._clean_env = clean_env_fn

    # ── Permissions ──────────────────────────────────────────────────

    def ensure_controller_permissions(self) -> bool:
        """Grant Edge flatpak read access to ``/run/udev`` for controllers.

        Edge's Gamepad API needs udev metadata (device names, vendor
        IDs) to identify controllers. ``flatpak run --device=all``
        only exposes ``/dev/*`` nodes; ``/run/udev`` requires a
        separate filesystem override. This is the same step
        Microsoft's official Steam Deck + xCloud guide recommends
        users run manually::

            flatpak --user override --filesystem=/run/udev:ro com.microsoft.Edge

        Returns ``True`` if the override is already present or was
        applied successfully, ``False`` on error.
        """
        if not shutil.which("flatpak"):
            return False
        overrides_path = Path(
            f"~/.local/share/flatpak/overrides/{_EDGE_FLATPAK_APP}",
        ).expanduser()
        with contextlib.suppress(OSError):
            if overrides_path.is_file():
                with overrides_path.open() as fh:
                    if "/run/udev" in fh.read():
                        logger.debug(
                            "[Edge] Edge udev override already present",
                        )
                        return True
        logger.info(
            "[Edge] Applying flatpak /run/udev:ro override for "
            "controller support",
        )
        try:
            proc = subprocess.run(
                [
                    "flatpak", "--user", "override",
                    "--filesystem=/run/udev:ro",
                    _EDGE_FLATPAK_APP,
                ],
                capture_output=True,
                timeout=30,
                check=False,
            )
            if proc.returncode == 0:
                logger.info(
                    "[Edge] Edge udev override applied successfully",
                )
                return True
            stderr = proc.stderr.decode(
                "utf-8", errors="replace",
            )[:200]
            logger.warning(
                "[Edge] Edge udev override failed: %s", stderr,
            )
            return False
        except Exception as exc:
            logger.warning(
                "[Edge] Edge udev override error: %s", exc,
            )
            return False

    # ── Detection thin wrappers (delegate to detection.py) ───────────

    def _flatpak_remote_names(self, scope: str) -> set[str]:
        """Delegate to detection.flatpak_remote_names."""
        return flatpak_remote_names(self._clean_env, scope)

    def find_cmd(self) -> list[str] | None:
        """Delegate to detection.find_edge_cmd."""
        return find_edge_cmd(self._clean_env)

    @property
    def is_installed(self) -> bool:
        """Delegate to detection.is_edge_installed."""
        return is_edge_installed(self._clean_env)

    # ── Install-specific helpers ─────────────────────────────────────

    async def _ensure_user_flathub_remote(self) -> bool:
        """Ensure the user Flatpak installation can see the Flathub remote."""
        if _FLATHUB_REMOTE in flatpak_remote_names(
            self._clean_env, "--user",
        ):
            return True
        logger.info(
            "[Edge] Adding user flathub remote for "
            "browser installation",
        )
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
                    env=self._clean_env(),
                    check=False,
                ),
            )
        except Exception as e:
            # Intentional: subprocess failure here can't be classified
            # (network, missing binary, timeout). Log and surface as
            # False so the install wizard displays a retry option.
            logger.warning(
                "[Edge] Could not add user flathub remote: %s", e,
            )
            return False
        if proc.returncode != 0:
            stderr = proc.stderr.decode(
                "utf-8", errors="replace",
            )[:200]
            logger.warning(
                "[Edge] Adding user flathub remote failed: %s",
                stderr,
            )
            return False
        return (
            _FLATHUB_REMOTE
            in flatpak_remote_names(self._clean_env, "--user")
        )

    # ── Default browser snapshot / restore ───────────────────────────

    def _get_default_browser(self) -> str | None:
        """Snapshot the current default web browser before Edge install."""
        try:
            result = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
                env=self._clean_env(),
                check=False,  # rc is read manually below
            )
            if result.returncode == 0 and result.stdout.strip():
                return result.stdout.strip()
        except Exception as e:
            # ``xdg-settings`` may be missing on minimal Decks or
            # fail under non-interactive sessions. Either way the
            # caller treats a ``None`` snapshot as "skip restore".
            logger.debug("[Edge] xdg-settings probe failed: %s", e)
        return None

    def _restore_default_browser(self, original: str | None) -> None:
        """Restore the default browser if Edge install changed it."""
        if not original:
            return
        try:
            current = subprocess.run(
                ["xdg-settings", "get", "default-web-browser"],
                capture_output=True, text=True, timeout=5,
                env=self._clean_env(),
                check=False,
            ).stdout.strip()
            if current != original:
                subprocess.run(
                    [
                        "xdg-settings", "set",
                        "default-web-browser", original,
                    ],
                    capture_output=True, timeout=5,
                    env=self._clean_env(),
                    check=False,
                )
                logger.info(
                    "[Edge] Restored default browser to %s",
                    original,
                )
        except Exception as e:
            # Intentional: xdg-settings failure is non-fatal (e.g.
            # missing on non-desktop distros). Worst case the user
            # has to manually reset their browser.
            logger.debug(
                "[Edge] Could not restore default browser: %s", e,
            )

    # ── Install ──────────────────────────────────────────────────────

    async def install(self) -> dict[str, Any]:
        """Install Microsoft Edge via Flatpak in the user installation.

        Ensures the user Flathub remote exists first so this works on
        SteamOS variants, Bazzite, CachyOS, and other immutable Linux
        distros where Flatpak is present but only system remotes were
        preconfigured. Saves and restores the user's default browser
        setting so that Edge installation does not hijack URL handlers
        from Firefox or other browsers.

        Returns:
          Dict with ``success`` and ``message`` or ``error`` keys.

        """
        if not shutil.which("flatpak"):
            return {"success": False, "error": "microsoft.flatpakNotFound"}
        if self.is_installed:
            return {
                "success": True,
                "message": "microsoft.browserAlreadyInstalled",
            }
        if not await self._ensure_user_flathub_remote():
            return {
                "success": False,
                "error": "microsoft.browserInstallFailed",
            }
        # Snapshot current default browser before installing Edge
        original_browser = self._get_default_browser()
        logger.info(
            "[Edge] Attempting to install Microsoft Edge via flatpak...",
        )
        try:
            proc = await self._run_flatpak_install()
            if proc.returncode == 0:
                logger.info(
                    "[Edge] Microsoft Edge installed successfully",
                )
                self._restore_default_browser(original_browser)
                await self._wait_for_edge_ready()
                # Grant udev access so Edge can detect
                # controllers (xCloud)
                self.ensure_controller_permissions()
                return {
                    "success": True,
                    "message": "microsoft.browserInstalled",
                }
            stderr = proc.stderr.decode(
                "utf-8", errors="replace",
            )[:200]
            logger.warning(
                "[Edge] Microsoft Edge install failed: %s", stderr,
            )
            return {
                "success": False,
                "error": "microsoft.browserInstallFailed",
            }
        except subprocess.TimeoutExpired:
            logger.warning("[Edge] Microsoft Edge install timed out")
            return {
                "success": False,
                "error": "microsoft.edgeInstallTimeout",
            }
        except Exception as e:
            logger.warning(
                "[Edge] Microsoft Edge install error: %s", e,
            )
            return {
                "success": False,
                "error": "microsoft.browserInstallFailed",
            }

    async def _run_flatpak_install(
        self,
    ) -> subprocess.CompletedProcess[bytes]:
        """Run the flatpak install command in the executor.

        The ``subprocess.run`` is synchronous and would block
        the event loop — we trampoline through the default
        executor. The clean env prevents session-specific
        locale/theme variables from confusing flatpak's
        noninteractive output parser.
        """
        return await asyncio.get_event_loop().run_in_executor(
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
                env=self._clean_env(),
                check=False,
            ),
        )

    async def _wait_for_edge_ready(self) -> None:
        """Poll until flatpak metadata is indexed, up to 10 seconds.

        On some distros there is a short window after
        ``flatpak install`` returns before ``flatpak info`` can
        locate the app. Callers need ``is_installed == True``
        immediately after this returns, so we poll for the
        command to resolve before proceeding.
        """
        for _ in range(10):
            if self.find_cmd() is not None:
                return
            await asyncio.sleep(1)
