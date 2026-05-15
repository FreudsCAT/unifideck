from __future__ import annotations

import os
import platform
import re
import shlex
import sys
from pathlib import Path
from typing import NoReturn

DOSBOX_CALL_RE = re.compile(r'run_dosbox\s+((?:\"[^\"]+\"\s*)+)')
def find_steam_runtime() -> Path | None:
    """Find steam runtime."""
    candidates = (
        Path.home() / ".steam" / "steam" / "ubuntu12_32" / "steam-runtime",
        Path.home() / ".local" / "share" / "Steam" / "ubuntu12_32" / "steam-runtime",
    )
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return None
def build_runtime_library_paths(
    runtime_root: Path, arch_dir: str,
) -> list[str]:
    """Build runtime library paths."""
    paths: list[str] = []
    for rel in (f"usr/lib/{arch_dir}", f"lib/{arch_dir}"):
        candidate = runtime_root / rel
        if candidate.exists():
            paths.append(str(candidate))
    return paths
def parse_dosbox_conf_args(start_script: Path) -> list[str]:
    """Parse dosbox conf args."""
    content = start_script.read_text(encoding="utf-8", errors="ignore")
    match = DOSBOX_CALL_RE.search(content)
    if not match:
        raise ValueError(
            f"Could not find run_dosbox call in {start_script}",
        )
    return shlex.split(match.group(1))
def launch_via_steam_runtime(
    runtime_root: Path | None,
    start_script: Path,
    args: list[str],
) -> NoReturn:
    """Launch via steam runtime."""
    if runtime_root:
        run_sh = runtime_root / "run.sh"
        if run_sh.exists():
            # ``execv`` (no shell) replaces this Python process with
            # ``run.sh``; running this through a shell would defeat
            # the purpose of the wrapper (extra PID, signal forwarding,
            # quoting hazards).
            os.execv(str(run_sh), [str(run_sh), str(start_script), *args])  # noqa: S606 — intentional process exec; arg list is fully controlled internal data
    os.execv(str(start_script), [str(start_script), *args])  # noqa: S606 — intentional process exec; arg list is fully controlled internal data
def _parse_argv() -> tuple[Path, list[str]]:
    """Parse argv."""
    if len(sys.argv) < 2:
        raise SystemExit(
            "Usage: python -m "
            "unifideck.launcher.proton.gog_linux_dosbox "
            "/path/to/start.sh [args...]",
        )
    start_script = Path(sys.argv[1]).resolve()
    extra_args = [
        arg for arg in sys.argv[2:]
        if not re.match(r"^(epic|gog|amazon|ubisoft):", arg)
    ]
    return start_script, extra_args

def _select_architecture(
    dosbox_dir: Path,
) -> tuple[Path, Path, str] | None:

    """Select architecture."""
    arch = platform.machine().lower()
    if arch in {"x86_64", "amd64"}:
        return (
            dosbox_dir / "dosbox_x86_64",
            dosbox_dir / "libs" / "x86_64",
            "x86_64-linux-gnu",
        )
    if arch in {"i686", "i386"}:
        return (
            dosbox_dir / "dosbox_i686",
            dosbox_dir / "libs" / "i686",
            "i386-linux-gnu",
        )
    return None
def _build_env(
    bundled_lib_dir: Path,
    runtime_root: Path | None,
    runtime_arch_dir: str,
) -> dict[str, str]:
    """Build env."""
    runtime_libs = (
        build_runtime_library_paths(runtime_root, runtime_arch_dir)
        if runtime_root else []
    )
    env = os.environ.copy()
    ld_parts = [str(bundled_lib_dir), *runtime_libs]
    if env.get("LD_LIBRARY_PATH"):
        ld_parts.append(env["LD_LIBRARY_PATH"])
    env["LD_LIBRARY_PATH"] = ":".join(
        dict.fromkeys(part for part in ld_parts if part),
    )
    return env
def main() -> None:
    """Main."""
    start_script, extra_args = _parse_argv()
    runtime_root = find_steam_runtime()
    if extra_args:
        launch_via_steam_runtime(
            runtime_root, start_script, extra_args,
        )
    root_dir = start_script.parent
    dosbox_dir = root_dir / "dosbox"
    if not dosbox_dir.is_dir():
        launch_via_steam_runtime(
            runtime_root, start_script, extra_args,
        )
    arch_info = _select_architecture(dosbox_dir)
    if arch_info is None:
        launch_via_steam_runtime(
            runtime_root, start_script, extra_args,
        )
    binary, bundled_lib_dir, runtime_arch_dir = arch_info
    if not binary.exists() or not bundled_lib_dir.is_dir():
        launch_via_steam_runtime(
            runtime_root, start_script, extra_args,
        )
    conf_args = parse_dosbox_conf_args(start_script)
    env = _build_env(bundled_lib_dir, runtime_root, runtime_arch_dir)
    command = [str(binary)]
    for conf in conf_args:
        command.extend(["-conf", conf])
    command.extend(["-no-console", "-c", "exit"])
    os.chdir(root_dir)
    # ``execvpe`` (no shell) replaces this Python process with
    # the DOSBox binary; bypassing a shell avoids quoting hazards
    # around the per-game ``.conf`` paths and keeps the env exactly
    # as we built it above.
    os.execvpe(str(binary), command, env)  # noqa: S606 — intentional process exec; arg list is fully controlled internal data
if __name__ == "__main__":
    main()
