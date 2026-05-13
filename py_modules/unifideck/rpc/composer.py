"""RPC composer — bind handler-group methods onto the plugin instance.

OP-24d | py_modules/unifideck/rpc/composer.py

The plugin class (Decky-side) exposes its RPC surface as plain
methods (``plugin.dispatch_unifideck_action``,
``plugin.get_audit_log``, etc.). ``bind_handlers`` copies the
methods from each handler-group instance onto the plugin so
Decky's RPC machinery sees them as native plugin methods.

A collision detector guards against two handler groups
declaring the same method name — a hard error at boot is much
better than silent overwrite.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from unifideck.rpc.handlers.base import RpcHandlerBase

logger = logging.getLogger(__name__)


def bind_handlers(plugin: Any, *groups: RpcHandlerBase) -> list[str]:
    """Attach every handler-group method onto ``plugin`` with collision detection.

    For each group, iterates ``group.handler_methods()`` (the
    list of public method names defined by
    ``RpcHandlerBase.handler_methods``) and binds the
    corresponding bound-method onto ``plugin``.

    Collision policy:

    * If two groups declare the same method name → raise
      ``ValueError`` with both class names in the message.
      Fail-fast at boot is cheap; a silent overwrite would
      leak hours of debugging.

    Logs at INFO with the total count for observability —
    a quick eyeball check that the expected number of RPC
    methods is bound.

    Args:
        plugin: the Decky plugin instance to bind onto.
        *groups: one or more handler-group instances. Order
            matters only for the message of a collision
            error.

    Returns:
        Sorted list of bound method names — useful for
        snapshotting the RPC surface in tests.

    Raises:
        ValueError: on a method-name collision.
    """
    seen: dict[str, str] = {}
    for group in groups:
        group_name = type(group).__name__
        for name in group.handler_methods():
            if name in seen:
                raise ValueError(
                    f"RPC name collision: '{name}' defined by both "
                    f"{seen[name]} and {group_name}",
                )
            seen[name] = group_name
            setattr(plugin, name, getattr(group, name))
    bound = sorted(seen.keys())
    logger.info(
        "[rpc.composer] bound %d RPC methods from %d groups",
        len(bound),
        len(groups),
    )
    return bound
