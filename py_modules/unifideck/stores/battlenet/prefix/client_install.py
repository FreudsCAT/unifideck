"""Download and silently install the Battle.net client into a prefix.

py_modules/unifideck/stores/battlenet/prefix/client_install.py

Nothing is bundled. The client is fetched at runtime from Blizzard's own
installer URL and cached, exactly as Ubisoft fetches
``UbisoftConnectInstaller.exe`` — shipping a vendor installer in the plugin
would be both large and stale.

Proven on-device 2026-07-03: the official 4.9 MB stub, run under ``umu-run``
with ``WINEPREFIX`` pointing at a fresh prefix, downloaded and installed the
client and Agent unattended and exited 0.

Two things this must get right or the install hangs rather than fails:

* **Display environment.** The plugin runs headless under
  ``plugin_loader``; a Wine process with no ``DISPLAY`` /
  ``XDG_RUNTIME_DIR`` / DBus hangs. ``WineEnvResolver`` borrows them from
  the live Steam process.
* **The client is 32-bit** (PE32 i386, confirmed on-device). Without a
  32-bit Vulkan ICD the installer freezes around 25% with no error, so that
  is checked up front and reported rather than waited on.
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import ssl
import urllib.request
from dataclasses import dataclass
from pathlib import Path

from unifideck.stores.battlenet import paths
from unifideck.stores.shared.wine_env import WineEnvResolver

from . import tweaks

logger = logging.getLogger(__name__)

# The stub is ~4.9 MB; anything wildly off means we cached an error page.
MIN_INSTALLER_BYTES = 1_000_000
DOWNLOAD_TIMEOUT_SECONDS = 300
# The installer downloads the real client, so it needs a generous budget.
INSTALL_TIMEOUT_SECONDS = 1800

GAMEID = "umu-battlenet"


@dataclass(frozen=True, slots=True)
class BootstrapResult:
    """Outcome of preparing a prefix with the client in it."""

    success: bool
    error: str | None = None
    error_code: str | None = None


def _ssl_context() -> ssl.SSLContext:
    """Permissive TLS.

    An outdated CA bundle on SteamOS breaks otherwise-fine downloads, and
    the plugin disables verification everywhere except the updater for
    exactly this reason.
    """
    ctx = ssl.create_default_context()
    ctx.check_hostname = False
    ctx.verify_mode = ssl.CERT_NONE
    return ctx


def _download_sync(url: str, destination: Path) -> bool:
    tmp = destination.with_suffix(destination.suffix + ".part")
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        request = urllib.request.Request(url, headers={"User-Agent": "Unifideck"})
        with urllib.request.urlopen(
            request, timeout=DOWNLOAD_TIMEOUT_SECONDS, context=_ssl_context(),
        ) as response, tmp.open("wb") as handle:
            shutil.copyfileobj(response, handle)
    except (OSError, ValueError) as exc:
        logger.warning("[Battlenet] installer download failed: %s", exc)
        tmp.unlink(missing_ok=True)
        return False
    if tmp.stat().st_size < MIN_INSTALLER_BYTES:
        logger.warning(
            "[Battlenet] downloaded installer is only %d bytes — discarding",
            tmp.stat().st_size,
        )
        tmp.unlink(missing_ok=True)
        return False
    tmp.replace(destination)
    return True


def _cached_installer_is_usable(path: Path) -> bool:
    """A cached file only counts if it is plausibly the real stub.

    Guards against an error page or a truncated download being reused
    forever as though it were the installer.
    """
    try:
        return path.is_file() and path.stat().st_size >= MIN_INSTALLER_BYTES
    except OSError:
        return False


async def ensure_installer(url: str, cache_path: Path) -> Path | None:
    """Return a cached installer, downloading it if absent. Never raises."""
    path = Path(cache_path)
    if await asyncio.to_thread(_cached_installer_is_usable, path):
        return path
    logger.info("[Battlenet] downloading client installer from %s", url)
    ok = await asyncio.to_thread(_download_sync, url, path)
    return path if ok else None


def has_32bit_vulkan() -> bool:
    """Whether a 32-bit Vulkan ICD is present.

    The client is PE32 i386. Without this the installer freezes at ~25%
    with no error message at all, so it is far better to say so up front.
    """
    roots = (
        Path("/usr/share/vulkan/icd.d"),
        Path("/usr/local/share/vulkan/icd.d"),
        Path("~/.local/share/vulkan/icd.d").expanduser(),
    )
    for root in roots:
        try:
            for icd in root.glob("*.json"):
                if "i686" in icd.name or "32" in icd.name:
                    return True
        except OSError:
            continue
    return False


async def run_silent_install(
    installer: Path,
    prefix: Path,
    resolver: WineEnvResolver,
) -> bool:
    """Run the installer inside ``prefix`` under umu. Never raises."""
    umu_run = resolver.find_umu_run()
    if not umu_run:
        logger.error("[Battlenet] umu-run not found — cannot install the client")
        return False

    env = resolver.build_env(prefix, GAMEID)
    if not env.get("DISPLAY") and not env.get("WAYLAND_DISPLAY"):
        # Headless Decky env: a Wine process with no display hangs instead
        # of failing, so refuse rather than burn the timeout.
        logger.error(
            "[Battlenet] no DISPLAY/WAYLAND_DISPLAY available — refusing to "
            "run the installer (it would hang rather than fail)",
        )
        return False

    await asyncio.to_thread(Path(prefix).mkdir, parents=True, exist_ok=True)
    logger.info("[Battlenet] installing client into %s", prefix)
    try:
        proc = await asyncio.create_subprocess_exec(
            umu_run,
            str(installer),
            env=env,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
    except OSError:
        logger.exception("[Battlenet] could not spawn the installer")
        return False

    try:
        _out, err = await asyncio.wait_for(
            proc.communicate(), timeout=INSTALL_TIMEOUT_SECONDS,
        )
    except TimeoutError:
        logger.exception("[Battlenet] installer timed out — killing")
        proc.kill()
        await proc.wait()
        return False

    if proc.returncode != 0:
        logger.warning(
            "[Battlenet] installer exited %s: %s",
            proc.returncode,
            err.decode(errors="replace")[-400:],
        )
    # Trust the filesystem over the exit code: the stub has been observed
    # exiting non-zero after a successful install.
    return bool(await asyncio.to_thread(paths.client_installed, prefix))


def apply_prefix_tweaks(prefix: Path) -> bool:
    """Write the settings the client needs before its first run."""
    drive_c = paths.drive_c(prefix)
    if drive_c is None:
        return False
    ok = tweaks.write_client_config(drive_c)
    if ok:
        tweaks.mark_applied(prefix)
    return ok


async def bootstrap_client(
    prefix: Path,
    *,
    installer_url: str,
    installer_cache: Path,
    resolver: WineEnvResolver,
) -> BootstrapResult:
    """Ensure ``prefix`` contains a usable, tweaked Battle.net client."""
    if paths.client_installed(prefix):
        if not tweaks.tweaks_applied(prefix):
            apply_prefix_tweaks(prefix)
        return BootstrapResult(success=True)

    if not has_32bit_vulkan():
        return BootstrapResult(
            success=False,
            error=(
                "32-bit Vulkan drivers are missing. The Battle.net client is "
                "32-bit and its installer will freeze without them."
            ),
            error_code="missing_32bit_vulkan",
        )

    installer = await ensure_installer(installer_url, installer_cache)
    if installer is None:
        return BootstrapResult(
            success=False,
            error="Could not download the Battle.net installer",
            error_code="installer_download_failed",
        )

    if not await run_silent_install(installer, prefix, resolver):
        return BootstrapResult(
            success=False,
            error="The Battle.net client installer did not complete",
            error_code="client_install_failed",
        )

    apply_prefix_tweaks(prefix)
    logger.info("[Battlenet] client installed into %s", prefix)
    return BootstrapResult(success=True)
