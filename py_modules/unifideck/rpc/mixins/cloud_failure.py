"""CloudFailureRPCMixin — per-store cloud-failure UX configuration.

OP-26b | py_modules/unifideck/rpc/mixins/cloud_failure.py

Mixin equivalent of the two ``UIHandlers`` methods
``get_cloud_failure_behaviors`` and
``set_cloud_failure_behavior`` (OP-25h). Same allow-lists,
same validation rules, same config keys.

Reaches for ``self.config`` directly on the host plugin
class (the older composition style).
"""

from __future__ import annotations

from typing import Any

from unifideck.rpc import RpcError


class CloudFailureRPCMixin:
    """Per-store cloud-failure behaviour read/write RPC."""

    config: Any
    # Battle.net is deliberately absent: it declares
    # ``supports_cloud_saves=False`` (Blizzard titles that sync do it
    # in-game against Blizzard's own service, which we neither drive nor
    # observe), so there is no cloud failure of ours to have a behaviour
    # for. Adding a row here would offer the user a setting that can never
    # take effect. Microsoft is absent for the same reason — xCloud streams.
    _CLOUD_FAILURE_STORES: tuple[str, ...] = (
        "default",
        "epic",
        "gog",
        "amazon",
        "ubisoft",
    )
    _CLOUD_FAILURE_MODES: tuple[str, ...] = ("silent", "toast")

    async def get_cloud_failure_behaviors(self) -> Any:
        """Return the per-store cloud-failure behaviour map.

        Defaults to ``"toast"`` for any store not explicitly
        configured. The ``"default"`` entry is a fallback
        used for stores not in the explicit list.

        Returns:
            ``{store_id → "silent" | "toast"}`` for every
            store in ``_CLOUD_FAILURE_STORES``.
        """
        result = {}
        for store in self._CLOUD_FAILURE_STORES:
            result[store] = self.config.get_str(
                f"cloud.failure_behavior.{store}",
                "toast",
            )
        return result

    async def set_cloud_failure_behavior(self, store: str, value: str) -> Any:
        """Persist a cloud-failure behaviour override for one store.

        Strict validation: unsupported store or behaviour
        raises a typed error with the allowed list in the
        context dict so the frontend can show a clear error
        message.

        Args:
            store: store id (must be in
                ``_CLOUD_FAILURE_STORES``).
            value: behaviour (must be in
                ``_CLOUD_FAILURE_MODES``).

        Returns:
            ``{success: True, store, value}``.

        Raises:
            RpcError: ``unsupported_store`` or
                ``invalid_behavior`` on bad inputs.
        """
        if store not in self._CLOUD_FAILURE_STORES:
            raise RpcError(
                "unsupported_store",
                store=store,
                supported=list(self._CLOUD_FAILURE_STORES),
            )
        if value not in self._CLOUD_FAILURE_MODES:
            raise RpcError(
                "invalid_behavior",
                value=value,
                supported=list(self._CLOUD_FAILURE_MODES),
            )
        self.config.set(
            f"cloud.failure_behavior.{store}",
            value,
        )
        return {"success": True, "store": store, "value": value}
