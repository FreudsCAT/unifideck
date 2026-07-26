from __future__ import annotations

import asyncio
import contextlib
import logging
import os
import shutil
import signal
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
# umu-run picks the Steam Runtime *generation* per Proton build (it reads
# the selected PROTONPATH's own toolmanifest.vdf) — a newer GE-Proton can
# require "steamrt4" instead of the default "sniper"/"steamrt3". Mirrors
# the variant names in umu's own bundled ``umu/__init__.py``
# (``__runtime_versions__``). Managing only "steamrt3" here made our own
# cache checks/wipes a silent no-op for anyone on a build that resolved to
# a different variant — covering all three keeps them meaningful
# regardless of which runtime a given Proton build actually uses.
UMU_RUNTIME_VARIANTS = ("steamrt2", "steamrt3", "steamrt4")
_RECOVERABLE_CODES = {2, 74, 127}
# Recoverable codes whose likely cause is a corrupt/incomplete steamrt
# runtime bootstrap — the only ones that justify wiping the *shared*
# runtime cache (hundreds of MB, re-downloaded on the next launch of
# ANY game) before a retry. 127 (command-not-found) is recoverable but
# is NOT a runtime-corruption signal, so it retries WITHOUT the
# expensive nuke. Wiping the shared cache on every recoverable failure
# was both wasteful and — paired with the old "Network Error" title —
# actively misleading about the real failure.
_RUNTIME_CORRUPTION_CODES = {2, 74}
# Returned when a bounded umu step is force-killed for exceeding its
# timeout. Never in ``_RECOVERABLE_CODES`` on purpose: a hung
# Proton/Wine boot (e.g. a broken auto-updated Proton-Experimental
# build spinning wineserver forever) will just hang again on retry, so
# the caller should fail the step rather than loop.
UMU_TIMEOUT_RC = 124


def _kill_process_group(proc: asyncio.subprocess.Process) -> None:
    """Best-effort SIGKILL of ``proc``'s whole process group.

    ``start_new_session=True`` (see :func:`_run_umu_once`) makes the
    spawned umu-run its own session/process-group leader, so killing
    just ``proc.pid`` would leave every descendant running untouched —
    pressure-vessel, wineserver, the simulated Wine boot. A broken
    Proton build left exactly such trees spinning wineserver at ~14%
    CPU indefinitely, wedging the serial install queue. Killing the
    group reaps the whole tree.

    Mirrors ``prefix_init._kill_process_group`` (the createprefix path
    already does this); kept as a local copy to avoid an import-linter
    layer dependency from ``infrastructure`` onto ``compat``.
    """
    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as e:
        logger.warning("[launcher.umu] failed to kill process group: %s", e)


def _reap_prefix_wineserver(env: dict[str, str] | None) -> None:
    """Also SIGKILL the wineserver bound to ``env``'s WINEPREFIX.

    ``_kill_process_group`` misses it: a ``waitforexitandrun`` wineserver
    detaches from the umu-run session and survives the killpg, keeping its
    ``/tmp/.wine-<uid>/server-<dev>-<ino>/lock``. Left alive, it deadlocks
    the NEXT run against the same prefix (retries stack stuck wineservers
    on one lock — the observed install-warmup wedge). Reaping it here lets
    the retry get a clean server. Best-effort; no WINEPREFIX → nothing to do.
    """
    prefix = (env or {}).get("WINEPREFIX")
    if not prefix:
        return
    try:
        from unifideck.launcher.proton.infrastructure.wineserver_reap import (
            reap_prefix_wineserver,
        )
        reap_prefix_wineserver(Path(prefix))
    except Exception:
        logger.exception("[launcher.umu] wineserver reap failed for %s", prefix)


def open_game_log() -> Any:
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
        *(UMU_CACHE_DIR / variant for variant in UMU_RUNTIME_VARIANTS),
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
def _runtime_entry_point_ok(variant_dir: Path) -> bool:
    """Return whether ``variant_dir`` has a usable umu entry point.

    Mirrors umu's OWN launch-time gate (``build_command`` does
    ``entry_point.is_file()`` on ``<variant>/umu``): the ``umu`` symlink
    must resolve to an existing ``_v2-entry-point``. ``Path.is_file()``
    follows symlinks, so a *missing* ``umu`` file AND a *dangling* ``umu``
    symlink both return ``False`` — exactly the two states that make umu
    raise "Runtime Platform missing or download incomplete" *after* it has
    already logged "<variant> is up to date".
    """
    return (variant_dir / "umu").is_file()
def repair_incomplete_umu_runtime() -> None:
    """Wipe any runtime variant that is present but has no umu entry point.

    UD-084: a umu setup that died after extracting the runtime payload
    (``<variant>_platform_*`` / ``pressure-vessel`` / ``VERSIONS.txt``) but
    before its LAST step — creating the ``umu -> _v2-entry-point`` symlink
    (umu's ``_install_umu`` does that in a ``finally``) — leaves a runtime
    that umu's own ``_update_umu`` treats as "up to date" (it only checks
    the platform dir / pressure-vessel / VERSIONS.txt, never the entry
    point). The next launch then dies in ``build_command`` with
    ``FileNotFoundError``, which umu exits with a code OUTSIDE our
    ``_RECOVERABLE_CODES`` (0 when the bundled zipapp swallows it, 1 when a
    field build re-raises) — so ``run_umu_with_retry`` never retries or
    wipes, and the user stays wedged.

    Deleting just the broken variant dir lets umu re-download it cleanly on
    the next ``umu-run`` this same launch; healthy sibling variants are left
    untouched (surgical). Cheap — one ``stat`` per existing variant — and a
    no-op on a healthy runtime, so it is safe to call on every launch. Safe
    without locking: launches run serially and this runs before any umu
    process is spawned, so no concurrent umu holds ``umu.lock`` here.
    """
    for variant in UMU_RUNTIME_VARIANTS:
        variant_dir = UMU_CACHE_DIR / variant
        if variant_dir.is_dir() and not _runtime_entry_point_ok(variant_dir):
            logger.warning(
                "[launcher.umu] runtime '%s' present but entry point missing "
                "— removing so umu re-downloads it", variant,
            )
            shutil.rmtree(variant_dir, ignore_errors=True)
def ensure_umu_runtime_ready() -> None:
    """Ensure UMU runtime ready."""
    # The Steam Linux Runtime (steamrt2/3/4, depending on which one the
    # selected Proton build requires) is downloaded by the first
    # ``umu-run`` and is the slowest part of a first-ever launch
    # (hundreds of MB), with no native progress — exactly the
    # "is it frozen?" gap the user hit. Toast once when it's missing so
    # the wait is expected. Fires only on the genuine first setup; the
    # cache then persists and is shared across every game.
    if not any((UMU_CACHE_DIR / variant).exists() for variant in UMU_RUNTIME_VARIANTS):
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
    timeout: float | None = None,  # noqa: ASYNC109 — bounds a subprocess wait via wait_for + killpg, not an asyncio.timeout() wrapper
) -> int:

    """Run UMU with retry.

    ``timeout`` bounds each attempt's ``proc.wait()``. It defaults to
    ``None`` (unbounded — a real game launch runs for hours), so the
    bound is strictly opt-in per caller: only the short prefix-compat
    steps (winetricks / vcruntime regedit) pass one. On timeout the
    process group is force-killed and the attempt returns
    :data:`UMU_TIMEOUT_RC`, which is deliberately *not* recoverable, so
    a hung Proton fails the step instead of retrying into the same hang.
    """
    last_rc = 1
    game_log = open_game_log()
    out = game_log if game_log is not None else None
    try:
        for attempt in range(1, max_attempts + 1):
            logger.info(
                "[launcher.umu] run attempt %d/%d: %s (output → %s)",
                attempt, max_attempts, argv[:3],
                "game.log" if out is not None else "inherited",
            )
            rc = await _run_umu_once(argv, env, cwd, out, on_start, timeout)
            last_rc = rc
            logger.info("[launcher.umu] attempt %d exit code: %d", attempt, rc)
            if rc == 0:
                return 0
            if rc in _RECOVERABLE_CODES and attempt < max_attempts:
                wipe = rc in _RUNTIME_CORRUPTION_CODES
                logger.warning(
                    "[launcher.umu] recoverable rc=%d, retry (wipe_cache=%s)",
                    rc, wipe,
                )
                launcher_toast(
                    "toasts.launcher.retryingUmu",
                    i18n_title_key="toasts.launcher.launchRetry",
                    i18n_params={
                        "seconds": _RETRY_BACKOFF_SECONDS,
                        "attempt": attempt + 1,
                        "max": max_attempts,
                    },
                    severity="warning",
                )
                if wipe:
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
    timeout: float | None = None,  # noqa: ASYNC109 — bounds a subprocess wait via wait_for + killpg, not an asyncio.timeout() wrapper
) -> int:
    """Spawn one umu process, fire ``on_start``, await its exit code.

    When ``timeout`` is set, a process that outlives it is force-killed
    (whole process group) and :data:`UMU_TIMEOUT_RC` is returned; with
    ``timeout is None`` the wait is unbounded (the launch default).
    Cancellation (e.g. the user hitting Cancel on a "Setting up game…"
    install) also reaps the process group before propagating, so no
    orphaned wineserver is left spinning.
    """
    if env is not None:
        # Belt-and-suspenders: neither loader variable may reach umu-run/
        # pressure-vessel here, regardless of what built ``env``.
        #
        # LD_PRELOAD — re-exporting the host's gameoverlayrenderer.so crashes
        # the game process with "WARNING: Keyboard Interrupt".
        #
        # LD_LIBRARY_PATH — umu copies it into STEAM_RUNTIME_LIBRARY_PATH
        # (umu_run.enable_steam_game_drive), so a *host* library path rides
        # into the pressure-vessel container and shadows the container's own
        # libs. The container then can't start ``python3`` — the interpreter
        # of Proton's launch script — which dies with "error while loading
        # shared libraries: libz.so.1" and umu exits 127. This bites hardest
        # where Steam itself runs containerised (SteamOS 3.8+), whose
        # LD_LIBRARY_PATH points at /usr/lib/pressure-vessel/overrides/... —
        # paths that only resolve inside the *outer* container.
        #
        # Epic was immune only because handlers/epic.py already wraps its
        # umu-run invocation in ``env -u LD_LIBRARY_PATH -u LD_PRELOAD``;
        # GOG/Amazon/Ubisoft/raw-exe reach this spawn point directly. Doing
        # it here covers every store at the single choke point.
        # See sanitize_frozen_loader_env.
        env.pop("LD_PRELOAD", None)
        env.pop("LD_LIBRARY_PATH", None)
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
    try:
        if timeout is None:
            return await proc.wait()
        try:
            return await asyncio.wait_for(proc.wait(), timeout=timeout)
        except TimeoutError:
            logger.warning(
                "[launcher.umu] %s exceeded %ds — killing process group",
                argv[:3], int(timeout),
            )
            _kill_process_group(proc)
            _reap_prefix_wineserver(env)
            with contextlib.suppress(Exception):
                await asyncio.wait_for(proc.wait(), timeout=5)
            return UMU_TIMEOUT_RC
    except asyncio.CancelledError:
        # Reap the whole tree before unwinding, else the umu-run /
        # pressure-vessel / wineserver descendants outlive the cancelled
        # task and keep spinning (start_new_session=True detaches them).
        _kill_process_group(proc)
        _reap_prefix_wineserver(env)
        with contextlib.suppress(Exception):
            await asyncio.wait_for(proc.wait(), timeout=5)
        raise
