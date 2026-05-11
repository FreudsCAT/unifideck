"""user_info.py — Frozen value-object for the GOG user record.

# OP-52e | py_modules/unifideck/stores/gog/tokens/user_info.py | Depends: (none)
"""
from __future__ import annotations

from dataclasses import dataclass


@dataclass
class GOGUserInfo:
    """GOG user info."""

    username: str = ''
    galaxy_user_id: str = ''
