"""config/startup.py — Config validation at plugin startup.
# OP-11g | config/startup.py | Depends: OP-11a, OP-11e
"""
from __future__ import annotations
from typing import Any


async def validate_config_at_startup(
    bus, config, defaults_path: str, user_config_path: str,
) -> tuple[Any, bool]:
    """Validate config at startup. Returns (result, degraded)."""
    raise NotImplementedError("OP-11g")
