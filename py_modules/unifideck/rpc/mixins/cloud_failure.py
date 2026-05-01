"""Cloud failure behaviour RPC mixin for Plugin class.

OP-26h | rpc/mixins/cloud_failure.py
"""
from __future__ import annotations

from typing import Any

from unifideck.rpc.errors import RpcError

_CLOUD_FAILURE_STORES = ("default", "epic", "gog", "amazon", "ubisoft")
_CLOUD_FAILURE_MODES = ("silent", "toast")


class CloudFailureRPCMixin:
    """Per-store cloud-save failure behaviour configuration."""

    config: Any

    async def get_cloud_failure_behaviors(self) -> Any:
        """Return per-store cloud failure behavior map."""
        return {
            store: self.config.get(
                f"cloud.failure_behavior.{store}", "toast",
            )
            for store in _CLOUD_FAILURE_STORES
        }

    async def set_cloud_failure_behavior(
        self, store: str, value: str,
    ) -> Any:
        """Persist a per-store cloud failure behavior override."""
        if store not in _CLOUD_FAILURE_STORES:
            raise RpcError(
                "unsupported_store",
                f"Must be one of {_CLOUD_FAILURE_STORES}",
                store=store,
            )
        if value not in _CLOUD_FAILURE_MODES:
            raise RpcError(
                "invalid_behavior",
                f"Must be one of {_CLOUD_FAILURE_MODES}",
                value=value,
            )
        self.config.set(f"cloud.failure_behavior.{store}", value)
        return {"store": store, "value": value}
