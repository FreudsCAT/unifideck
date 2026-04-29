"""unifideck.paths — Single source of truth for plugin path resolution.

Before this module, three independent call sites each rolled
their own heuristic to locate the plugin root on disk:

 - ``bin/unifideck-launcher`` : ``Path(__file__).parent.parent``
 - ``launcher/dispatcher.py`` : walk up looking for ``plugin.json``
 - ``launcher/bootstrap.py``  : ``Path(__file__).parents[3]``

All three are *correct* in the normal case (Decky Loader
installs the plugin at ``~/homebrew/plugins/unifideck/``) but
they drift in edge cases:

 - If the plugin is symlinked, the ``parents[N]`` heuristics
   may point at the symlink's target instead of the install
   location.
 - If ``plugin.json`` is accidentally shipped in a subdirectory,
   the walk-up heuristic may resolve to the wrong parent.
 - If Decky Loader changes its plugin layout, all three sites
   need to be updated in lockstep.

This module provides a single ``resolve_plugin_dir`` function
that every caller imports. Any future layout change is one
edit, not three.

## Resolution strategy

Tries locations in order of increasing fragility:

1. ``UNIFIDECK_PLUGIN_DIR`` environment variable (explicit
   override; useful for tests and non-Decky installations).
2. ``DECKY_PLUGIN_DIR`` environment variable (set by Decky
   Loader itself for the plugin's own process — authoritative
   when present).
3. ``~/homebrew/plugins/unifideck`` (the canonical Decky
   install path — reliable when the launcher process is
   spawned by Steam outside of Decky's own env).
4. Walk upward from ``__file__`` looking for ``plugin.json``
   (last resort for unusual install layouts).

Returns a ``Path`` object, not a string, because all consumers
eventually do ``Path(plugin_dir) / "subdir" / "file"``.

## Why not a class

A class would suggest state that isn't there. This is a pure
function that consults env vars and filesystem once per call.
Callers that need the result more than once should cache it
themselves — no hidden module-level cache here to avoid test-
isolation issues.
"""
from __future__ import annotations

import logging
import os
from pathlib import Path

logger = logging.getLogger(__name__)

# The canonical install path when Decky Loader manages the
# plugin. Hardcoded because Decky itself hardcodes this layout
# — changing it would break every existing deployment.
_DECKY_DEFAULT_PATH = Path.home() / "homebrew" / "plugins" / "unifideck"


def resolve_plugin_dir(start: Path | None = None) -> Path:
    """Locate the Unifideck plugin root directory.

    Args:
        start: Optional Path from which to walk upward as a last
            resort (step 4 of the resolution strategy). If None,
            defaults to this module's own ``__file__``. Tests
            can pass a fake start location to exercise the walk.

    Returns:
        A ``Path`` pointing at the plugin root. Callers should
        use this as the base for constructing paths to
        ``defaults/``, ``data/``, ``py_modules/``, etc.

    Notes:
        Never raises. If no strategy succeeds, returns
        ``_DECKY_DEFAULT_PATH`` as a best-effort fallback
        (calling code that touches the filesystem on the returned
        path will surface the real error in context).

    """
    # 1. Explicit override — highest priority, intended for
    # tests and custom deployments.
    explicit = os.environ.get("UNIFIDECK_PLUGIN_DIR")
    if explicit:
        p = Path(explicit).expanduser()
        if p.is_dir():
            return p
        logger.warning(
            "[paths] UNIFIDECK_PLUGIN_DIR=%s is not a "
            "directory, ignoring",
            explicit,
        )

    # 2. Decky's own env var — authoritative when we're running
    # in Decky's Python process.
    decky = os.environ.get("DECKY_PLUGIN_DIR")
    if decky:
        p = Path(decky).expanduser()
        if p.is_dir():
            return p

    # 3. Canonical Decky install path — works when the launcher
    # is spawned by Steam without inheriting Decky's env.
    if _DECKY_DEFAULT_PATH.is_dir():
        return _DECKY_DEFAULT_PATH

    # 4. Walk upward from start file looking for plugin.json.
    # Last resort for unusual layouts (symlinked devs, CI, etc.)
    here = (start or Path(__file__)).resolve()
    for parent in here.parents:
        if (parent / "plugin.json").is_file():
            return parent

    # Nothing worked. Return the canonical default so downstream
    # filesystem operations at least fail with a predictable
    # path in the error message instead of something randomly
    # derived from __file__.
    logger.warning(
        "[paths] plugin directory could not be resolved, "
        "falling back to %s", _DECKY_DEFAULT_PATH,
    )
    return _DECKY_DEFAULT_PATH


def resolve_py_modules_dir() -> Path:
    """Return ``<plugin_dir>/py_modules``.

    Small convenience for the launcher wrapper which needs to
    insert this directory into ``sys.path`` before importing
    any unifideck module.
    """
    return resolve_plugin_dir() / "py_modules"
