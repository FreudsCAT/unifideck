"""Async start-up tasks — run once after all services are constructed.

OP-13e | py_modules/unifideck/services/bootstrap/startup.py

Some services need an async initialisation step that can't run inside
their constructor (the constructor is synchronous). ``start_async_services``
is the awaitable called after ``bootstrap_services`` to perform :

* DB warmup (open the playtime DB connection, run migrations);
* token store rehydration (decrypt cached credentials);
* artwork cache integrity check.

``_self_heal_executable_bits`` is a safety pass that sets ``chmod +x``
on bundled binaries (gogdl, nile, …) — necessary because git on
Windows can strip the exec bit, and the bundled wheel may have lost it.
"""

from __future__ import annotations
import logging
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .container import ServiceContainer
logger = logging.getLogger(__name__)


async def start_async_services(container: ServiceContainer) -> None:
    """Run the async start-up phase on every service that needs one.

    Walks a fixed allow-list of services (``download``, ``account``,
    ``playtime``, ``security``, ``launch_history``) and calls
    ``start()`` on each one when present. The order matters:
    ``download`` is started first so it can rehydrate its queue
    from disk before any other service tries to enqueue items, and
    ``security`` is started before ``launch_history`` so that its
    audit log is ready to receive launch-history's failure events.

    Per-service failures are tolerated (logged at WARN) — a broken
    ``start`` must not prevent the rest of the plugin from coming
    up. After every service is started, ``_self_heal_executable_bits``
    runs as a one-off safety pass.

    Args:
        container: the populated ``ServiceContainer`` returned by
            ``bootstrap_services``.
    """
    for attr in (
        "download",
        "account",
        "playtime",
        "security",
        "launch_history",
    ):
        svc = getattr(container, attr, None)
        if svc is None:
            continue
        start = getattr(svc, "start", None)
        if start is None:
            continue
        try:
            await start()
        except Exception as e:
            logger.warning(
                "[bootstrap] %s.start failed: %s",
                attr,
                e,
            )
    _self_heal_executable_bits()


def _self_heal_executable_bits() -> None:
    """Restore the executable bit on bundled binaries.

    Git on Windows strips the executable bit when checking out the
    plugin's wheel, leaving binaries like ``gogdl``, ``nile`` and
    ``legendary`` non-launchable on the Steam Deck. This pass
    locates the plugin directory (4 levels above this file) and
    delegates to ``launcher.packaging.ensure_executable_files``
    which scans the known-binary paths and chmod-s them ``+x``.

    Logs at INFO when any file was fixed (one-time event on first
    boot after install), at WARN if the heal pass itself crashes
    (rare — typically permission issues if the plugin dir is
    read-only).
    """
    try:
        plugin_dir_path = Path(__file__).resolve().parents[4]
        from ...launcher.packaging import ensure_executable_files

        fixed = ensure_executable_files(plugin_dir_path)
        if fixed > 0:
            logger.info(
                "[bootstrap] executable bit self-heal: %d file(s) fixed",
                fixed,
            )
    except Exception as e:
        logger.warning(
            "[bootstrap] executable bit self-heal failed: %s",
            e,
        )
