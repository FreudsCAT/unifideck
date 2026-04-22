"""config/user_config_path.py — Resolve user config file path.
# OP-11h | config/user_config_path.py | Depends: (none)
"""
from __future__ import annotations


def resolve_user_config_path() -> str:
    """Return the path to the user config.json file."""
    raise NotImplementedError("OP-11h: ~/.config/unifideck/config.json")
