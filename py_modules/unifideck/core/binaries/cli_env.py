"""core/binaries/cli_env.py — Clean environment for bundled CLI subprocesses.

WHY THIS EXISTS
---------------
The Decky backend (``PluginLoader``) is a PyInstaller-frozen binary. Frozen
processes rewrite the dynamic-loader environment for their OWN bundled libs
and leave it that way for every child they spawn, so ``os.environ`` inside the
plugin carries::

    LD_LIBRARY_PATH=/tmp/_MEIxxxx      # the loader's bundled libs
    LD_LIBRARY_PATH_ORIG=...           # PyInstaller's stash of the real value

Handing that to a child that is *not* the frozen app makes it link the wrong
libraries. The repo has hit this repeatedly and fixed it locally each time:
``curl`` picked up the Steam Runtime's old libssl (``stores/epic/playtime_api``
and ``stores/epic/achievements``), ``umu-run`` failed to start ``python3``
inside pressure-vessel with "libz.so.1" and exited 127
(``launcher/proton/infrastructure/umu_runtime``), and
``bin/unifideck-launcher`` pops both variables before it imports anything.

This module is that same fix, factored out for the bundled store CLIs, which
never had it.

The store CLIs made this urgent: legendary >=0.20.40 and gogdl >=1.2.2 ship a
Python **zipapp** rather than a PyInstaller ELF, so they run under the SYSTEM
``python3`` via a ``#!/usr/bin/env python3`` shebang. A frozen ELF carries its
own libraries and ignores both the loader variables and ``PYTHON*``; a
shebang-launched interpreter obeys all of them. The failure mode is not
hypothetical: with the plugin's ``py_modules`` on ``PYTHONPATH``, legendary's
``Cryptodome`` import resolved our vendored ``cffi`` and died with
``Exception: Version mismatch`` before it could parse a single argument.

Deliberately NOT scrubbed: ``PATH`` (the shebang needs it to find ``python3``)
and ``HOME``/``XDG_*`` (the zipapps extract native modules under the cache dir
those name, and pinning them here would strand the caches).
"""
from __future__ import annotations

import os

# Loader variables, plus PyInstaller's ``*_ORIG`` stashes. The stashes are
# dropped rather than restored: the pre-freeze value is Steam's, which is no
# more welcome in a non-Steam child than the ``_MEI`` one — restoring it is
# exactly the bug that made every GOG/Amazon/Ubisoft launch exit 127.
_LOADER_VARS = (
    "LD_LIBRARY_PATH",
    "LD_LIBRARY_PATH_ORIG",
    "LD_PRELOAD",
    "LD_PRELOAD_ORIG",
)

# Interpreter controls. Harmless to a frozen ELF, decisive for a zipapp:
# ``PYTHONHOME`` sends it to the wrong stdlib entirely, and ``PYTHONPATH``
# lets our vendored packages shadow the ones the zipapp bundles.
_PYTHON_VARS = (
    "PYTHONHOME",
    "PYTHONPATH",
)

SCRUBBED_VARS: tuple[str, ...] = (*_LOADER_VARS, *_PYTHON_VARS)


def clean_cli_env(
    overrides: dict[str, str] | None = None,
) -> dict[str, str]:
    """Return ``os.environ`` minus the vars that break a bundled CLI.

    Args:
      overrides: extra variables to set on top, applied AFTER scrubbing so
        a caller can still pass a deliberate ``PYTHONPATH`` if it ever
        needs one.

    Returns:
      A new dict — ``os.environ`` itself is never mutated, so this is safe
      to call from the long-lived backend process.
    """
    env = {k: v for k, v in os.environ.items() if k not in SCRUBBED_VARS}
    if overrides:
        env.update(overrides)
    return env


def scrub_cli_env(env: dict[str, str]) -> dict[str, str]:
    """Strip the same variables from an env dict the caller already built.

    For call sites that assemble their own environment (store credential
    builders, cloud-save strategies) instead of starting from
    :func:`clean_cli_env`. Mutates and returns ``env`` so it can be used
    inline at the ``subprocess`` call.
    """
    for var in SCRUBBED_VARS:
        env.pop(var, None)
    return env
