"""config/config_persistence.py — Config load/save with atomic writes.
# OP-11b | config/config_persistence.py | Depends: (none)
"""
from __future__ import annotations
from typing import Any


async def load_config(path: str) -> dict[str, Any]:
    """Load JSON config file. Return empty dict on failure."""
    raise NotImplementedError("OP-11b")


async def save_config(path: str, data: dict[str, Any]) -> bool:
    """Atomic-write config to path. Return True on success."""
    raise NotImplementedError("OP-11b")


def merge_configs(defaults: dict, user: dict) -> dict:
    """Deep-merge user config over defaults."""
    raise NotImplementedError("OP-11b")
