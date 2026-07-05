"""Regression: Epic launches must never expose STORE=egs to ProtonFixes.

ProtonFixes' EGS-store defaults are actively harmful, not just redundant:
they re-run vcrun2022 (core-dumps inside pressure-vessel) and — the one
that actually breaks launches — add a HKCR\\com.epicgames.launcher registry
key that makes the EOS SDK switch to launcher-IPC auth mode, causing an
instant exit/hang for non-Ubisoft Epic titles that use EOS (the retired
bash launcher forced STORE=none for exactly this reason).

Field case: Kingdom Hearts Re:Chain of Memories never launched, on any
Proton version, even after deleting and recreating the prefix — because
compat/vcruntime.py's regedit step (and prefix_init.py's createprefix, and
winetricks.py) all build their env as ``dict(plan.env)``, which carried
STORE=egs generically. That poisoned the prefix's registry on the very
first setup step, before the actual game ever ran — so a fresh prefix hit
the exact same corruption immediately. build_legendary_env() already forced
STORE=none for the final legendary invocation, but that was too late: the
damage was already done by the earlier setup steps sharing the same env.
"""
from __future__ import annotations

from pathlib import Path

from unifideck.launcher.proton.infrastructure import core
from unifideck.launcher.types.context import LaunchContext, RuntimeState


def _prepare(tmp_path, monkeypatch, store):
    ctx = LaunchContext(
        store=store,
        game_id="game1",
        exe_path=Path("/dev/null"),
        work_dir=tmp_path,
        plugin_dir=tmp_path,
    )
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    monkeypatch.setattr(core, "_resolve_prefix", lambda c: prefix)
    monkeypatch.setattr(core, "_lookup_umu_id", lambda c, s, p: None)
    monkeypatch.setattr(
        core, "_locate_umu_wrapper", lambda p, d: tmp_path / "umu-run",
    )
    return core.proton_prepare(
        ctx, RuntimeState(),
        python_bin=Path("/usr/bin/python3"),
        proton_path=tmp_path / "proton",
        proton_tool_id="GE-Proton10-34",
    )


def test_epic_launch_forces_store_none(tmp_path, monkeypatch):
    plan = _prepare(tmp_path, monkeypatch, "epic")
    assert plan.env["STORE"] == "none"
    # Diagnostics still record the real store code.
    assert plan.state.umu_store_code == "egs"


def test_gog_launch_keeps_real_store(tmp_path, monkeypatch):
    plan = _prepare(tmp_path, monkeypatch, "gog")
    assert plan.env["STORE"] == "gog"


def test_ubisoft_launch_keeps_real_store(tmp_path, monkeypatch):
    plan = _prepare(tmp_path, monkeypatch, "ubisoft")
    assert plan.env["STORE"] == "ubisoft"
