#!/usr/bin/env python3
"""Launch classic Linux GOG DOSBox wrappers with compatible legacy libraries."""

from __future__ import annotations

import os
import platform
import re
import shlex
import sys
from pathlib import Path


DOSBOX_CALL_RE = re.compile(r"run_dosbox\s+((?:\"[^\"]+\"\s*)+)")


def find_steam_runtime() -> Path | None:
    candidates = (
        Path.home() / ".steam" / "steam" / "ubuntu12_32" / "steam-runtime",
        Path.home() / ".local" / "share" / "Steam" / "ubuntu12_32" / "steam-runtime",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None


def build_runtime_library_paths(runtime_root: Path, arch_dir: str) -> list[str]:
    paths: list[str] = []
    for rel in (f"usr/lib/{arch_dir}", f"lib/{arch_dir}"):
        candidate = runtime_root / rel
        if candidate.exists():
            paths.append(str(candidate))
    return paths


def parse_dosbox_conf_args(start_script: Path) -> list[str]:
    content = start_script.read_text(encoding="utf-8", errors="ignore")
    match = DOSBOX_CALL_RE.search(content)
    if not match:
        raise ValueError(f"Could not find run_dosbox call in {start_script}")
    return shlex.split(match.group(1))


def launch_via_steam_runtime(runtime_root: Path | None, start_script: Path, args: list[str]) -> None:
    if runtime_root:
        run_sh = runtime_root / "run.sh"
        if run_sh.exists():
            os.execv(str(run_sh), [str(run_sh), str(start_script), *args])
    os.execv(str(start_script), [str(start_script), *args])


def main() -> None:
    if len(sys.argv) < 2:
        raise SystemExit("Usage: gog_linux_dosbox.py /path/to/start.sh [args...]")

    start_script = Path(sys.argv[1]).resolve()
    extra_args = [
        arg for arg in sys.argv[2:]
        if not re.match(r"^(epic|gog|amazon|ubisoft):", arg)
    ]

    runtime_root = find_steam_runtime()

    if extra_args:
        launch_via_steam_runtime(runtime_root, start_script, extra_args)

    root_dir = start_script.parent
    dosbox_dir = root_dir / "dosbox"
    if not dosbox_dir.is_dir():
        launch_via_steam_runtime(runtime_root, start_script, extra_args)

    arch = platform.machine().lower()
    if arch in {"x86_64", "amd64"}:
        binary = dosbox_dir / "dosbox_x86_64"
        bundled_lib_dir = dosbox_dir / "libs" / "x86_64"
        runtime_arch_dir = "x86_64-linux-gnu"
    elif arch in {"i686", "i386"}:
        binary = dosbox_dir / "dosbox_i686"
        bundled_lib_dir = dosbox_dir / "libs" / "i686"
        runtime_arch_dir = "i386-linux-gnu"
    else:
        launch_via_steam_runtime(runtime_root, start_script, extra_args)

    if not binary.exists() or not bundled_lib_dir.is_dir():
        launch_via_steam_runtime(runtime_root, start_script, extra_args)

    conf_args = parse_dosbox_conf_args(start_script)
    runtime_libs = build_runtime_library_paths(runtime_root, runtime_arch_dir) if runtime_root else []

    env = os.environ.copy()
    ld_parts = [str(bundled_lib_dir), *runtime_libs]
    if env.get("LD_LIBRARY_PATH"):
        ld_parts.append(env["LD_LIBRARY_PATH"])
    env["LD_LIBRARY_PATH"] = ":".join(dict.fromkeys(part for part in ld_parts if part))

    command = [str(binary)]
    for conf in conf_args:
        command.extend(["-conf", conf])
    command.extend(["-no-console", "-c", "exit"])

    os.chdir(root_dir)
    os.execvpe(str(binary), command, env)


if __name__ == "__main__":
    main()
