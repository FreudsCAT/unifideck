from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)
UMU_CACHE_DIR = Path("~/.local/share/umu").expanduser()
_LAUNCHES_DIR = Path("~/.local/share/unifideck/launches").expanduser()
_RECOVERABLE_CODES = {2, 74}


def _open_game_log() -> Any:
    """Open the per-launch game-output log for umu stdout+stderr.

    Proton / Wine / the game itself write to stdout+stderr, which the
    Python logging archive does NOT capture — so a game that exits
    nonzero left no trace and had to be reproduced by hand. Routing
    that output to ``launches/<launch_id>.game.log`` makes every
    failure diagnosable from disk. Returns ``None`` on any error, in
    which case the caller inherits stdout/stderr as before.
    """
    from unifideck.launcher.diagnostics.correlation import get_launch_id
    try:
        _LAUNCHES_DIR.mkdir(parents=True, exist_ok=True)
        path = _LAUNCHES_DIR / f"{get_launch_id()}.game.log"
        return path.open("a", encoding="utf-8", errors="replace")
    except OSError as e:
        logger.debug("[launcher.umu] game log open failed: %s", e)
        return None
def cleanup_umu_runtime_cache() -> None:
    """Cleanup UMU runtime cache."""
    targets = [
        UMU_CACHE_DIR / "steamrt3",
        UMU_CACHE_DIR / "compatibilitytool.vdf",
        UMU_CACHE_DIR / ".ref",
    ]
    for target in targets:
        if target.is_dir():
            shutil.rmtree(target, ignore_errors=True)
        elif target.exists():
            with contextlib.suppress(OSError):
                target.unlink()
    logger.info("[launcher.umu] cache cleaned: %s", UMU_CACHE_DIR)
def ensure_umu_runtime_ready() -> None:
    """Ensure UMU runtime ready."""
    UMU_CACHE_DIR.mkdir(parents=True, exist_ok=True)
    os.environ["UMU_LOG"] = "1"
    os.environ["UMU_NO_PROTON"] = "0"
    config_dir = Path("~/.config/umu").expanduser()
    config_dir.mkdir(parents=True, exist_ok=True)

async def run_umu_with_retry(
    argv: list[str],
    *,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    max_attempts: int = 2,
    on_start: Callable[[object], None] | None = None,
) -> int:

    """Run UMU with retry."""
    last_rc = 1
    game_log = _open_game_log()
    out = game_log if game_log is not None else None
    try:
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "[launcher.umu] run attempt %d/%d: %s (output → %s)",
                attempt, max_attempts, argv[:3],
                "game.log" if out is not None else "inherited",
            )
            proc = await asyncio.create_subprocess_exec(
                *argv,
                env=env,
                cwd=str(cwd) if cwd else None,
                stdout=out,
                stderr=out,
                start_new_session=True,
            )
            if on_start is not None:
                try:
                    on_start(proc)
                except Exception:
                    logger.exception("[launcher.umu] on_start callback failed")
            rc = await proc.wait()
            last_rc = rc
            logger.info("[launcher.umu] attempt %d exit code: %d", attempt, rc)
            if rc == 0:
                return 0
            if rc in _RECOVERABLE_CODES and attempt < max_attempts:
                logger.warning(
                    "[launcher.umu] recoverable rc=%d, wiping cache + retry",
                    rc,
                )
                cleanup_umu_runtime_cache()
                continue
            return rc
        return last_rc
    finally:
        if game_log is not None:
            with contextlib.suppress(OSError):
                game_log.close()
