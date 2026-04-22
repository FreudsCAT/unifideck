#!/usr/bin/env python3
# OP-03a | bin/unifideck-launcher | Depends: OP-35c
from __future__ import annotations

import os

os.environ.pop("LD_LIBRARY_PATH", None)
os.environ.pop("LD_PRELOAD", None)

import sys
from pathlib import Path


def _bootstrap_path() -> None:
    plugin_dir = Path(__file__).resolve().parent.parent
    py_modules = plugin_dir / "py_modules"
    if py_modules.is_dir():
        sys.path.insert(0, str(py_modules))


def main() -> int:
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
