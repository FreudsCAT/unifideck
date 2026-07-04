"""py_modules/unifideck/config/ — Configuration subpackage.

Consolidated. This subpackage now owns the ENTIRE
config-related public API of Unifideck's backend:

  - ConfigManager : 3-layer runtime config (defaults, user,
                           fallback) with dot-notation lookup and
                           typed getters (was core/config_manager.py)

  - load_json_layer : tolerant JSON loader used for both the
                           shipped defaults and user overrides (was
                           core/config_persistence.py)

  - atomic_write_json : write-to-temp-then-rename helper that
                           guarantees config.json is never left
                           truncated on a crash (same source file)

  - validate_i18n_schema : i18n section validator that cross-checks
                           the locale identifiers against the
                           scripts/locale_config catalogue (was
                           core/config_schema.py, renamed to
                           i18n_schema.py to dispel the name clash
                           with config/schema.json)

  - ConfigSchemaError : exception raised by validate_i18n_schema

  - ConfigValidator : JSON Schema Draft-07 validator for the
                           whole config.json shape, emits
                           CONFIG_VALIDATION_* events (,
                           already in this package)

  - ValidationResult : dataclass holding the outcome of a
                           ConfigValidator run

  - ValidationError : one entry inside a ValidationResult
                           describing a single violation

  - schema.json : formal JSON Schema (Draft-07) describing
                           the full shape of config.json

Legacy entry points at unifideck.core.config_manager,
unifideck.core.config_persistence and unifideck.core.config_schema
are preserved as deprecated shims for backward compatibility with
code written before v0.7.0. They emit a DeprecationWarning on
import and will be removed in v0.9.0.

Typical usage from main.py::

    from unifideck.config import (
        ConfigManager,
        ConfigValidator,
        ValidationResult,
    )

    # 1. Validate the files on disk first
    validator = ConfigValidator(bus=self.bus)
    result = await validator.validate_config(
        defaults_path="defaults/config.json",
        user_path="~/.config/unifideck/config.json",
    )

    # 2. Then load into a ConfigManager. If validation failed,
    # degraded mode ignores user overrides and keeps only the
    # shipped defaults.
    config = ConfigManager(
        defaults_path="defaults/config.json",
        user_path=None if not result.success else "~/.config/unifideck/config.json",
    )
"""
from .config_manager import ConfigManager
from .config_persistence import (
    atomic_write_json,
    load_json_layer,
)
from .i18n_schema import (
    ConfigSchemaError,
    validate_i18n_schema,
)
from .validator import (
    ConfigValidator,
    ValidationError,
    ValidationResult,
)

__all__ = [
    # Runtime config
    "ConfigManager",
    "ConfigSchemaError",
    # JSON Schema validator
    "ConfigValidator",
    "ValidationError",
    "ValidationResult",
    "atomic_write_json",
    # Persistence helpers
    "load_json_layer",
    # i18n schema validation
    "validate_i18n_schema",
]
