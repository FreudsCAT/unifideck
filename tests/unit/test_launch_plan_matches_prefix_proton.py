"""Regression: the game must run under the Proton that built its prefix.

``launch_windows`` freezes ``plan.env["PROTONPATH"]`` in Phase 1, then runs
``setup_prefix`` in Phase 1.5 — which has its own GE hang-recovery ladder and
can legitimately finish on a different Proton. Nothing reconciled the two, so
the game ran under the Phase-1 Proton inside a prefix another Proton had
created, compat-installed and version-stamped. A Wine prefix is single-Proton
state: that mismatch surfaces as Proton's own "Prefix has an invalid version?!"
and, in the field, as a game that simply never starts.

Worse, the divergence was invisible: ``run_game_subprocess`` logged
``proton=`` from ``state.proton_tool_id``, which ``_run_one`` had already
mutated to the setup Proton — so the log named a Proton the game was not
using.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from unifideck.launcher import proton as proton_pkg
from unifideck.services.launcher import orchestrator as orch


def _ctx():
    return SimpleNamespace(
        store="epic", game_id="g1", game_key="epic:g1", steam_app_id="4179000979",
    )


def _state(tool: str, path: Path):
    return SimpleNamespace(rc=0, proton_tool_id=tool, proton_path=path)


def _service(plan):
    svc = MagicMock()
    svc._prepare_windows_plan = AsyncMock(return_value=(plan, None))
    svc._cloud_sync_phase = AsyncMock()
    svc._run_game_subprocess = AsyncMock(return_value=0)
    svc._resolve_exit_code = MagicMock(return_value=0)
    svc._bus = MagicMock(emit=AsyncMock())
    return svc


def _plan(protonpath: Path):
    return SimpleNamespace(
        env={"PROTONPATH": str(protonpath)},
        python_bin=Path("/usr/bin/python3"),
        on_process_start=None,
    )


async def test_plan_follows_setup_prefix_when_the_ladder_switches(monkeypatch):
    user_proton = Path("/steam/common/Proton 9.0 (Beta)")
    ge_proton = Path("/compat/GE-Proton11-3")
    plan = _plan(user_proton)
    state = _state("proton_9", user_proton)

    async def _setup(_ctx, st, **_kw):
        # The GE hang-recovery ladder took over and published its winner.
        st.proton_tool_id, st.proton_path = "GE-Proton11-3", ge_proton
        return "GE-Proton11-3", True

    monkeypatch.setattr(proton_pkg, "setup_prefix", _setup)
    rebuilt = _plan(ge_proton)
    prepare = MagicMock(return_value=rebuilt)
    monkeypatch.setattr(proton_pkg, "proton_prepare", prepare)

    svc = _service(plan)
    await orch.launch_windows(svc, _ctx(), state)

    prepare.assert_called_once()
    assert prepare.call_args.kwargs["proton_tool_id"] == "GE-Proton11-3"
    assert prepare.call_args.kwargs["proton_path"] == ge_proton
    # The GAME runs the realigned plan, not the Phase-1 one.
    assert svc._run_game_subprocess.await_args.args[0] is rebuilt


async def test_plan_is_untouched_when_setup_keeps_the_same_proton(monkeypatch):
    """The common case must not rebuild the plan (or re-resolve Proton)."""
    user_proton = Path("/steam/common/Proton 9.0 (Beta)")
    plan = _plan(user_proton)
    state = _state("proton_9", user_proton)

    monkeypatch.setattr(proton_pkg, "setup_prefix", AsyncMock())
    prepare = MagicMock()
    monkeypatch.setattr(proton_pkg, "proton_prepare", prepare)

    svc = _service(plan)
    await orch.launch_windows(svc, _ctx(), state)

    prepare.assert_not_called()
    assert svc._run_game_subprocess.await_args.args[0] is plan


async def test_setup_prefix_is_handed_the_already_resolved_proton(monkeypatch):
    """One ``config.vdf`` read per launch — the two can't drift apart."""
    user_proton = Path("/steam/common/Proton 10.0")
    state = _state("proton_10", user_proton)
    setup = AsyncMock(return_value=("proton_10", False))
    monkeypatch.setattr(proton_pkg, "setup_prefix", setup)

    svc = _service(_plan(user_proton))
    await orch.launch_windows(svc, _ctx(), state)

    assert setup.await_args.kwargs["proton"] == (user_proton, "proton_10")
