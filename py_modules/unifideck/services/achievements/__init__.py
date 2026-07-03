"""services/achievements — GOG achievement tracking (live toasts + summary).

Re-exports ``AchievementWatcher`` so the bootstrap wiring table can write
``from unifideck.services.achievements import AchievementWatcher``.
"""
from __future__ import annotations

from .watcher import AchievementWatcher

__all__ = ["AchievementWatcher"]
