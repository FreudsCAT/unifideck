"""ConfigValidationRPCMixin — surface the startup config-validation outcome.

OP-26c | py_modules/unifideck/rpc/mixins/config_validation.py

Mixin equivalent of ``UIHandlers.get_config_validation_status``
(OP-25h). Reads ``_config_validation_result`` from the host
plugin class (stamped there by the bootstrap sequence after
validation runs) and produces a JSON-friendly status dict for
the frontend's health-check banner.
"""

from __future__ import annotations

from typing import Any


class ConfigValidationRPCMixin:
    """Single-method mixin exposing the config-validation result."""

    async def get_config_validation_status(self) -> Any:
        """Return the result of the startup config-validation pass.

        Three states:

        * **No result captured** (bootstrap skipped
          validation) → return a healthy default
          (``degraded=False``, no errors).
        * **Successful validation** →
          ``degraded=not result.success`` (so ``False``),
          plus counts and flags.
        * **Failed validation** → ``degraded=True`` with the
          first 20 errors (path + source + message).

        The 20-error cap prevents bloated payloads when many
        keys fail simultaneously.

        Returns:
            Validation status dict for the frontend's
            health-check banner.
        """
        result = getattr(self, "_config_validation_result", None)
        if result is None:
            return {
                "degraded": False,
                "defaults_validated": True,
                "user_overrides_present": False,
                "error_count": 0,
                "errors": [],
            }
        return {
            "degraded": not result.success,
            "defaults_validated": result.defaults_validated,
            "user_overrides_present": result.user_overrides_present,
            "error_count": len(result.errors),
            "errors": [
                {
                    "source": e.source,
                    "path": e.path,
                    "message": e.message,
                }
                for e in result.errors[:20]
            ],
        }
