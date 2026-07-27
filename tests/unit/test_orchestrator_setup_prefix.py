"""Regression: first launch runs the canonical ``setup_prefix`` in Phase 1.5.

The install-time warmup and the first-launch path used to run two different
prefix-setup implementations. Warmup had the managed-GE recovery ladder and (now)
pins the winning Proton; the launch path ran only ``ensure_prefix_initialized``
in the orchestrator plus a bare ``apply_prefix_compat`` inside ``proton.dispatch``
with NO recovery — so a warmup that recovered to GE was undone at Play time.

These tests pin the unification:
  1. ``launch_windows`` calls ``setup_prefix`` (the ONE shared process) in
     Phase 1.5, before the cloud sync-down; and
  2. ``proton.dispatch`` no longer calls ``apply_prefix_compat`` (it moved into
     ``setup_prefix``, so it must not run twice per launch).
"""
from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.launcher import proton as proton_pkg
from unifideck.services.launcher import orchestrator as orch


class _Ctx(SimpleNamespace):
    pass


def _ctx():
    return _Ctx(store="epic", game_id="g1", game_key="epic:g1", steam_app_id=None)


def _fake_service():
    svc = MagicMock()
    plan = SimpleNamespace()
    svc._prepare_windows_plan = AsyncMock(return_value=(plan, None))
    svc._cloud_sync_phase = AsyncMock()
    svc._run_game_subprocess = AsyncMock(return_value=0)
    svc._resolve_exit_code = MagicMock(return_value=0)
    svc._bus = MagicMock(emit=AsyncMock())
    return svc


async def test_launch_windows_calls_setup_prefix_before_sync_down(monkeypatch):
    order: list[str] = []
    setup = AsyncMock(side_effect=lambda *a, **k: order.append("setup_prefix"))
    monkeypatch.setattr(proton_pkg, "setup_prefix", setup)

    svc = _fake_service()
    svc._cloud_sync_phase = AsyncMock(
        side_effect=lambda _ctx, direction: order.append(f"sync_{direction}"),
    )

    ctx = _ctx()
    await orch.launch_windows(svc, ctx, SimpleNamespace(rc=0))

    setup.assert_awaited_once()
    # setup_prefix runs with the launch ctx/state and NO session_env
    # (Steam provides the user session at launch).
    assert setup.await_args.args[0] is ctx
    assert setup.await_args.kwargs.get("session_env") in (None, {})
    # and it must precede the cloud sync-down (drive_c must exist first).
    assert order[0] == "setup_prefix"
    assert "sync_down" in order
    assert order.index("setup_prefix") < order.index("sync_down")


async def test_dispatch_does_not_run_compat(monkeypatch):
    # dispatch must route to the store handler WITHOUT calling apply_prefix_compat
    # (that now lives once in setup_prefix). Patch the compat entry to a spy and
    # assert it's never touched.
    from unifideck.launcher.proton import compat as compat_pkg

    compat_spy = AsyncMock(return_value=False)
    monkeypatch.setattr(compat_pkg, "apply_prefix_compat", compat_spy)
    monkeypatch.setattr(proton_pkg, "repair_incomplete_umu_runtime", MagicMock())

    epic_spy = AsyncMock(return_value=0)
    monkeypatch.setattr(proton_pkg, "epic_launch", epic_spy)

    plan = SimpleNamespace(context=SimpleNamespace(store="epic"))
    rc = await proton_pkg.dispatch(plan)

    assert rc == 0
    epic_spy.assert_awaited_once_with(plan)
    compat_spy.assert_not_called()
