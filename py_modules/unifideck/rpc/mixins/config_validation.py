"""Config validation RPC mixin for Plugin class.

OP-26i | rpc/mixins/config_validation.py
"""
from __future__ import annotations

from typing import Any


class ConfigValidationRPCMixin:
    """Boot-time config validation status accessor."""

    config_validation_result: Any = None

    async def get_config_validation_status(self) -> Any:
        """Return the boot-time config validation result."""
        result = getattr(self, "config_validation_result", None)
        if result is None:
            return {"degraded": False, "errors": [], "warnings": []}
        return result
