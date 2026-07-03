"""Config validation RPC mixin for Plugin class.

OP-26i | rpc/mixins/config_validation.py
"""
from __future__ import annotations

from typing import Any


class ConfigValidationRPCMixin:
    """Boot-time config validation status accessor."""

    # The Plugin attribute is ``_config_validation_result`` (with
    # a leading underscore — see ``main.Plugin._validate_config``
    # and ``bootstrap.boot.boot_plugin``). The annotation here uses
    # the same name so type-checkers can see the contract; an
    # earlier version annotated ``config_validation_result`` and
    # read it without the underscore, which silently always hit
    # the fallback branch — the RPC returned an empty
    # ``{degraded, errors, warnings}`` dict even when validation
    # had flagged real issues at boot.
    _config_validation_result: Any = None

    async def get_config_validation_status(self) -> Any:
        """Return the boot-time config validation result."""
        result = getattr(self, "_config_validation_result", None)
        if result is None:
            return {"degraded": False, "errors": [], "warnings": []}
        return result
