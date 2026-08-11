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
from typing import TYPE_CHECKING

from unifideck.launcher import wrapper_session
from unifideck.launcher.frontend_bridge import launcher_toast
from unifideck.launcher.game_title import resolve_title
from unifideck.launcher.proton.handlers import battlenet_bootstrap as bootstrap
from unifideck.launcher.proton.handlers import battlenet_watch as watch
from unifideck.launcher.proton.handlers import wrapper_clients
from unifideck.launcher.proton.handlers.battlenet_client import (
    find_client_exe,
    find_launcher_exe,
    record_launch_ok,
    resolve_family,
)
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.proton.infrastructure.umu_runtime import run_umu_with_retry
from unifideck.launcher.types.errors import GameFailedError

if TYPE_CHECKING:  # pragma: no cover - typing only
    from unifideck.stores.battlenet.prefix.client_install import BootstrapResult

logger = logging.getLogger(__name__)

# Must outlast a cold start plus a forced client self-update — the client
# updated itself within five minutes of first launch during the spike.
CLIENT_READY_TIMEOUT = 300.0
# Bounded on purpose: this is the silent-failure detector.
GAME_APPEAR_TIMEOUT = 180.0
# The exec invocation does not exit promptly even on success, so it is
# fire-and-bounded-wait rather than awaited to completion.
EXEC_TIMEOUT = 60.0
# How long a client-less Wine session gets to produce a renderer before we
# call it stale. Short: a healthy client never reaches this path.
STALE_SESSION_GRACE = 20.0

STORE = "battlenet"

# The login token is a registry key, and wineserver owns the registry: it saves
# on a short timer after a change and rewrites the file from memory when it
# exits. Both numbers exist so a capture reads the token this run rotated
# rather than the one before it.
REGISTRY_SETTLE_TIMEOUT = 20.0
REGISTRY_SETTLE_SECONDS = 3.0


def _fail(
    plan: ProtonLaunchPlan,
    key: str,
    message: str,
    *,
    rc: int = 1,
    titled: bool = True,
    **context: object,
) -> GameFailedError:
    """Toast the failure and build the error to raise.

    ``titled=False`` for messages with no ``{{gameTitle}}`` placeholder.
    The auth shortcut is not a game: ``resolve_title`` finds no registry row
    for ``battlenet:bnet-auth`` and returns the key itself, which is how a
    user came to read "isn't set up for battlenet:bnet-auth yet".
    """
    launcher_toast(
        f"toasts.launcher.{key}Message",
        i18n_title_key=f"toasts.launcher.{key}",
        game_title=resolve_title(plan.context.game_key) if titled else "",
        severity="error",
    )
    plan.state.game_exit_code = rc
    return GameFailedError(message, subprocess_rc=rc, context=dict(context))


def _install_error(result: BootstrapResult | None, fallback: str) -> str:
    """The installer's own words for the log, or ``fallback`` if it has none."""
    return result.error if result is not None and result.error else fallback


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
    """Phase C. PROTON_VERB=run, one argument, return code ignored.

    ``reap_wineserver=False`` is load-bearing. This run shares its prefix
    with the client phase A started and does not own that wineserver, so
    the :data:`EXEC_TIMEOUT` cancellation must reap only its own process
    group. It did not: the prefix-scoped reap SIGKILLed the live client
    60 s into every launch, killing the Battle.net Agent mid-download.
    Measured on-device — every Diablo II install stalled inside a minute,
    frozen at 27%, with the Agent's log going silent at the reap's exact
    timestamp. See ``infrastructure/wineserver_reap`` for the scope rule.
    """
    env = dict(plan.env)
    env["PROTON_VERB"] = "run"
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(client_exe), f"--exec={command}"]
    logger.info("[battlenet] phase C: --exec=%s (PROTON_VERB=run)", command)
    with contextlib.suppress(TimeoutError, asyncio.CancelledError):
        await asyncio.wait_for(
            run_umu_with_retry(argv, env=env, max_attempts=1, reap_wineserver=False),
            timeout=EXEC_TIMEOUT,
        )


async def _clear_stale_session(plan: ProtonLaunchPlan) -> None:
    """Clear a Wine session that has no usable client left.

    Phase A runs ``waitforexitandrun``, which blocks on the prefix's
    existing wineserver — so a dead session does not just sit there, it
    wedges the next launch. Sessions were being left behind because
    teardown only recognised the client's main process, so once that died
    its CEF children and Wine infrastructure survived unsignalled and the
    next launch stacked a second client on top. Two were measured on-device.

    Costs nothing on a cold prefix (no Wine processes) or a healthy one
    (the caller's readiness check returns first). The grace period is paid
    only when a session exists but is not becoming ready — which is also
    what distinguishes "stale" from "still starting up".
    """
    if not watch.wine_pids(plan.prefix_path):
        return
    if await watch.wait_for_client_ready(plan.prefix_path, STALE_SESSION_GRACE):
        return
    logger.warning("[battlenet] stale Wine session in %s — clearing", plan.prefix_path)
    await asyncio.to_thread(watch.stop_stale_session, plan.prefix_path)


async def _release_other_clients(plan: ProtonLaunchPlan) -> None:
    """Make sure no client is running in a *different* prefix.

    Every prefix is a clone, so every client presents the same
    ``Client.GaClientId`` and the same token. Two running at once both refresh
    that token and the server invalidates one of them — which is how the user
    reaches "Your login session has expired" by opening the Sign-In tile and
    then launching a game.

    The auth prefix's client is closed: it exists only to sign in, never
    downloads anything, and it writes its session directly into the prefix we
    are about to inject from, so stopping it is both safe and necessary.

    A client in any *other* game prefix is left strictly alone and the launch
    fails instead. It may be mid-download, and killing the Agent is a measured
    failure — every Diablo II install stalled at 27% when a reap reached it.
    """
    others = await asyncio.to_thread(
        wrapper_clients.live_client_prefixes, STORE,
        exclude=(plan.prefix_path,),
    )
    if not others:
        return
    auth = wrapper_session.auth_prefix(STORE)
    auth_resolved = auth.resolve() if auth is not None else None
    for other in others:
        if auth_resolved is not None and other == auth_resolved:
            logger.info(
                "[battlenet] closing the sign-in client in %s before starting "
                "one here (two clients share an identity and race)", other,
            )
            await asyncio.to_thread(watch.stop_client, other)
            continue
        raise _fail(
            plan,
            "battlenetClientBusyElsewhere",
            f"The Battle.net client is already running for another game "
            f"in {other.name}",
            other_prefix=str(other),
        )


async def _inject_session(plan: ProtonLaunchPlan) -> None:
    """Refresh this prefix's session from auth before the client starts.

    The reason a prefix that has sat idle for a month still opens signed in:
    the vendor rotates its token on every run, so the copy this prefix was
    cloned with is stale, while the auth prefix is kept current by the capture
    on the other side of every run.

    Every guard lives in ``wrapper_session.inject`` — an auth prefix with no
    session, or a target already holding something newer, is a no-op.
    """
    spec = wrapper_session.spec_for(STORE)
    auth = wrapper_session.auth_prefix(STORE)
    if spec is None or auth is None:
        return
    # The token is a registry key, and a live wineserver owns the registry —
    # it rewrites the file from memory on exit, silently discarding whatever
    # we wrote. ``_clear_stale_session`` has already run, so the normal path
    # is quiet; report the truth either way.
    busy = bool(await asyncio.to_thread(watch.wine_pids, plan.prefix_path))
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            _inject_call, spec, auth, Path(plan.prefix_path), busy,
        )


def _inject_call(
    spec: wrapper_session.SessionSpec, auth: Path, target: Path, busy: bool,
) -> bool:
    return wrapper_session.inject(spec, auth, target, target_busy=busy)


async def _capture_session(plan: ProtonLaunchPlan) -> None:
    """Hand this run's rotated session back to the auth prefix.

    Called after the client has been stopped, never before: the client flushes
    its rotated token on shutdown, which is why teardown SIGTERMs first and
    waits. The backend repeats this on ``GAME_STOPPED`` because this process
    can itself be SIGKILLed — belt and braces for the one thing whose loss the
    user actually notices.
    """
    spec = wrapper_session.spec_for(STORE)
    auth = wrapper_session.auth_prefix(STORE)
    if spec is None or auth is None:
        return
    # This prefix's wineserver must be gone before its registry is read: Wine
    # saves the token on a short timer after the key changes, so a read taken
    # while it is still up returns the *previous* token.
    await _await_quiet(plan.prefix_path)
    busy = bool(await asyncio.to_thread(watch.wine_pids, auth))
    with contextlib.suppress(Exception):
        await asyncio.to_thread(
            _capture_call, spec, Path(plan.prefix_path), auth, busy,
        )


def _capture_call(
    spec: wrapper_session.SessionSpec, source: Path, auth: Path, busy: bool,
) -> bool:
    return wrapper_session.capture(spec, source, auth, auth_busy=busy)


async def _await_quiet(prefix: Path | str) -> None:
    """Wait, bounded, for every Wine process in ``prefix`` to be gone.

    Then a short settle, because wineserver flushes the registry on a timer
    rather than on each change — reading the instant the last pid vanishes can
    still miss the token it just rotated.
    """
    waited = 0.0
    while waited < REGISTRY_SETTLE_TIMEOUT:
        if not await asyncio.to_thread(watch.wine_pids, prefix):
            break
        await asyncio.sleep(1.0)
        waited += 1.0
    await asyncio.sleep(REGISTRY_SETTLE_SECONDS)


async def _bring_up_client(plan: ProtonLaunchPlan) -> Path:
    """Phases A + B. Returns the client exe once it will accept commands."""
    launcher_exe = find_launcher_exe(plan.prefix_path)
    client_exe = find_client_exe(plan.prefix_path)
    install: BootstrapResult | None = None
    if launcher_exe is None or client_exe is None:
        install = await bootstrap.install_client(plan)
        launcher_exe = find_launcher_exe(plan.prefix_path)
        client_exe = find_client_exe(plan.prefix_path)
    if launcher_exe is None or client_exe is None:
        raise _fail(
            plan,
            bootstrap.toast_key_for(install, "battlenetPrefixNotReady"),
            _install_error(install, "Battle.net client is not installed in this prefix"),
            rc=127,
            titled=install is None or install.error_code is None,
            prefix=str(plan.prefix_path),
        )

    if not watch.client_ready(plan.prefix_path):
        await _start_client_here(plan, launcher_exe)
    return client_exe


async def _start_client_here(plan: ProtonLaunchPlan, launcher_exe: Path) -> None:
    """Phase A + B proper: clear the way, start the client, wait for readiness.

    Split from :func:`_bring_up_client` to stay under the fan-out gate; the
    ordering below is load-bearing and unchanged.
    """
    # Order matters. No other client may be live when we inject — it would be
    # writing the very files we are replacing — and the injection has to land
    # before this client reads them at startup.
    await _release_other_clients(plan)
    await _clear_stale_session(plan)
    await _inject_session(plan)
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
    pid, before = await _issue_and_confirm(plan, client_exe, uid, family)

    async with _client_teardown(plan):
        await watch.wait_for_exit(plan.prefix_path, pid, before=before)
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

    The session capture follows the stop, in that order, because the client
    only writes its rotated token as it shuts down.
    """
    try:
        yield
    finally:
        with contextlib.suppress(Exception):
            await asyncio.to_thread(watch.stop_client, plan.prefix_path)
        await _capture_session(plan)


async def _issue_and_confirm(
    plan: ProtonLaunchPlan, client_exe: Path, uid: str, family: str,
) -> tuple[str, set[str]]:
    """Phases C + D: send the launch, then prove a game process appeared.

    Returns ``(pid, before)`` — the new pid, and the pre-launch snapshot
    phase E needs to follow a launcher-to-game hand-off. Raises when
    nothing started, because the client accepts an obsolete family code
    and does nothing — no error, no dialog, no exit code — so only a new
    process is evidence.
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
    return pid, before


async def battlenet_auth_launch(plan: ProtonLaunchPlan) -> int:
    """Open the client so the user can sign in.

    Blocks until the user closes it, which is what stops the Steam shortcut
    exiting immediately and tearing the window down with it.

    Installs the client first when the prefix has none. That is the normal
    path after a fresh install or a full cleanup, not an edge case.
    """
    launcher_exe = find_launcher_exe(plan.prefix_path)
    install: BootstrapResult | None = None
    if launcher_exe is None:
        install = await bootstrap.install_client(plan)
        launcher_exe = find_launcher_exe(plan.prefix_path)
    if launcher_exe is None:
        # The auth shortcut has no game title to name, and resolve_title
        # would hand the toast the raw "battlenet:bnet-auth" key — which is
        # exactly what a user saw on screen. Its keys take no title.
        raise _fail(
            plan,
            bootstrap.toast_key_for(install, "battlenetAuthPrefixNotReady"),
            _install_error(install, "Battle.net client is not installed in the auth prefix"),
            rc=127,
            titled=False,
            prefix=str(plan.prefix_path),
        )
    launcher_toast(
        "toasts.launcher.signingInBattlenetMessage",
        i18n_title_key="toasts.launcher.signingInBattlenet",
    )
    argv = [str(plan.python_bin), str(plan.umu_wrapper), str(launcher_exe)]
    # reap_wineserver=False so a stop from the UI unwinds through
    # _client_teardown's SIGTERM instead of SIGKILLing the client outright:
    # the token the client rotated during sign-in lives in CachedData.db and
    # is lost if it never gets to flush. See watch.stop_client.
    async with _client_teardown(plan):
        rc = await run_umu_with_retry(
            argv, env=plan.env, on_start=plan.on_process_start,
            reap_wineserver=False,
        )
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
