"""services/launcher/service.py — LauncherService DI facade.

Single entry point used by main.py and the dispatcher CLI. Holds
references to existing services (ShortcutService, ProtonService,
CloudSaveService, EdgeBrowser) and orchestrates a single launch
end-to-end. No logic duplication — all non-trivial work is
delegated. The remaining code here is dispatch + signal wiring +
launch stage events + CLI-tool subprocess wrapping.
"""
from __future__ import annotations

import asyncio
import logging
import signal
from typing import TYPE_CHECKING, Any

from unifideck.core.types import Result
from unifideck.launcher.types.context import LaunchContext, RuntimeState

if TYPE_CHECKING:
    from unifideck.auth.edge_browser import EdgeBrowser
    from unifideck.event_bus.event_bus import EventBus
    from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
    from unifideck.services.cloud_save.service import CloudSaveService
    from unifideck.services.proton_service import ProtonService
    from unifideck.services.shortcut.service import ShortcutService

logger = logging.getLogger(__name__)


class LauncherService:
    """Facade orchestrating one launch via delegation to services."""

    def __init__(
        self,
        bus: EventBus,
        shortcut_svc: ShortcutService,
        proton_svc: ProtonService,
        cloud_svc: CloudSaveService,
        edge_browser: EdgeBrowser,
        config: Any | None = None,
        launch_history: Any | None = None,
    ) -> None:
        """Store injected deps + initialise signal/process registry state."""
        self._bus = bus
        self._shortcut_svc = shortcut_svc
        self._proton_svc = proton_svc
        self._cloud_svc = cloud_svc
        self._edge_browser = edge_browser
        self._config = config
        self._launch_history = launch_history

        self._active_subprocess: Any = None
        self._cancelled = False
        self._launch_started_at: float | None = None

    async def start(self) -> None:
        """Install signal handlers. Called by ServiceBootstrap.

        One-shot: ``SIGTERM``/``SIGINT`` routed to cancel the
        active launch subprocess gracefully.
        """
        def _handle_signal(sig: int, frame: Any) -> None:
            logger.info("[LauncherService] received signal %s, cancelling launch", sig)
            self._cancelled = True
            if self._active_subprocess:
                try:
                    self._active_subprocess.terminate()
                except Exception as e:
                    logger.debug("[LauncherService] terminate failed: %s", e)

        try:
            signal.signal(signal.SIGTERM, _handle_signal)
            signal.signal(signal.SIGINT, _handle_signal)
        except ValueError:
            # We might not be in the main thread
            pass
        except Exception as e:
            logger.debug("[LauncherService] signal install failed: %s", e)

    async def stop(self) -> None:
        """Bootstrap teardown hook. No-op for now — signals are
        removed when the event loop shuts down.
        """

    async def launch(self, ctx: LaunchContext) -> Result:
        """Launch a game described by the immutable ``LaunchContext``.

        Dispatch matrix: xCloud → ``_launch_xcloud``; Windows →
        ``_launch_windows``; native Linux → ``_launch_native``.
        Wrapped in circuit-breaker check + error-toast emission.
        Returns a ``Result`` summarising exit code + elapsed time.
        """
        import time
        self._launch_started_at = time.monotonic()

        if await self._check_circuit_breaker(ctx):
            return Result(success=False, error="circuit_open")

        state = RuntimeState(started_at=ctx.env.get("started_at", 0))

        try:
            if ctx.is_xcloud:
                res = await self._launch_xcloud(ctx)
            elif ctx.is_windows_game:
                res = await self._launch_windows(ctx, state)
            else:
                res = await self._launch_native(ctx, state)

            # Enrich with elapsed time
            res.elapsed = self._elapsed_since_launch()
            return res
        except Exception as e:
            return await self._handle_launcher_error(ctx, e)

    async def _launch_xcloud(self, ctx: LaunchContext) -> Result:
        """xCloud streaming path — Edge kiosk mode on the Xbox URL."""
        from unifideck.core.types.events import Events

        store = ctx.game.get("store")
        game_id = ctx.game.get("game_id")

        await self._bus.emit(
            Events.GAME_LAUNCHED,
            store=store,
            game_id=game_id,
            title=ctx.game.get("title", ""),
            app_id=ctx.game.get("app_id", 0)
        )

        # xCloud specific URL
        url = f"https://www.xbox.com/play/games/{game_id}"

        try:
            # ``EdgeBrowser.launch_xcloud`` is synchronous and
            # returns ``bool``. An earlier version of this code
            # awaited it (TypeError: object bool can't be used
            # in 'await' expression) AND treated the return as
            # an integer rc, computing ``success = rc == 0`` —
            # which is always ``False`` whether the bool was
            # True or False, since ``True == 0`` is ``False``
            # and ``False == 0`` is also ``False`` (the rc would
            # be ``0`` only for an int, not a bool).
            #
            # We dispatch through ``asyncio.to_thread`` because
            # the underlying ``subprocess.Popen`` blocks while
            # Edge initializes; without the thread hop the event
            # loop stalls for ~half a second on every launch.
            launched = await asyncio.to_thread(
                self._edge_browser.launch_xcloud, url,
            )
            return Result(success=bool(launched))
        except Exception as e:
            logger.exception("[LauncherService] xCloud launch failed")
            return Result(success=False, error=str(e))
        finally:
            await self._bus.emit(Events.GAME_STOPPED, store=store, game_id=game_id)

    async def _get_launch_id_or_none(self) -> str | None:
        """Return the current launch id from ``launch_history`` or None."""
        if self._launch_history:
            return getattr(self._launch_history, "current_launch_id", None)
        return None

    async def _emit_circuit_open_toast(self, ctx: LaunchContext, failure_count: int) -> None:
        """Delegate to ``circuit_breaker.emit_circuit_open_toast``."""
        # The toast helper lives in circuit_breaker.py because the
        # message it renders is specific to the breaker's open state;
        # error_toasts.py only handles post-failure toasts.
        from .circuit_breaker import emit_circuit_open_toast
        await emit_circuit_open_toast(self, ctx, failure_count)

    async def _check_circuit_breaker(self, ctx: LaunchContext) -> bool:
        """Delegate to ``circuit_breaker.check_before_launch``."""
        from .circuit_breaker import check_circuit_breaker
        res = await check_circuit_breaker(self, ctx)
        return res is not None and not res.success

    async def _emit_launcher_error_toast(self, ctx: LaunchContext, err_code: str) -> None:
        """Delegate to ``error_toasts.emit_launcher_error``."""
        from .error_toasts import emit_launcher_error_toast
        await emit_launcher_error_toast(self, ctx, err_code)

    async def _handle_launcher_error(self, ctx: LaunchContext, err: Any) -> Result:
        """Delegate to ``error_toasts.handle_launcher_error``."""
        from .error_toasts import handle_launcher_error
        return await handle_launcher_error(self, ctx, err)

    async def _launch_windows(self, ctx: LaunchContext, state: RuntimeState) -> Result:
        """Delegate to ``orchestrator.launch_windows``."""
        from .orchestrator import launch_windows
        return await launch_windows(self, ctx, state)

    async def _launch_native(self, ctx: LaunchContext, state: RuntimeState) -> Result:
        """Delegate to ``orchestrator.launch_native``."""
        from .orchestrator import launch_native
        return await launch_native(self, ctx, state)

    async def _prepare_windows_plan(self, ctx: LaunchContext, state: RuntimeState) -> tuple[ProtonLaunchPlan, object]:
        """Delegate to ``helpers.prepare_windows_plan``."""
        from .helpers import prepare_windows_plan
        return await prepare_windows_plan(self, ctx, state)

    async def _cloud_sync_phase(self, ctx: LaunchContext, direction: str) -> None:
        """Delegate to ``helpers.cloud_sync_phase``."""
        from .helpers import cloud_sync_phase
        await cloud_sync_phase(self, ctx, direction)

    async def _run_game_subprocess(self, plan: ProtonLaunchPlan, ctx: LaunchContext, state: RuntimeState) -> int:
        """Delegate to ``helpers.run_game_subprocess``."""
        from .helpers import run_game_subprocess
        return await run_game_subprocess(self, plan, ctx, state)

    async def _sync_saves_and_track_size(self, ctx: LaunchContext, phase: str) -> None:
        """Delegate to ``helpers.sync_saves_and_track_size``."""
        from .helpers import sync_saves_and_track_size
        await sync_saves_and_track_size(self, ctx, phase)

    def _resolve_exit_code(self, state: RuntimeState) -> int:
        """Delegate to ``helpers.resolve_exit_code``."""
        from .helpers import resolve_exit_code
        return resolve_exit_code(self, state)

    def _elapsed_since_launch(self) -> float:
        """Delegate to ``helpers.elapsed_since_launch``."""
        from .helpers import elapsed_since_launch
        return elapsed_since_launch(self)
