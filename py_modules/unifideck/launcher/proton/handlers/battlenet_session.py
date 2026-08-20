"""Moving Battle.net's session in and out of the prefix a run uses.

py_modules/unifideck/launcher/proton/handlers/battlenet_session.py

Every prefix is a clone, and the vendor rotates its token on every run — so
the copy a prefix was cloned with goes server-stale, and the copy this run
produces is the only current one. Hence the pair: inject before the client
starts, capture after it stops. It is why a prefix that has sat idle for a
month still opens signed in.

``launcher/wrapper_session`` owns *how* a session moves and what one consists
of, per store. This module is only the Battle.net launch-time ordering around
it, split out of ``battlenet.py`` to keep that file under the volumetry cap.
The ordering is the load-bearing part, and it is all about wineserver:

* **Inject before the client starts.** The client reads the session at
  startup, so an injection that lands afterwards is not read.
* **Capture after the prefix goes quiet.** The token is a registry key and a
  live wineserver owns the registry — it saves on a short timer after a
  change and rewrites the file from memory when it exits, so a read taken
  while it is still up returns the *previous* token.
"""

from __future__ import annotations

import asyncio
import contextlib
from pathlib import Path

from unifideck.launcher import wrapper_session

from . import battlenet_watch as watch

STORE = "battlenet"

# The two numbers behind "capture reads the token this run rotated rather
# than the one before it". Both exist because wineserver flushes the registry
# on a timer rather than on each change.
REGISTRY_SETTLE_TIMEOUT = 20.0
REGISTRY_SETTLE_SECONDS = 3.0


async def inject_into(prefix: Path | str) -> None:
    """Refresh ``prefix``'s session from the auth prefix, before the client starts.

    Every guard lives in ``wrapper_session.inject`` — an auth prefix with no
    session, or a target already holding something newer, is a no-op.
    """
    spec = wrapper_session.spec_for(STORE)
    auth = wrapper_session.auth_prefix(STORE)
    if spec is None or auth is None:
        return
    # A live wineserver in the target would rewrite the registry from memory
    # on exit and silently discard what we wrote. The caller has already
    # cleared any stale session, so the normal path is quiet; report the
    # truth either way and let ``inject`` decide.
    busy = bool(await asyncio.to_thread(watch.wine_pids, prefix))
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_inject_call, spec, auth, Path(prefix), busy)


def _inject_call(
    spec: wrapper_session.SessionSpec, auth: Path, target: Path, busy: bool,
) -> bool:
    return wrapper_session.inject(spec, auth, target, target_busy=busy)


async def capture_from(prefix: Path | str) -> None:
    """Hand the session ``prefix`` rotated back to the auth prefix.

    Called after the client has been stopped, never before: the client
    flushes its rotated token on shutdown, which is why teardown SIGTERMs
    first and waits. The backend repeats this on ``GAME_STOPPED`` because the
    launcher can itself be SIGKILLed — belt and braces for the one thing
    whose loss the user actually notices.
    """
    spec = wrapper_session.spec_for(STORE)
    auth = wrapper_session.auth_prefix(STORE)
    if spec is None or auth is None:
        return
    await await_quiet(prefix)
    busy = bool(await asyncio.to_thread(watch.wine_pids, auth))
    with contextlib.suppress(Exception):
        await asyncio.to_thread(_capture_call, spec, Path(prefix), auth, busy)


def _capture_call(
    spec: wrapper_session.SessionSpec, source: Path, auth: Path, busy: bool,
) -> bool:
    return wrapper_session.capture(spec, source, auth, auth_busy=busy)


async def await_quiet(prefix: Path | str) -> None:
    """Wait, bounded, for every Wine process in ``prefix`` to be gone.

    Then a short settle: reading the instant the last pid vanishes can still
    miss the token it just rotated.
    """
    waited = 0.0
    while waited < REGISTRY_SETTLE_TIMEOUT:
        if not await asyncio.to_thread(watch.wine_pids, prefix):
            break
        await asyncio.sleep(1.0)
        waited += 1.0
    await asyncio.sleep(REGISTRY_SETTLE_SECONDS)
