"""services.launcher.service — LauncherService DI facade.
LauncherService is the single entry point used by main.py and the
dispatcher CLI. It holds references to the existing backend
services (ShortcutService for games_map access, ProtonService for
compat-tool resolution, CloudSaveService for pre/post sync, and
EdgeBrowser for xCloud + OAuth kiosk modes) and orchestrates a
single launch end-to-end.
**No duplication of existing code.** Every non-trivial piece of
logic is delegated to a backend service that already implements
it. The only code that lives in this subpackage is:
    - The dispatch logic (which store handler to call for a given
            LaunchContext) — in ``launch`` below
    - The signal-handler wiring (signals.py)
    - The launch stage event sequence (launched → stopped)
    - Wrapping subprocess.run calls for store-specific CLI tools
            (legendary, gogdl, nile — these aren't a service)
Module layout
-------------
This class used to live as a single 821 LOC module. During the
2026-04-18 volumetry refactor it was split into five sibling
modules that this class delegates to:
        - ``circuit_breaker``  : pre-launch failure-protection
        - ``error_toasts``     : post-failure user reporting
        - ``orchestrator``     : per-platform launch entry points
        - ``helpers``          : technical primitives for launch flows
        - ``builder``          : standalone-CLI factory (separate file)
Dependencies (all injected — never instantiated here):
    - bus: EventBus for emit_game_launched / _stopped
    - shortcut_svc: ShortcutService for games_map read/write
    - proton_svc: ProtonService for compat tool selection
    - cloud_svc: CloudSaveService for sync_down / sync_up
    - edge_browser: EdgeBrowser for auth flows and xCloud kiosk mode
The service_bootstrap already wires up the four services above.
LauncherService joins the bootstrap order after all of them so
its dependencies are guaranteed to be ready at construction time.
"""

from __future__ import annotations
import logging
from typing import TYPE_CHECKING, Any
from ...core.types import Result
from ...launcher.rpc import (
    emit_game_launched,
    emit_game_stopped,
    emit_stage,
)
from ...launcher.signals import (
    GameProcessRegistry,
    SignalState,
    install_signal_handlers,
)
from ...launcher.types.context import LaunchContext, RuntimeState
from ...launcher.types.errors import LauncherError
from . import circuit_breaker, error_toasts, helpers, orchestrator

if TYPE_CHECKING:
    from ...auth.edge_browser import EdgeBrowser
    from ...event_bus import EventBus
    from ...launcher.proton.infrastructure.core import ProtonLaunchPlan
    from ..cloud_save import CloudSaveService
    from ..proton_service import ProtonService
    from ..shortcut import ShortcutService
logger = logging.getLogger(__name__)


class LauncherService:
    """Facade that orchestrates a single game launch.
    Injected by ServiceBootstrap with references to every backend
    service it needs. Does not instantiate anything itself. Does
    not duplicate logic that lives elsewhere — the bash modules
    that used to reimplement Proton selection, cloud save sync,
    and Edge kiosk launch are replaced by delegation to the
    existing services.
    """

    def __init__(  # noqa: D107 — class docstring documents the constructor's contract
        self,
        bus: EventBus,
        shortcut_svc: ShortcutService,
        proton_svc: ProtonService,
        cloud_svc: CloudSaveService,
        edge_browser: EdgeBrowser,
        config: Any | None = None,
        launch_history: Any | None = None,
    ) -> None:
        """Initialize the instance."""
        self._bus = bus
        self._shortcut_svc = shortcut_svc
        self._proton_svc = proton_svc
        self._cloud_svc = cloud_svc
        self._edge_browser = edge_browser
        self._config = config
        self._launch_history = launch_history
        self._signal_state = SignalState()
        self._registry = GameProcessRegistry(self._signal_state)

    async def start(self) -> None:
        """Install signal handlers. Called by ServiceBootstrap.
        Idempotent: re-installing handlers during hot-reload is
        safe. The state is reused so any tracked PIDs from the
        previous instance are still honoured.
        """
        install_signal_handlers(self._registry)
        logger.info("[LauncherService] signal handlers installed")

    async def stop(self) -> None:
        """Bootstrap teardown hook. No-op for now.
        Signal handlers are process-global and don't need to be
        removed — they'll be replaced if another instance starts.
        """

    # ══════════════════════════════════════════════════════════
    # Public API
    # ══════════════════════════════════════════════════════════

    async def launch(self, ctx: LaunchContext) -> Result:
        """Launch a game described by the immutable LaunchContext.
        Dispatches on the context flags to the right handler:
        Windows games via Proton, xCloud via the streaming helper,
        native Linux, or OAuth auth actions.
        Control flow:
                        1. Check circuit breaker — short-circuit if open
                        2. Emit stage toast "launching ${game_title}"
                        3. If ctx.is_launch_action is False: delegate to auth
                        4. If ctx.is_xcloud: delegate to xcloud handler
                        5. If ctx.is_windows_game: delegate to Windows launcher
                        6. Otherwise: delegate to native Linux launcher
                        7. On LauncherError: record failure + emit toast
        """
        logger.info(
            "[LauncherService] launch request: %s",
            ctx.to_log_dict,
        )
        state = RuntimeState()
        refusal = await self._check_circuit_breaker(ctx)
        if refusal is not None:
            return refusal
        # Record subprocess start time for fast-boot detection in
        # the finally block. monotonic to be immune to NTP jumps.
        import time as _time

        self._launch_started_at = _time.monotonic()
        try:
            await emit_stage(
                self._bus,
                i18n_key="toasts.launcher.launchingGame",
                game_title=ctx.game_key,
                priority="low",
            )
            if not ctx.is_launch_action:
                # OAuth shortcut path — delegate to auth.py
                from ...launcher.flows.auth import handle_store_auth

                return await handle_store_auth(ctx, self._edge_browser)
            if ctx.is_xcloud:
                return await self._launch_xcloud(ctx)
            if ctx.is_windows_game:
                return await self._launch_windows(ctx, state)
            # Native Linux game — delegate to native.py
            return await self._launch_native(ctx, state)
        except LauncherError as err:
            return await self._handle_launcher_error(ctx, err)
        finally:
            # No ``return`` in this ``finally`` (B012): real Result
            # is emitted from the try/except branches above. Circuit
            # breaker post-flight classification is handled in
            # LaunchHistoryService._on_game_stopped via @subscribe.
            logger.info(
                "[LauncherService] launch finished: state=%s signal=%s",
                state.to_log_dict,
                self._signal_state.terminated_by_signal,
            )

    async def _launch_xcloud(self, ctx: LaunchContext) -> Result:
        """xCloud streaming path.
        Delegates to the ``launch_xcloud`` helper. We fire
        ``GAME_LAUNCHED`` before streaming so Playtime tracking
        starts immediately, and ``GAME_STOPPED`` in the finally
        so the counterpart fires even if the browser throws.
        """  # noqa: D403 — "xCloud" is the product name
        from ...launcher.flows.xcloud import launch_xcloud

        await emit_game_launched(
            self._bus,
            store=ctx.store,
            game_id=ctx.game_id,
        )
        try:
            return await launch_xcloud(ctx, self._edge_browser)
        finally:
            # No ``return`` here (B012) — real Result bubbled.
            await emit_game_stopped(
                self._bus,
                store=ctx.store,
                game_id=ctx.game_id,
                exit_code=0,
                elapsed_seconds=0.0,
                terminated_by_signal=False,
            )

    # ══════════════════════════════════════════════════════════
    # Thin delegators — extracted to sibling modules for cohesion
    # ══════════════════════════════════════════════════════════
    async def _get_launch_id_or_none(self) -> str | None:
        """Get launch ID or none."""
        return await circuit_breaker.get_launch_id_or_none(self)

    async def _emit_circuit_open_toast(
        self,
        ctx: LaunchContext,
        failure_count: int,
    ) -> None:
        """Emit circuit open toast."""
        await circuit_breaker.emit_circuit_open_toast(
            self,
            ctx,
            failure_count,
        )

    async def _check_circuit_breaker(self, ctx: LaunchContext) -> Result | None:
        """Check circuit breaker."""
        return await circuit_breaker.check_circuit_breaker(self, ctx)

    async def _emit_launcher_error_toast(
        self,
        ctx: LaunchContext,
        err_code: str,
    ) -> None:
        """Emit launcher error toast."""
        await error_toasts.emit_launcher_error_toast(
            self,
            ctx,
            err_code,
        )

    async def _handle_launcher_error(
        self,
        ctx: LaunchContext,
        err: LauncherError,
    ) -> Result:
        """Handle launcher error."""
        return await error_toasts.handle_launcher_error(self, ctx, err)

    async def _launch_windows(self, ctx: LaunchContext, state: RuntimeState) -> Result:
        """Launch windows."""
        return await orchestrator.launch_windows(self, ctx, state)

    async def _launch_native(self, ctx: LaunchContext, state: RuntimeState) -> Result:
        """Launch native."""
        return await orchestrator.launch_native(self, ctx, state)

    async def _prepare_windows_plan(
        self,
        ctx: LaunchContext,
        state: RuntimeState,
    ) -> tuple[ProtonLaunchPlan, object]:
        """Prepare windows plan."""
        return await helpers.prepare_windows_plan(self, ctx, state)

    async def _cloud_sync_phase(self, ctx: LaunchContext, direction: str) -> None:
        """Cloud sync phase."""
        await helpers.cloud_sync_phase(self, ctx, direction)

    async def _run_game_subprocess(
        self,
        plan: ProtonLaunchPlan,
        ctx: LaunchContext,
        state: RuntimeState,
    ) -> int:
        """Run game subprocess."""
        return await helpers.run_game_subprocess(self, plan, ctx, state)

    async def _sync_saves_and_track_size(self, ctx: LaunchContext, phase: str) -> None:
        """Sync saves and track size."""
        await helpers.sync_saves_and_track_size(self, ctx, phase)

    def _resolve_exit_code(self, state: RuntimeState) -> int:
        """Resolve exit code."""
        return helpers.resolve_exit_code(self, state)

    def _elapsed_since_launch(self) -> float:
        """Elapsed since launch."""
        return helpers.elapsed_since_launch(self)
