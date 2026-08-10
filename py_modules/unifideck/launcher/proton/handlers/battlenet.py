"""Battle.net launch handler — two-phase, with post-launch verification.

py_modules/unifideck/launcher/proton/handlers/battlenet.py

Launching a Battle.net game is not one command. The client must already be
running before it will accept a launch instruction, so::

    Phase A  start Battle.net Launcher.exe, DETACHED
             PROTON_VERB=waitforexitandrun — this run owns the wineserver
    Phase B  poll until a CEF renderer exists in THIS prefix
    Phase C  Battle.net.exe --exec="launch <FAMILY>"
             PROTON_VERB=run  <-- load-bearing, see below
    Phase D  verify a game process actually appeared
    Phase E  watch until it exits

**Phase C must use ``PROTON_VERB=run``.** ``waitforexitandrun`` runs
``wineserver -w`` first, which blocks until the prefix's existing wineserver
exits — and in phase C that wineserver is the client we just started.
Measured on-device: with ``waitforexitandrun`` the second invocation never
reaches the exe at all and the command never lands; with ``run`` it works.

**Phase D is mandatory, not defensive.** Blizzard renamed Diablo IV's family
code ``D4`` -> ``Fen`` in 2026, and the client *accepts the obsolete code and
does nothing* — no error, no dialog, no exit code. The only way to know a
launch worked is to see a new game process. For the same reason the phase C
return code is ignored: the client forwards the command and exits, so its rc
says nothing about the game.

Only one argument is passed. NonSteamLaunchers issue #957 reports a shortcut
opening the launcher instead of the game while passing both ``--exec`` and a
``battlenet://`` URI; the conflicting second argument is a prime suspect.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import AsyncGenerator
from pathlib import Path

from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.game_title import resolve_title
from unifideck.launcher.proton.handlers import battlenet_watch as watch
from unifideck.launcher.proton.handlers.battlenet_client import (
    find_client_exe,
    find_launcher_exe,
    record_launch_ok,
    resolve_family,
)
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError

logger = logging.getLogger(__name__)

# Must outlast a cold start plus a forced client self-update — the client
# updated itself within five minutes of first launch during the spike.
CLIENT_READY_TIMEOUT = 300.0
# Bounded on purpose: this is the silent-failure detector.
GAME_APPEAR_TIMEOUT = 180.0
# The exec invocation does not exit promptly even on success, so it is
# fire-and-bounded-wait rather than awaited to completion.
EXEC_TIMEOUT = 60.0


def _fail(
    plan: ProtonLaunchPlan,
    key: str,
    message: str,
    *,
    rc: int = 1,
    **context: object,
) -> GameFailedError:
    launcher_toast(
        f"toasts.launcher.{key}Message",
        i18n_title_key=f"toasts.launcher.{key}",
        game_title=resolve_title(plan.context.game_key),
        severity="error",
    )
    plan.state.game_exit_code = rc
    return GameFailedError(message, subprocess_rc=rc, context=dict(context))


async def _start_client_detached(plan: ProtonLaunchPlan, launcher_exe: Path) -> None:
    """Phase A. Owns the wineserver session, so it keeps waitforexitandrun."""
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(launcher_exe)]
    logger.info("[battlenet] phase A: starting client")
    proc = await asyncio.create_subprocess_exec(
        *argv,
        env=plan.env,
        stdout=asyncio.subprocess.DEVNULL,
        stderr=asyncio.subprocess.DEVNULL,
        start_new_session=True,
    )
    if plan.on_process_start:
        with contextlib.suppress(Exception):
            plan.on_process_start(proc)


async def _issue_exec(plan: ProtonLaunchPlan, client_exe: Path, command: str) -> None:
    """Phase C. PROTON_VERB=run, one argument, return code ignored."""
    env = dict(plan.env)
    env["PROTON_VERB"] = "run"
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(client_exe), f"--exec={command}"]
    logger.info("[battlenet] phase C: --exec=%s (PROTON_VERB=run)", command)
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(
            run_umu_with_retry(argv, env=env, max_attempts=1),
            timeout=EXEC_TIMEOUT,
        )


async def _bring_up_client(plan: ProtonLaunchPlan) -> Path:
    """Phases A + B. Returns the client exe once it will accept commands."""
    launcher_exe = find_launcher_exe(plan.prefix_path)
    client_exe = find_client_exe(plan.prefix_path)
    if (launcher_exe is None or client_exe is None) and await _install_client_here(plan):
        launcher_exe = find_launcher_exe(plan.prefix_path)
        client_exe = find_client_exe(plan.prefix_path)
    if launcher_exe is None or client_exe is None:
        raise _fail(
            plan,
            "battlenetPrefixNotReady",
            "Battle.net client is not installed in this prefix",
            rc=127,
            prefix=str(plan.prefix_path),
        )

    if not watch.client_ready(plan.prefix_path):
        await _start_client_detached(plan, launcher_exe)
        launcher_toast(
            "toasts.launcher.battlenetStartingClientMessage",
            i18n_title_key="toasts.launcher.battlenetStartingClient",
            game_title=resolve_title(plan.context.game_key),
        )
        if not await watch.wait_for_client_ready(plan.prefix_path, CLIENT_READY_TIMEOUT):
            raise _fail(
                plan,
                "battlenetClientNotReady",
                "Battle.net client did not become ready",
                timeout=CLIENT_READY_TIMEOUT,
            )
    return client_exe


async def battlenet_launch(plan: ProtonLaunchPlan) -> int:
    """Launch an installed Battle.net game through the resident client."""
    uid = plan.context.game_id
    family = resolve_family(uid)
    if not family:
        # Never fall back to "open the client bare". Battle.net's failure is
        # silent, so the user would see the client open and nothing happen.
        raise _fail(
            plan,
            "battlenetFamilyMissing",
            f"No Battle.net family code known for {uid}",
            uid=uid,
        )

    launcher_toast(
        "toasts.launcher.startingBattlenetGame",
        i18n_title_key="toasts.launcher.launchingGame",
        game_title=resolve_title(plan.context.game_key),
    )
    client_exe = await _bring_up_client(plan)
    pid = await _issue_and_confirm(plan, client_exe, uid, family)

    async with _client_teardown(plan):
        await watch.wait_for_exit(plan.prefix_path, pid)
    plan.state.game_exit_code = 0
    return 0


@contextlib.asynccontextmanager
async def _client_teardown(plan: ProtonLaunchPlan) -> AsyncGenerator[None]:
    """Stop the client in this prefix when the run ends, however it ends.

    The client is started detached (``start_new_session=True``), so it
    outlives us by default: on a normal exit Steam marks the shortcut
    stopped while Battle.net is still running, and on a stop from the UI
    the SIGTERM reaches only this launcher. Either way the user is left
    with a window whose "X" no longer talks to anything and a play session
    that never closes.

    Runs on cancellation too, which is the path the Steam stop button and
    the QAM "X" actually take.
    """
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(watch.stop_client, plan.prefix_path)


async def _issue_and_confirm(
    plan: ProtonLaunchPlan, client_exe: Path, uid: str, family: str,
) -> str:
    """Phases C + D: send the launch, then prove a game process appeared.

    Returns the new pid. Raises when nothing started, because the client
    accepts an obsolete family code and does nothing — no error, no dialog,
    no exit code — so only a new process is evidence.
    """
    before = watch.game_pids(plan.prefix_path)
    await _issue_exec(plan, client_exe, f"launch {family}")

    pid = await watch.wait_for_game(plan.prefix_path, before, GAME_APPEAR_TIMEOUT)
    if pid is None:
        raise _fail(
            plan,
            "battlenetLaunchNotObserved",
            f"Battle.net accepted 'launch {family}' but no game process appeared",
            uid=uid,
            family=family,
        )

    # This family is now proven for this uid. Record it before the
    # (potentially hours-long) exit wait: a crash or a forced shutdown
    # mid-session must not cost us the one fact that makes a later family
    # rename detectable.
    with contextlib.suppress(Exception):
        record_launch_ok(uid, family, time.time())
    return pid


async def _install_client_here(plan: ProtonLaunchPlan) -> bool:
    """Install the Battle.net client into this prefix, with progress toasts.

    Runs **here**, inside the RunGame session, rather than in the backend.
    The rule is the one ``services/download/wrapper_signals.py`` already
    states: the backend must not spawn the vendor client itself, because in
    Gaming Mode a bare subprocess has no gamescope session and its window
    never appears. It applies to the client's *installer* too — that is
    exactly how this failed. Signing in from the desktop showed the wizard;
    from Gaming Mode it rendered nowhere, and the sign-in RPC blocked on a
    window nobody could see.

    Stdlib-and-launcher imports only, deliberately verified to load under
    the SYSTEM python (3.10-3.14) that runs this process.
    """
    logger.info("[battlenet] no client in %s — installing it", plan.prefix_path)
    launcher_toast(
        "toasts.launcher.battlenetInstallingClientMessage",
        i18n_title_key="toasts.launcher.battlenetInstallingClient",
    )
    try:
        from unifideck.stores.battlenet import config as store_config
        from unifideck.stores.battlenet.prefix.client_install import bootstrap_client
        from unifideck.stores.shared.wine_env import WineEnvResolver

        cfg = store_config.from_config_manager(None)
        result = await bootstrap_client(
            plan.prefix_path,
            installer_url=cfg.installer_url,
            installer_cache=cfg.installer_path,
            resolver=WineEnvResolver(
                "battlenet", str(getattr(plan.context, "plugin_dir", "") or ""),
            ),
        )
    except Exception:
        # Report "the prefix has no client", which is true and actionable,
        # rather than a traceback from the repair attempt. The caller falls
        # through to its own typed failure and the user gets a toast.
        logger.exception("[battlenet] client install raised")
        return False
    if not result.success:
        logger.error("[battlenet] client install failed: %s", result.error)
        return False
    logger.info("[battlenet] client installed into %s", plan.prefix_path)
    return True


async def battlenet_auth_launch(plan: ProtonLaunchPlan) -> int:
    """Open the client so the user can sign in.

    Blocks until the user closes it, which is what stops the Steam shortcut
    exiting immediately and tearing the window down with it.

    Installs the client first when the prefix has none. That is the normal
    path after a fresh install or a full cleanup, not an edge case.
    """
    launcher_exe = find_launcher_exe(plan.prefix_path)
    if launcher_exe is None and await _install_client_here(plan):
        launcher_exe = find_launcher_exe(plan.prefix_path)
    if launcher_exe is None:
        raise _fail(
            plan,
            "battlenetPrefixNotReady",
            "Battle.net client is not installed in the auth prefix",
            rc=127,
            prefix=str(plan.prefix_path),
        )
    launcher_toast(
        "toasts.launcher.signingInBattlenetMessage",
        i18n_title_key="toasts.launcher.signingInBattlenet",
    )
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(launcher_exe)]
    async with _client_teardown(plan):
        rc = await run_umu_with_retry(argv, env=plan.env, on_start=plan.on_process_start)
    plan.state.game_exit_code = rc
    return rc


async def battlenet_install_launch(plan: ProtonLaunchPlan) -> int:
    """Open the client on a game's page so the user can press Install.

    ``--exec="install <FAMILY>"`` does **not** start a download — measured
    against the current client with a known-good family code. So this
    navigates and hands over, exactly as the Ubisoft install flow does; the
    download worker owns completion by polling ``product.db``.
    """
    uid = plan.context.game_id
    family = resolve_family(uid)
    if not family:
        raise _fail(
            plan,
            "battlenetFamilyMissing",
            f"No Battle.net family code known for {uid}",
            uid=uid,
        )
    launcher_toast(
        "toasts.launcher.installingBattlenetMessage",
        i18n_title_key="toasts.launcher.installingBattlenet",
        game_title=resolve_title(plan.context.game_key),
    )
    client_exe = await _bring_up_client(plan)
    # Navigate to the game's page; the user presses Install there.
    await _issue_exec(plan, client_exe, f"launch {family}")
    # Stay alive while the client is up so Steam keeps the shortcut running
    # and the install window is not torn down under the user.
    async with _client_teardown(plan):
        await watch.wait_while_client_running(plan.prefix_path)
    plan.state.game_exit_code = 0
    return 0
