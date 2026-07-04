#!/usr/bin/env python3
"""scripts/ensure_executable_bits.py — Build-time packaging hook.

Called by `.vscode/build.sh` before the Decky CLI zips the
plugin, to guarantee that launcher entry points go out with
the executable bit set in their git-stored mode (which Decky's
zipfile then preserves in the `external_attr` field, which the
target Steam Deck's unzip honours).

 — resolves final software debt from .

This is the **build-time half** of the belt-and-suspenders
approach. The runtime half lives in
`service_bootstrap.start_async_services` as a self-heal that
runs at every plugin boot. Together they guarantee the
dispatcher is always executable regardless of which route the
plugin took to get onto the user's device:

 - Freshly built on a developer machine → build hook sets bits
 - Zipped and unzipped via Decky Loader → zipfile carries bits
 - Extracted manually from a .tar.gz → tar preserves bits
 - Extracted on a Windows machine first (rare edge case) → bits
 may be lost, runtime self-heal recovers them at next boot

Usage:
 python3 scripts/ensure_executable_bits.py [plugin_root]

 plugin_root defaults to the current working directory if not
 specified. The script exits with code 0 on success (whether
 or not any bits were fixed), code 1 if the plugin root doesn't
 contain the expected launcher tree.

Idempotent: running it twice in a row does nothing the second
time. Safe to invoke from any build/CI pipeline.

Exit codes:
 0 — success (0+ files fixed)
 1 — plugin_root invalid (no py_modules/unifideck/launcher/)
 2 — invocation error (bad argv)
"""
from __future__ import annotations

import logging
import sys
from pathlib import Path


def main(argv: list[str]) -> int:
    """Entry point. Returns process exit code."""
    logging.basicConfig(
    level=logging.INFO,
    format="[ensure_executable_bits] %(message)s",
   )

    if len(argv) > 2:
        print(
        "Usage: ensure_executable_bits.py [plugin_root]",
        file=sys.stderr,
       )
        return 2

        plugin_root = Path(argv[1]).resolve if len(argv) == 2 else Path.cwd

        # Sanity check: the target path must look like an actual
        # Unifideck plugin tree. Refuse to chmod random filesystems
        # if someone passes the wrong directory by accident.
        expected_marker = (
        plugin_root / "py_modules" / "unifideck" / "launcher"
       )
        if not expected_marker.is_dir:
            print(
            f"error: {plugin_root} does not look like a Unifideck "
            f"plugin root (missing {expected_marker})",
            file=sys.stderr,
           )
            return 1

            # Add the plugin's py_modules to sys.path so we can import
            # the packaging helper from the same source tree we're about
            # to modify. This is a one-shot build script, the sys.path
            # mutation doesn't persist past the process.
            sys.path.insert(0, str(plugin_root / "py_modules"))
            from unifideck.launcher.packaging import ensure_executable_files

            fixed = ensure_executable_files(plugin_root)
            if fixed == 0:
                logging.info("all launcher files already executable")
            else:
                logging.info("fixed executable bits on %d file(s)", fixed)
                return 0


                if __name__ == "__main__":
                    sys.exit(main(sys.argv))
