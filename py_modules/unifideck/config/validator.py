"""config/validator.py — JSON Schema config validation.
# OP-11e | config/validator.py | Depends: (none)
"""
from __future__ import annotations
from typing import Any


def validate_config(config: dict[str, Any], schema: dict[str, Any]) -> list[str]:
    """Validate config against JSON schema. Return list of errors."""
    raise NotImplementedError("OP-11e: use jsonschema library")
