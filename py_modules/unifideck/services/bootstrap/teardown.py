"""Symmetric shutdown — counterpart to ``bootstrap_services``.

OP-13f | py_modules/unifideck/services/bootstrap/teardown.py

``stop_all_services(container)`` walks the service container in
reverse construction order and calls each service's ``stop()``
coroutine (or skips silently if absent). Used by ``Plugin._unload`` to
release file handles, close DB connections, drain in-flight tasks
with a deadline before Decky kills the process.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .container import ServiceContainer
logger = logging.getLogger(__name__)


async def stop_all_services(container: ServiceContainer) -> None:
    """Stop all services."""
    teardown_order = [
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
    ]
    for attr in teardown_order:
        svc = getattr(container, attr, None)
        if svc is None:
            continue
        stop_fn = getattr(svc, "stop", None) or getattr(svc, "disconnect", None)
        if stop_fn is None:
            continue
        try:
            await stop_fn()
        except Exception as e:
            logger.warning(
                "[bootstrap] %s.%s raised: %s",
                attr,
                stop_fn.__name__,
                e,
            )
