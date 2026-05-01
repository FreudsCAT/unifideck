"""services/bootstrap/teardown.py — Shutdown sequence for Layer-5 services.

Calls ``stop`` (or ``disconnect`` for CDP) on every service in
reverse wiring order — consumers before producers — so no
service can fire into a half-shutdown subscriber.
"""
from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .container import ServiceContainer

logger = logging.getLogger(__name__)

# Reverse wiring order. Pure-consumer services tear down first
# because they only subscribe — once detached, no event can
# race against their shutdown. CDP last because some services
# may still try to inject CSS during shutdown.
_TEARDOWN_ORDER: tuple[str, ...] = (
    "cloud_prompt",
    "security",
    "probe_reaction",
    "feature_flags",
    "playtime",
    "account",
    "metrics",
    "cloudsave",
    "proton",
    "artwork",
    "metadata",
    "download",
    "shortcut",
    "cdp",
)


async def stop_all_services(container: ServiceContainer) -> None:
    """Call ``stop``/``disconnect`` on every service in order.

    Walks ``_TEARDOWN_ORDER``: None slot or missing stop method
    → skip. Each call wrapped in try/except so one broken
    service can't hold the plugin in a half-shutdown state
    (otherwise Steam would hang). Failures logged at WARNING
    and teardown continues.
    """
    for service_name in _TEARDOWN_ORDER:
        instance = getattr(container, service_name, None)
        if instance is None:
            continue

        method_name = "disconnect" if service_name == "cdp" else "stop"
        stop_method = getattr(instance, method_name, None)

        if not callable(stop_method):
            continue

        try:
            # Need to check if it's a coroutine function because
            # some services may use async stop hooks, others sync.
            import asyncio
            if asyncio.iscoroutinefunction(stop_method):
                await stop_method()
            else:
                stop_method()

            logger.info("[Teardown] stopped %s", service_name)
        except Exception as e:
            logger.warning(
                "[Teardown] failed to stop %s: %s",
                service_name, e,
            )
