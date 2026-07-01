from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import Any

from unifideck.launcher.frontend_bridge import launcher_toast

logger = logging.getLogger(__name__)
# Short pause before a recoverable umu retry — gives a transient
# runtime/network hiccup a moment to clear and makes the "retrying in
# Ns" toast truthful.
_RETRY_BACKOFF_SECONDS = 3
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
    """Wipe the umu runtime cache so the next launch re-fetches it clean.

    umu names the Steam Linux Runtime by version (``steamrt3`` for umu
    ≤1.2, ``steamrt4`` for 1.3+); glob ``steamrt*`` so a corrupt runtime
    is cleared regardless of the bundled umu version — targeting only
    ``steamrt3`` missed 1.3's ``steamrt4``, making the recoverable-retry
    wipe a no-op.
    """
    targets = [
        *UMU_CACHE_DIR.glob("steamrt*"),
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
    # The Steam Linux Runtime (steamrt3) is downloaded by the first
    # ``umu-run`` and is the slowest part of a first-ever launch
    # (hundreds of MB), with no native progress — exactly the
    # "is it frozen?" gap the user hit. Toast once when it's missing so
    # the wait is expected. Fires only on the genuine first setup; the
    # cache then persists and is shared across every game.
    if not (UMU_CACHE_DIR / "steamrt3").exists():
        launcher_toast(
            "toasts.launcher.downloadingRuntime",
            i18n_title_key="toasts.launcher.firstTimeSetup",
        )
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
            rc = await _run_umu_once(argv, env, cwd, out, on_start)
            last_rc = rc
            logger.info("[launcher.umu] attempt %d exit code: %d", attempt, rc)
            if rc == 0:
                return 0
            if rc in _RECOVERABLE_CODES and attempt < max_attempts:
                logger.warning(
                    "[launcher.umu] recoverable rc=%d, wiping cache + retry",
                    rc,
                )
                launcher_toast(
                    "toasts.launcher.retryingUmu",
                    i18n_title_key="toasts.launcher.networkError",
                    i18n_params={
                        "seconds": _RETRY_BACKOFF_SECONDS,
                        "attempt": attempt + 1,
                        "max": max_attempts,
                    },
                    severity="warning",
                )
                cleanup_umu_runtime_cache()
                await asyncio.sleep(_RETRY_BACKOFF_SECONDS)
                continue
            return rc
        return last_rc
    finally:
        if game_log is not None:
            with contextlib.suppress(OSError):
                game_log.close()


async def _run_umu_once(
    argv: list[str],
    env: dict[str, str] | None,
    cwd: Path | None,
    out: Any,
    on_start: Callable[[object], None] | None,
) -> int:
    """Spawn one umu process, fire ``on_start``, await its exit code."""
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
    return await proc.wait()
