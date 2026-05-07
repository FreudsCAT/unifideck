"""services/cloud_save/paths.py"""
from __future__ import annotations

from pathlib import Path


def local_save_dir(local_root: str, store: str, game_id: str) -> str:
    """Return the absolute path for a game's local save directory."""
    return str(Path(local_root) / store / game_id)


def remote_save_dir(cloud_root: str, store: str, game_id: str) -> str:
    """Return the absolute path for a game's remote cloud save directory."""
    return str(Path(cloud_root) / store / game_id)
