"""Unit tests for the GOG launcher-stub fallback (``_run_gog_with_fallback``).

Regression (UD-022): a GOG game launched through forced compatibility
ran the primary exe through a full 2-attempt umu retry AND then, if it
exited suspiciously fast, ran the fallback game exe through ANOTHER full
2-attempt retry. One Play press could therefore fire the "Retrying"
toast — and, for corruption codes, wipe the shared runtime cache — up to
4× (2 primary + 2 fallback).

The fix gives the fallback exe a single attempt (``max_attempts=1``),
since the fallback is itself a higher-level retry (a *different* exe).
These tests pin:
  * the fallback exe runs with ``max_attempts=1`` (primary still 2);
  * the fallback only runs when the primary fails fast (rc != 0 AND
    elapsed < EARLY_EXIT_SECONDS) and resolves to a different exe.

``_run_umu_exe`` is stubbed with a spy so no real umu is spawned; it
records the ``max_attempts`` each call received. ``time.monotonic`` is
scripted to control the elapsed-time branch deterministically.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from unifideck.launcher.proton.compat import gog


def _make_plan(exe_path: str):
    """Minimal ProtonLaunchPlan stub — only the attributes the code reads."""
    return SimpleNamespace(
        context=SimpleNamespace(exe_path=Path(exe_path)),
        state=SimpleNamespace(wrappers=[], game_args=[]),
        env={},
        python_bin="/usr/bin/python3",
        umu_wrapper="/umu/umu-run",
        prefix_path="/prefix",
        on_process_start=None,
    )


@pytest.fixture()
def gog_harness(monkeypatch):
    """Stub Comet, the fallback resolver, the clock, and ``_run_umu_exe``.

    Returns an object exposing the recorded ``_run_umu_exe`` calls
    (each a ``(exe_basename, max_attempts)`` tuple) plus setters to
    script the exit codes, the elapsed clock, and the resolver result.
    """
    calls: list[tuple[str, int]] = []
    state = {"codes": [], "clock": [0.0, 0.0], "fallback": None}

    async def _spy_run_umu_exe(_plan, exe_path, _work_dir, *, max_attempts=2):
        calls.append((Path(exe_path).name, max_attempts))
        return state["codes"].pop(0)

    def _fake_monotonic():
        # First call = start, second = after the primary run.
        return state["clock"].pop(0) if state["clock"] else 0.0

    def _fake_resolve(_install_path):
        return state["fallback"]

    monkeypatch.setattr(gog, "_run_umu_exe", _spy_run_umu_exe)
    monkeypatch.setattr(gog, "start_comet", lambda _plan: None)
    monkeypatch.setattr(gog, "resolve_fallback_exe", _fake_resolve)
    monkeypatch.setattr(gog.time, "monotonic", _fake_monotonic)

    class _H:
        @staticmethod
        def calls():
            return calls

        @staticmethod
        def script(*, codes, elapsed, fallback):
            state["codes"] = list(codes)
            # start=0, after-primary=elapsed → elapsed seconds observed.
            state["clock"] = [0.0, float(elapsed)]
            state["fallback"] = fallback

    return _H


async def test_fallback_exe_runs_single_attempt(gog_harness):
    """Primary fast-fails → fallback runs with max_attempts=1 (primary=2)."""
    gog_harness.script(
        codes=[3, 0],  # primary fails, fallback succeeds
        elapsed=2,  # < EARLY_EXIT_SECONDS → treated as a stub bail-out
        fallback="/games/rcg2/game.exe",
    )
    plan = _make_plan("/games/rcg2/launcher.exe")

    rc = await gog._run_gog_with_fallback(plan, Path("/games/rcg2"))

    assert rc == 0
    assert gog_harness.calls() == [
        ("launcher.exe", 2),  # primary keeps the full retry budget
        ("game.exe", 1),  # fallback gets a single attempt — the UD-022 fix
    ]


async def test_no_fallback_when_primary_succeeds(gog_harness):
    """rc=0 primary → no fallback consulted."""
    gog_harness.script(codes=[0], elapsed=2, fallback="/games/x/game.exe")
    plan = _make_plan("/games/x/launcher.exe")

    rc = await gog._run_gog_with_fallback(plan, Path("/games/x"))

    assert rc == 0
    assert gog_harness.calls() == [("launcher.exe", 2)]


async def test_no_fallback_when_slow_exit(gog_harness):
    """Primary fails but ran long enough → a real crash, not a stub bail."""
    gog_harness.script(
        codes=[1],
        elapsed=gog.EARLY_EXIT_SECONDS + 5,  # >= threshold
        fallback="/games/x/game.exe",
    )
    plan = _make_plan("/games/x/launcher.exe")

    rc = await gog._run_gog_with_fallback(plan, Path("/games/x"))

    assert rc == 1
    assert gog_harness.calls() == [("launcher.exe", 2)]


async def test_no_fallback_when_resolver_returns_same_exe(gog_harness):
    """Resolver returning the primary exe must not re-run it as a fallback."""
    gog_harness.script(
        codes=[3],
        elapsed=2,
        fallback="/games/x/launcher.exe",  # same as primary
    )
    plan = _make_plan("/games/x/launcher.exe")

    rc = await gog._run_gog_with_fallback(plan, Path("/games/x"))

    assert rc == 3
    assert gog_harness.calls() == [("launcher.exe", 2)]


async def test_no_fallback_when_resolver_returns_none(gog_harness):
    """No fallback exe resolved → primary rc is returned as-is."""
    gog_harness.script(codes=[3], elapsed=2, fallback=None)
    plan = _make_plan("/games/x/launcher.exe")

    rc = await gog._run_gog_with_fallback(plan, Path("/games/x"))

    assert rc == 3
    assert gog_harness.calls() == [("launcher.exe", 2)]
