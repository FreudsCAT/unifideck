"""Observing a wrapper store's sign-in, and reporting a verdict either way.

py_modules/unifideck/stores/shared/wrapper_auth_monitor.py

A *wrapper store* signs in through the vendor's own Windows client, running
detached inside a Wine prefix. There is no callback: the client writes its
session into the prefix and exits, so the only way to know sign-in finished
is to watch the prefix for it.

The frontend depends on that verdict far more than it looks. ``AuthDispatcher``
holds one in-flight promise per store in a module-singleton ``Map`` and only
clears it when ``STORE_AUTH_COMPLETE`` or ``STORE_AUTH_FAILED`` arrives. A flow
that emits neither does not merely fail to update the UI — it wedges the button
for the full 10-minute timeout, because the next press is handed the same stale
pending promise and never reaches the RPC at all. Measured from a tester's
device: Battle.net's sign-in emitted nothing, and "it only worked again after I
restarted Steam" is exactly what reloading the frontend bundle does to that map.

So this module exists to guarantee **a terminal event on every path**, which is
the part both stores previously got wrong in different ways:

* Battle.net had no monitor at all — no success signal, no failure signal.
* Ubisoft emitted ``STORE_AUTH_COMPLETE`` on capture but nothing on the timeout
  path, so an abandoned or rejected sign-in hung the frontend identically.

Shared rather than copied for the reason ``prefix_placement`` states: the same
question asked separately in two places is how these stores drift apart. A
store supplies a probe and, optionally, what to do once the session lands.
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

    async def stop(self, reason: str) -> None:
        """Abandon the watch and report failure.

        For the paths that make a pending sign-in moot — a logout, or the user
        starting over. Silent on an already-finished monitor: a completed
        capture has emitted its verdict and must not be contradicted.
        """
        running = self._monitor_task is not None and not self._monitor_task.done()
        await self._cancel_task()
        if running and not self._session_captured:
            await self._emit_failed(reason)

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
        await self._emit_failed(
            f"Sign-in was not completed within {int(self._timeout_s // 60)} minutes",
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

    async def _emit_failed(self, error: str) -> None:
        """Emit STORE_AUTH_FAILED so the frontend settles instead of hanging."""
        await self._emit(Events.STORE_AUTH_FAILED, error=error)

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
