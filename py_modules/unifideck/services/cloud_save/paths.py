"""Per-game save-directory paths.

OP-17e | py_modules/unifideck/services/cloud_save/paths.py

Two functions to derive the canonical save-directory paths :

* ``local_save_dir(game, paths)`` — where the game writes its saves
  on the local Steam Deck (game-specific, often inside the prefix);
* ``remote_save_dir(game, paths)`` — where Unifideck caches the
  cloud-side mirror.
"""

from __future__ import annotations
from pathlib import Path


def local_save_dir(local_root: str, store: str, game_id: str) -> str:
    """Local save dir."""
    return str(Path(local_root) / store / game_id)


def remote_save_dir(cloud_root: str, store: str, game_id: str) -> str:
    """Remote save dir."""
    return str(Path(cloud_root) / store / game_id)
