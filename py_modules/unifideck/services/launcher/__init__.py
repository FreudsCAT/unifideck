"""Launcher service — game launch orchestration.

OP-20 | py_modules/unifideck/services/launcher/__init__.py

Re-exports ``LauncherService``. The service is the unified entry
point for launching any game on any store : it builds the Proton /
native launch command, applies pre-launch cloud sync, manages the
subprocess lifecycle, and propagates exit events to playtime
tracking and launch-history services.
"""

from __future__ import annotations
from .builder import build_standalone
from .service import LauncherService

__all__ = ["build_standalone", "LauncherService"]
