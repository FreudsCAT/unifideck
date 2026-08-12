"""Watching a wrapper store's prefix for a completed sign-in.

py_modules/unifideck/stores/shared/wrapper_auth_monitor.py

A *wrapper store* signs in through the vendor's own Windows client, running
detached inside a Wine prefix. There is no callback: the client writes its
session into the prefix and exits, so the only way to know sign-in finished
is to watch the prefix for it.

The frontend depends on a success signal. ``AuthDispatcher`` holds one
in-flight promise per store and only clears it when the backend emits
``STORE_AUTH_COMPLETE`` — otherwise the promise stays pending and the Sign In
button returns nothing on later presses. Measured from a tester's device:
Battle.net's sign-in emitted nothing, and "it only worked again after I
restarted Steam" is exactly what reloading the frontend bundle does.

This module provides the success half. Failure is the dispatcher's own
concern and need not be signalled from here — it already resolves on the
auth app's exit and on its own 10-minute timeout.

**``STORE_AUTH_FAILED`` is deliberately never emitted.** The frontend
``auth-store`` subscribes to that event and translates it to a store status
of ``"error"``, which ``StoreAuthButton`` renders as ``null`` — the button
vanishes. The one answer worse than a button that does nothing is a button
that is not there at all. A sign-in that times out should show
"disconnected" with a working Connect button, not a blank row.

The original Ubisoft monitor (``stores/ubisoft/auth/session_monitor.py``,
now retired) got this right by accident: it only ever emitted success and
logged a warning on timeout, so the flow silently hung rather than
vanishing. Battle.net had nothing. This module replaces both, and its rule
is the same: signal success, and only success.

Shared rather than copied for the reason ``prefix_placement`` states: the
same question asked separately in two places is how these stores drift
apart. A store supplies a probe and, optionally, what to do once the
session lands.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events, Result

if TYPE_CHECKING:
    from unifideck.event_bus.event_bus import EventBus

logger = logging.getLogger(__name__)

# Generous on purpose: this bounds a human typing credentials, solving a
# captcha and clearing a 2FA prompt, not a machine operation.
AUTH_MONITOR_TIMEOUT_S = 30 * 60
AUTH_MONITOR_POLL_INTERVAL_S = 2.0

# Returns True once the auth prefix holds a usable session. Async because one
# store's probe reads a licence ledger off disk and the other captures files.
SignedInProbe = Callable[[], Awaitable[bool]]
# Ran once, immediately after the probe first answers True and before the
# success event is emitted — session propagation, asset warm-up, and the like.
CapturedHook = Callable[[], Awaitable[None]]


class WrapperAuthMonitor:
    """Poll a wrapper store's auth prefix and emit a terminal auth event.

    One instance per store, owned by that store's auth facade. Restartable:
    :meth:`start` cancels any previous run, so a user pressing Sign In twice
    gets a fresh window rather than an already-expiring one.
    """

    def __init__(
        self,
        *,
        store: str,
        is_signed_in: SignedInProbe,
        bus: EventBus | None = None,
        on_captured: CapturedHook | None = None,
        timeout_s: float = AUTH_MONITOR_TIMEOUT_S,
        poll_interval_s: float = AUTH_MONITOR_POLL_INTERVAL_S,
    ) -> None:
        """Initialize the instance."""
        self._store = store
        self._is_signed_in = is_signed_in
        self._bus = bus
        self._on_captured = on_captured
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s
        self._monitor_task: asyncio.Task[None] | None = None
        self._session_captured = False

    async def start(self) -> Result:
        """Begin watching. Cancels and replaces any run already in progress."""
        await self._cancel_task()
        self._session_captured = False
        self._monitor_task = asyncio.create_task(self._loop())
        logger.info("[%sAuth] started auth session monitor", self._store)
        return Result(success=True)

    async def stop(self) -> None:
        """Abandon the watch silently.

        For the paths that make a pending sign-in moot — a logout, or the user
        pressing Sign In again. The cancelled task will unwind without
        emitting anything. A completed capture has already emitted its
        verdict and is left undisturbed; the guard is ``_session_captured``.
        """
        await self._cancel_task()

    async def _cancel_task(self) -> None:
        """Cancel the in-flight task, if any, and wait for it to unwind."""
        task = self._monitor_task
        self._monitor_task = None
        if task is None or task.done():
            return
        task.cancel()
        try:
            await task
        except asyncio.CancelledError:
            pass
        except Exception as e:  # a dying monitor must not break its replacement
            logger.debug("[%sAuth] old monitor task error on cancel: %s", self._store, e)

    async def _loop(self) -> None:
        """Poll until signed in or the ceiling is reached, then report."""
        elapsed = 0.0
        while elapsed < self._timeout_s:
            await asyncio.sleep(self._poll_interval_s)
            elapsed += self._poll_interval_s
            if not await self._probe():
                continue
            logger.info("[%sAuth] auth session monitor: session captured", self._store)
            self._session_captured = True
            await self._run_captured_hook()
            await self._emit_complete()
            return
        logger.warning(
            "[%sAuth] auth session monitor timed out after %.0fs",
            self._store, self._timeout_s,
        )

    async def _probe(self) -> bool:
        """Ask the store whether it is signed in yet.

        A probe reads a live Wine prefix that the vendor client is writing to,
        so a transient failure (a half-written file, a torn read) is expected
        rather than exceptional. Swallow it and try again on the next tick —
        raising here would kill the monitor and take the terminal event with it,
        which is the exact failure this class exists to prevent.
        """
        try:
            return await self._is_signed_in()
        except Exception as e:
            logger.debug("[%sAuth] sign-in probe failed: %s", self._store, e)
            return False

    async def _run_captured_hook(self) -> None:
        """Run the store's post-capture work. Never blocks the event."""
        if self._on_captured is None:
            return
        try:
            await self._on_captured()
        except Exception as e:
            logger.warning("[%sAuth] post-capture hook failed: %s", self._store, e)

    async def _emit_complete(self) -> None:
        """Emit STORE_AUTH_COMPLETE so the frontend settles and refreshes."""
        await self._emit(Events.STORE_AUTH_COMPLETE)

    async def _emit(self, event: str, **payload: Any) -> None:
        """Emit ``event`` for this store, swallowing bus failures."""
        if self._bus is None:
            return
        try:
            await self._bus.emit(event, store=self._store, **payload)
        except Exception as e:
            logger.warning("[%sAuth] failed to emit %s: %s", self._store, event, e)

    def status(self) -> dict[str, Any]:
        """Whether a session was captured, and whether a watch is running."""
        monitoring = self._monitor_task is not None and not self._monitor_task.done()
        return {"captured": self._session_captured, "monitoring": monitoring}
