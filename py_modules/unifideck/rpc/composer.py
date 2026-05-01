"""RPC handler composer — graft handler methods onto Plugin.

OP-24d | py_modules/unifideck/rpc/composer.py
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def bind_handlers(plugin: Any, *groups: Any) -> list[str]:
    """Graft public async methods from *groups* onto *plugin*.

    Raises ``ValueError`` on name collisions between groups.
    Returns sorted list of grafted method names.
    """
    grafted: dict[str, str] = {}  # name → source class name
    for group in groups:
        cls_name = type(group).__name__
        for name in group.handler_methods():
            if name in grafted:
                raise ValueError(
                    f"RPC name collision: '{name}' defined in both "
                    f"{grafted[name]} and {cls_name}"
                )
            setattr(plugin, name, getattr(group, name))
            grafted[name] = cls_name
    names = sorted(grafted)
    logger.info("Grafted %d handler methods: %s", len(names), ", ".join(names))
    return names
