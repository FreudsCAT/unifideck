"""services/bootstrap/startup.py — Async start hooks + post-boot self-heal.

Calls ``start()`` on services that need async initialisation,
each wrapped in its own try/except so one broken service can't
block the others. Then runs a post-boot self-heal that restores
the +x bit on launcher entry points.
"""
from __future__ import annotations

import logging
import os
import stat
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .container import ServiceContainer

logger = logging.getLogger(__name__)

# Services with async init hooks. First three open DBs or spawn
# poll loops; ``security`` runs device-fingerprint verification;
# ``launch_history`` doesn't truly need async but is listed here
# for uniformity. Other services don't implement ``start`` and
# are skipped by the getattr probe below.
_ASYNC_START_SERVICES: tuple[str, ...] = (
    "download",
    "account",
    "playtime",
    "security",
    "launch_history",
)


async def start_async_services(container: ServiceContainer) -> None:
    """Await ``start`` on each entry in ``_ASYNC_START_SERVICES``.

    Missing service (None slot) → skip. Missing ``start`` method
    → skip. Failed start → log WARNING + continue (broken DB open
    or fingerprint check leaves that service disabled but plugin
    still boots). Always runs the executable-bit self-heal at
    the end.
    """
    for service_name in _ASYNC_START_SERVICES:
        instance = getattr(container, service_name, None)
        if instance is None:
            continue

        start_method = getattr(instance, "start", None)
        if not callable(start_method):
            continue

        try:
            await start_method()
            logger.info("[Startup] started %s", service_name)
        except Exception as e:
            logger.warning(
                "[Startup] failed to start %s: %s",
                service_name, e,
            )

    _self_heal_executable_bits()


def _self_heal_executable_bits() -> None:
    """Restore +x on launcher entry points after Decky Loader unzip.

    Decky Loader's unzip doesn't always preserve the
    ``external_attr`` field, so ``dispatcher.py`` can land
    without +x → execve fails with "Permission denied" even
    though the shebang is correct. Runs BEFORE the shortcut
    migration so when shortcuts are rewritten to point at the
    dispatcher it's already executable. Best-effort — failure
    logged but plugin continues to boot (recoverable via manual
    chmod +x).
    """
    try:
        # Get path to the bin directory relative to this file
        # This file is at py_modules/unifideck/services/bootstrap/startup.py
        base_dir = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(__file__)))))
        bin_dir = os.path.join(base_dir, "bin")

        if not os.path.isdir(bin_dir):
            return

        for filename in os.listdir(bin_dir):
            path = os.path.join(bin_dir, filename)
            if os.path.isfile(path):
                st = os.stat(path)
                # Add executable bit for owner/group/others if not present
                if not (st.st_mode & stat.S_IXUSR):
                    os.chmod(path, st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)
                    logger.info("[Startup] restored +x on %s", path)
    except Exception as e:
        logger.warning("[Startup] failed to self-heal executable bits: %s", e)
