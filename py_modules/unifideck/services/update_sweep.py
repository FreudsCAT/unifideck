"""services/update_sweep.py — background "which games have updates" sweep.

WHY THIS EXISTS
---------------
Update state used to be discovered exactly once, on the App-Details mount,
by running the store's BULK scan to answer a single boolean. That had two
costs the user felt:

* **Latency.** ``legendary list-installed --check-updates`` logs in to
  Epic and re-downloads the asset manifest for every installed platform
  before printing a line — 5-10 s on a real Deck. The Update button
  appeared *after* the user had already had time to press Play.
* **Blindness everywhere else.** Nothing but App-Details ever asked, so
  the QAM Installed list, the library, and the launch path had no idea an
  update was pending.

This service moves the scan off the interaction path: it runs shortly
after boot, again whenever a library sync completes, and every 6 h after
that, writing results into
:mod:`~unifideck.services.update_check_cache`. By the time a user opens
anything, the answer is already sitting in memory, and
``check_game_update`` never has to block.

Transitions are pushed to the frontend as ``GAME_UPDATE_AVAILABLE`` —
an event that has existed (declared, prioritised, polled, allow-listed)
with **no emitter** since it was added. This is that emitter.

WHAT IT DOES NOT DO
-------------------
It does not apply updates. Nothing here queues a download; it only
answers "which game ids does this store say are out of date". Applying is
still the user's explicit action through ``update_game``.
"""
from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Events
from unifideck.services import update_check_cache

if TYPE_CHECKING:
    from unifideck.event_bus import EventBus
    from unifideck.stores.shared.store_registry import StoreRegistry

logger = logging.getLogger(__name__)


class UpdateSweepService:
    """Periodic + event-driven update scan across every registered store.

    Attributes:
        POLL_INTERVAL_SECONDS: Steady-state sweep cadence (6 h). Games do
            not ship updates faster than a user notices, and each sweep
            costs a real store round-trip per store.
        BOOT_DELAY_SECONDS: Grace period before the first sweep. Boot is
            already contended (library sync, artwork backfill, shortcut
            reconcile); an Epic login thrown into that window competes
            with work the user is actually waiting on.
        STORE_TIMEOUT_SECONDS: Per-store ceiling. GOG's scan is one HTTPS
            request per installed game, so a large library on a bad
            connection could otherwise hold the sweep open indefinitely.
        COALESCE_WINDOW_S: Scans requested within this window of a
            completed one reuse it. Collapses bursts (boot sweep + a
            post-sync refresh + several page opens arriving together)
            into a single store round-trip.
    """

    POLL_INTERVAL_SECONDS = 6 * 3600
    BOOT_DELAY_SECONDS = 45
    STORE_TIMEOUT_SECONDS = 300
    COALESCE_WINDOW_S = 60

    def __init__(self, bus: EventBus, registry: StoreRegistry) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._registry = registry
        self._poll_task: asyncio.Task[None] | None = None
        # Strong refs to fire-and-forget refreshes so the GC can't collect
        # a scan mid-flight (the same trap StoreRegistry guards against).
        self._refresh_tasks: set[asyncio.Task[None]] = set()
        # Last list we told the frontend about, per store — so a sweep
        # that changes nothing stays silent instead of re-announcing the
        # same pending update every 6 h.
        self._announced: dict[str, list[str]] = {}

    async def start(self) -> None:
        """Begin the boot sweep + polling loop, and listen for syncs."""
        if self._poll_task is not None:
            return
        self._bus.on(Events.SYNC_COMPLETE, self._on_sync_complete)
        self._poll_task = asyncio.create_task(
            self._poll_loop(), name="update_sweep_poll",
        )
        logger.info(
            "[UpdateSweep] started (first sweep in %ds, then every %ds)",
            self.BOOT_DELAY_SECONDS, self.POLL_INTERVAL_SECONDS,
        )

    async def stop(self) -> None:
        """Detach from the bus, cancel the loop and any in-flight refresh.

        Never raises — teardown is best-effort. Unsubscribing matters:
        a Decky reload builds a fresh service against the same bus, and a
        leaked handler would keep a dead instance scanning.
        """
        self._bus.off(Events.SYNC_COMPLETE, self._on_sync_complete)
        tasks = [t for t in (self._poll_task, *self._refresh_tasks) if t]
        self._poll_task = None
        self._refresh_tasks.clear()
        for task in tasks:
            task.cancel()
        for task in tasks:
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await task
        logger.info("[UpdateSweep] stopped")

    async def _poll_loop(self) -> None:
        """Sweep after the boot grace period, then every interval."""
        try:
            await asyncio.sleep(self.BOOT_DELAY_SECONDS)
            while True:
                await self.sweep()
                await asyncio.sleep(self.POLL_INTERVAL_SECONDS)
        except asyncio.CancelledError:
            raise
        except Exception:
            # A crashed loop must not take the plugin with it, but it does
            # mean update state silently stops refreshing — log loudly.
            logger.exception("[UpdateSweep] poll loop died — updates will go stale")

    async def _on_sync_complete(self, **_: Any) -> None:
        """Re-sweep after a library sync — the installed set may have moved."""
        self.request_refresh_all()

    async def sweep(self) -> dict[str, list[str]]:
        """Scan every registered store concurrently. Never raises.

        Stores are independent, so they run in parallel; a store that
        times out or throws contributes an empty result rather than
        failing the sweep for everyone else.
        """
        store_ids = self._registry.store_ids()
        if not store_ids:
            return {}
        results = await asyncio.gather(
            *(self.refresh_store(sid) for sid in store_ids),
            return_exceptions=True,
        )
        out: dict[str, list[str]] = {}
        for store_id, result in zip(store_ids, results, strict=True):
            out[store_id] = result if isinstance(result, list) else []
        total = sum(len(v) for v in out.values())
        logger.info(
            "[UpdateSweep] swept %d store(s), %d game(s) with updates",
            len(store_ids), total,
        )
        return out

    async def refresh_store(self, store_id: str) -> list[str]:
        """Scan one store, cache the result, announce any change.

        Returns the updatable game ids (empty on any failure — an update
        check that could not run must never be reported as "no updates
        exist", but it must also never break the caller).
        """
        store = self._registry.get_store(store_id)
        if store is None:
            return []
        try:
            # ``COALESCE_WINDOW_S``, not 0: a zero TTL would make the
            # re-check inside ``get_or_fetch``'s lock always miss, so a
            # burst (boot sweep landing on the same tick as a post-sync
            # refresh, or three page opens at once) would run N scans
            # back to back instead of sharing one. It is still far below
            # the poll interval, so a real 6 h tick always re-scans.
            ids = await asyncio.wait_for(
                update_check_cache.get_or_fetch(
                    store_id, store.check_for_updates,
                    ttl=self.COALESCE_WINDOW_S,
                ),
                timeout=self.STORE_TIMEOUT_SECONDS,
            )
        except Exception:
            # CancelledError is a BaseException and passes through, so
            # shutdown still cancels cleanly.
            logger.exception("[UpdateSweep] %s scan failed", store_id)
            return []
        await self._announce(store_id, ids)
        return ids

    async def _announce(self, store_id: str, ids: list[str]) -> None:
        """Emit ``GAME_UPDATE_AVAILABLE`` when a store's set changes.

        Emitting unconditionally would re-fire every 6 h for an update the
        user has already decided to ignore. Emitting on change also covers
        the *disappearance* case (the update was applied), which is what
        lets a live Play section drop its Update button.
        """
        if self._announced.get(store_id) == ids:
            return
        self._announced[store_id] = list(ids)
        await self._bus.emit(
            Events.GAME_UPDATE_AVAILABLE,
            store=store_id,
            game_ids=list(ids),
        )
        logger.info(
            "[UpdateSweep] %s: %d game(s) with updates -> %s",
            store_id, len(ids), ids or "none",
        )

    def request_refresh(self, store_id: str) -> None:
        """Schedule a scan of ``store_id`` without waiting for it.

        The RPC path calls this on a cache miss so a cold App-Details open
        returns instantly and the Update button arrives by event. Repeat
        calls are cheap: ``update_check_cache`` holds a per-store lock, so
        concurrent refreshes share one scan rather than spawning N.
        """
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:  # pragma: no cover — no loop outside the plugin
            return
        task = loop.create_task(
            self._refresh_quietly(store_id), name=f"update_refresh_{store_id}",
        )
        self._refresh_tasks.add(task)
        task.add_done_callback(self._refresh_tasks.discard)

    def request_refresh_all(self) -> None:
        """Schedule a scan of every registered store, without waiting."""
        for store_id in self._registry.store_ids():
            self.request_refresh(store_id)

    async def _refresh_quietly(self, store_id: str) -> None:
        """``refresh_store`` with failures swallowed — this is fire-and-forget."""
        with contextlib.suppress(Exception):
            await self.refresh_store(store_id)
