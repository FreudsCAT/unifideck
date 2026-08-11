"""Install the Battle.net client into a prefix, and say why when it fails.

py_modules/unifideck/launcher/proton/handlers/battlenet_bootstrap.py

Runs **here**, inside the RunGame session, rather than in the backend. The
rule is the one ``services/download/wrapper_signals.py`` already states: the
backend must not spawn the vendor client itself, because in Gaming Mode a
bare subprocess has no gamescope session and its window never appears. It
applies to the client's *installer* too — that is exactly how this failed
once. Signing in from the desktop showed the wizard; from Gaming Mode it
rendered nowhere, and the sign-in RPC blocked on a window nobody could see.

Split out of ``handlers/battlenet.py`` when the reason for a failure started
mattering as much as the fact of one. The old code reduced a structured
``BootstrapResult`` to a bare ``False``, so a user whose install was refused
over a (wrongly) undetected 32-bit Vulkan driver was told *"Battle.net Not
Installed — reinstall the game to rebuild it"*: not the reason, and not
something they could act on. The mapping below is the fix, and the reason
this module returns the result rather than a boolean.

Stdlib-and-launcher imports only, deliberately verified to load under the
SYSTEM python (3.10-3.14) that runs this process.
"""

from __future__ import annotations

import asyncio
import logging
from typing import TYPE_CHECKING

from unifideck.launcher.frontend_bridge import launcher_toast

if TYPE_CHECKING:  # pragma: no cover - typing only
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
    from unifideck.stores.battlenet.prefix.client_install import BootstrapResult

logger = logging.getLogger(__name__)

# error_code -> the i18n key pair (``key`` + ``keyMessage``) to toast.
# Anything unmapped falls back to the generic "prefix has no client", which
# is at least true even when it is not specific.
ERROR_TOAST_KEYS = {
    "missing_32bit_vulkan": "battlenetMissing32BitVulkan",
    "installer_download_failed": "battlenetInstallerDownloadFailed",
    "client_install_failed": "battlenetClientInstallFailed",
}


def toast_key_for(result: BootstrapResult | None, fallback: str) -> str:
    """The toast key naming why the client is missing."""
    code = result.error_code if result is not None else None
    return ERROR_TOAST_KEYS.get(code or "", fallback)


def _warn_no_32bit_vulkan() -> None:
    """Fired before an install on a host proven to lack 32-bit Vulkan.

    A warning, not a refusal. The install still runs: the client config is
    pre-seeded with hardware acceleration off, which may carry it through
    anyway, and a probe has no business costing a user their library.
    """
    launcher_toast(
        "toasts.launcher.battlenetNo32BitVulkanMessage",
        i18n_title_key="toasts.launcher.battlenetNo32BitVulkan",
    )


async def install_client(plan: ProtonLaunchPlan) -> BootstrapResult | None:
    """Install the client into ``plan.prefix_path``.

    Returns the ``BootstrapResult`` so the caller can name the failure, or
    ``None`` when the attempt itself raised — the caller falls back to its
    generic "this prefix has no client", which remains true either way.
    """
    logger.info("[battlenet] no client in %s — installing it", plan.prefix_path)
    launcher_toast(
        "toasts.launcher.battlenetInstallingClientMessage",
        i18n_title_key="toasts.launcher.battlenetInstallingClient",
    )
    try:
        from unifideck.stores.battlenet import config as store_config
        from unifideck.stores.battlenet.prefix.client_install import bootstrap_client
        from unifideck.stores.shared.wine_env import WineEnvResolver

        cfg = store_config.from_config_manager(None)
        result = await bootstrap_client(
            plan.prefix_path,
            installer_url=cfg.installer_url,
            installer_cache=cfg.installer_path,
            resolver=WineEnvResolver(
                "battlenet", str(getattr(plan.context, "plugin_dir", "") or ""),
            ),
            on_warning=_warn_no_32bit_vulkan,
        )
    except Exception:
        # Report "the prefix has no client", which is true and actionable,
        # rather than a traceback from the repair attempt.
        logger.exception("[battlenet] client install raised")
        return None
    if not result.success:
        logger.error(
            "[battlenet] client install failed (%s): %s", result.error_code, result.error,
        )
        return result
    logger.info("[battlenet] client installed into %s", plan.prefix_path)
    return result


async def ensure_tweaks(plan: ProtonLaunchPlan) -> bool:
    """Apply the client tweaks to a prefix that already holds a client.

    ``bootstrap_client`` does this too, but only ever reaches a prefix it had to
    install into. **No game prefix is ever one of those.** Every game prefix is
    an rsync clone of a template that already contains the client, so
    ``_bring_up_client`` finds the exe present, never calls the bootstrap, and
    the tweaks were silently skipped for the entire life of the prefix.
    Measured on-device: no ``Battle.net.config`` in the auth prefix, the
    template, or a game prefix carried ``HardwareAcceleration``, and no prefix
    carried the tweak marker. So the Lutris hardware-acceleration workaround
    (the fix for a login view that renders as a spinner with no buttons) was
    not actually in force anywhere. It went unnoticed because a host *with* a
    32-bit Vulkan driver does not need it.

    Marker-gated, so this is one ``stat`` on the normal path.

    Must run while the client is down: it merges into a file the client rewrites
    wholesale from memory when it exits.
    """
    try:
        from unifideck.stores.battlenet.prefix.client_install import (
            apply_prefix_tweaks,
        )
        from unifideck.stores.battlenet.prefix.tweaks import tweaks_applied

        if await asyncio.to_thread(tweaks_applied, plan.prefix_path):
            return False
        applied = await asyncio.to_thread(apply_prefix_tweaks, plan.prefix_path)
    except Exception:
        # Never fail a launch over this. The client starts either way, and on
        # the overwhelming majority of hosts it starts fine without the tweak.
        logger.exception("[battlenet] could not apply the client tweaks")
        return False
    if applied:
        logger.info("[battlenet] applied client tweaks to %s", plan.prefix_path)
    return applied
