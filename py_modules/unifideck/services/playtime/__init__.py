"""Playtime service — per-game session tracking.

OP-18 | py_modules/unifideck/services/playtime/__init__.py

Re-exports ``PlaytimeService``. The service records launch / exit
events per game and exposes aggregates (total playtime, sessions
count, last played) for the UI and for export to community trackers.
"""

from __future__ import annotations
from .service import PlaytimeService

__all__ = ["PlaytimeService"]
