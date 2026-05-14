from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path

from unifideck.core.types import Result
from unifideck.launcher.types.context import LaunchContext, RuntimeState
from unifideck.launcher.types.errors import DependencyMissingError, GameFailedError

logger = logging.getLogger(__name__)
STEAM_RUNTIME_CANDIDATES = [
    "~/.steam/steam/ubuntu12_32/steam-runtime/run.sh",
    "~/.local/share/Steam/ubuntu12_32/steam-runtime/run.sh",
]
def _find_steam_runtime() -> Path | None:
    """Find steam runtime."""
    for candidate in STEAM_RUNTIME_CANDIDATES:
        path = Path(candidate).expanduser()
        if path.is_file():
            return path
    return None
def _restore_steam_env(env: dict[str, str]) -> None:
    """Restore steam env."""
    steam_env = Path("~/.steam/steam.env").expanduser()
    if not steam_env.is_file():
        return
    try:
        for raw_line in steam_env.read_text(
            encoding="utf-8", errors="replace",
        ).splitlines():
            line = raw_line.strip()
            if not line or line.startswith("#"):
                continue
            if "=" not in line:
                continue
            key, _, value = line.partition("=")
            if key in ("STEAM_OVERLAY", "STEAM_INPUT"):
                env[key] = value
    except OSError:
        pass
def _is_gog_dosbox_wrapper(ctx: LaunchContext) -> bool:
    """Is GOG dosbox wrapper."""
    return (
        ctx.store == "gog"
        and ctx.exe_path.name == "start.sh"
    )

async def native_launch(
    ctx: LaunchContext,
    state: RuntimeState,
) -> Result:

    """Native launch."""
    exe_path = ctx.exe_path
    if not exe_path.is_file():
        raise DependencyMissingError(
            f"Native Linux executable not found: {exe_path}",
            context={"exe": str(exe_path), "store": ctx.store},
        )
    try:
        # 0o755 (rwxr-xr-x) is the standard Linux mode for an
        # executable file; we expect the game binary to be world-
        # executable so the user (and any group) can launch it.
        # ``exe_path`` is already a ``Path`` (from ``ctx.exe_path``)
        # so we don't re-wrap. ``chmod`` is sync I/O — dispatch it
        # off the event loop with ``asyncio.to_thread`` to avoid
        # blocking other coroutines on a slow filesystem (NFS,
        # external drive). The S103 noqa we used to carry on
        # ``os.chmod`` is no longer needed: bandit's S103 rule only
        # flags ``os.chmod``, not ``Path.chmod``, so silencing it
        # would now be a stale comment.
        await asyncio.to_thread(exe_path.chmod, 0o755)
    except OSError as e:
        logger.debug("[native] chmod 755 failed on %s: %s", exe_path, e)
    env = _prepare_launch_env(ctx)
    argv = _build_launch_argv(ctx, state, exe_path)
    cwd = ctx.work_dir if ctx.work_dir.is_dir() else exe_path.parent
    logger.info(
        "[launcher.native] spawning: argv=%s cwd=%s",
        argv[:3],
        cwd,
    )
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=env,
        cwd=str(cwd),
        stdout=None,
        stderr=None,
        start_new_session=True,
    )
    rc = await proc.wait()
    state.game_exit_code = rc
    logger.info("[launcher.native] game exited rc=%d", rc)
    if rc != 0:
        raise GameFailedError(
            f"Native Linux game exited with code {rc}",
            subprocess_rc=rc,
            context={
                "store": ctx.store,
                "game_id": ctx.game_id,
            },
        )
    return Result(success=True, store=ctx.store)
def _prepare_launch_env(ctx: LaunchContext) -> dict[str, str]:
    """Prepare launch env."""
    env = dict(os.environ)
    env.update(ctx.env_overrides)
    _restore_steam_env(env)
    return env

def _build_launch_argv(
    ctx: LaunchContext,
    state: RuntimeState,
    exe_path: Path,
) -> list[str]:

    """Build launch argv."""
    argv: list[str] = list(state.wrappers)
    if _is_gog_dosbox_wrapper(ctx):
        logger.info(
            "[launcher.native] using GOG DOSBox wrapper module",
        )
        argv.extend([
            "python3", "-m",
            "unifideck.launcher.proton.gog_linux_dosbox",
            str(exe_path),
        ])
    else:
        runtime = _find_steam_runtime()
        if runtime is not None:
            logger.info(
                "[launcher.native] using Steam Runtime: %s", runtime,
            )
            argv.extend([str(runtime), str(exe_path)])
        else:
            logger.info(
                "[launcher.native] no Steam Runtime, direct exec",
            )
            argv.append(str(exe_path))
    argv.extend(state.game_args)
    return argv
