"""Shortcut service — Steam shortcut management.

OP-14 | py_modules/unifideck/services/shortcut/__init__.py

Re-exports ``ShortcutService``, the orchestration class for everything
related to Steam shortcuts (the ``shortcuts.vdf`` file Steam uses to
list non-Steam apps).

Internal mixins (``games_map``, ``events``, ``vdf_shortcuts``,
``persistence``, ``auth_shortcut``) are not re-exported — they're
glued together via inheritance into ``ShortcutService``.
"""

from __future__ import annotations
from .games_map import GameMapEntry, generate_app_id
from .service import UNIFIDECK_TAG, ShortcutService

__all__ = [
    "UNIFIDECK_TAG",
    "GameMapEntry",
    "ShortcutService",
    "generate_app_id",
]
