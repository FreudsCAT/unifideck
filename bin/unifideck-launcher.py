#!/usr/bin/env python3
"""Steam shortcut launcher entry point — legacy CLI wrapper.

This thin wrapper is what Steam invokes when a user clicks a
non-Steam shortcut created by Unifideck. It runs in Steam's own
process context, where ``LD_LIBRARY_PATH`` and ``LD_PRELOAD`` are
populated by the Steam Runtime — values that systematically break
non-Steam binaries (Wine, Proton, store CLIs). Both variables are
scrubbed before any Python import so the dispatcher inherits a
clean environment.

The wrapper has two responsibilities :

    1. Sanitise the inherited environment.
    2. Prepend the plugin's ``py_modules`` directory to ``sys.path``
       so ``unifideck.launcher.dispatcher`` resolves at import time.

All real work is delegated to ``dispatcher_main(argv)``.
"""

from __future__ import annotations

import os

# Steam Runtime variables must be scrubbed before any other module
# is imported, including ``sys`` and ``pathlib`` — the dispatcher
# spawns subprocesses (Wine, Proton, store CLIs) that fail
# unpredictably when these variables leak into their environment.
os.environ.pop("LD_LIBRARY_PATH", None)
os.environ.pop("LD_PRELOAD", None)

import sys
from pathlib import Path

def _bootstrap_path() -> None:
    """Make the plugin's ``py_modules`` directory importable.

    Resolves the plugin root relative to this script's location
    (``<plugin>/bin/unifideck-launcher``) and inserts
    ``<plugin>/py_modules`` at the front of ``sys.path`` so the
    ``unifideck`` package imports cleanly regardless of the
    caller's working directory.

    The function is a no-op when ``py_modules/`` is missing — that
    case is reported later by ``main()`` with a clearer error
    message than ``ImportError: No module named 'unifideck'``.
    """
    plugin_dir = Path(__file__).resolve().parent.parent
    py_modules = plugin_dir / "py_modules"
    if py_modules.is_dir():
        sys.path.insert(0, str(py_modules))

def main() -> int:
    """Entry point — bootstrap, hand off to the dispatcher, return its code.

    Returns ``2`` if ``unifideck.launcher.dispatcher`` cannot be
    imported (broken install, missing ``py_modules`` directory,
    syntax error in the dispatcher itself). Otherwise returns
    whatever ``dispatcher_main`` returns ; Steam surfaces a
    non-zero exit code as a "game failed to launch" toast.

    The two stderr lines emitted on import failure carry the
    exception message and the resolved plugin directory — together
    they let the user diagnose a broken install without attaching
    a debugger to the Steam process.
    """
    _bootstrap_path()
    try:
        from unifideck.launcher.dispatcher import main as dispatcher_main
    except ImportError as exc:
        print(
            f"[unifideck-launcher] failed to import dispatcher: {exc}",
            file=sys.stderr,
        )
        print(
            f"[unifideck-launcher] plugin_dir="
            f"{Path(__file__).resolve().parent.parent}",
            file=sys.stderr,
        )
        return 2
    return dispatcher_main(sys.argv)

if __name__ == "__main__":
    sys.exit(main())
