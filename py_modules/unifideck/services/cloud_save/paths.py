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
    """Build the canonical local save directory for a game.

    Layout: ``<local_root>/<store>/<game_id>``. Strings are
    returned (rather than ``Path`` objects) to match the API of
    the rest of the cloud-save module, which is string-based for
    compatibility with subprocess callers.

    Args:
        local_root: typically ``<data_dir>/saves``.
        store: store identifier.
        game_id: store-specific game id.

    Returns:
        Absolute path string. Does not create the directory.
    """
    return str(Path(local_root) / store / game_id)


def remote_save_dir(cloud_root: str, store: str, game_id: str) -> str:
    """Build the canonical cloud-side mirror directory for a game.

    Symmetric to ``local_save_dir`` but rooted at ``cloud_root``.

    Args:
        cloud_root: user-configured cloud root (Steam Cloud
            mountpoint or Syncthing folder).
        store: store identifier.
        game_id: store-specific game id.

    Returns:
        Absolute path string under the cloud root.
    """
    return str(Path(cloud_root) / store / game_id)
