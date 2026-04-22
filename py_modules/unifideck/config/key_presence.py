"""config/key_presence.py — Check required config keys at startup.
# OP-11f | config/key_presence.py | Depends: (none)
"""
from __future__ import annotations


def check_required_keys(config: dict, required: list[str]) -> list[str]:
    """Return list of missing required dotted keys."""
    raise NotImplementedError("OP-11f")
