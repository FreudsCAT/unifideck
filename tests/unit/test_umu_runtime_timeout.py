"""Unit tests for run_umu_with_retry / _run_umu_once timeout + kill.

Regression: the umu helper used for the prefix-warmup compat steps
(winetricks + the vcruntime ``regedit`` import) did a bare
``await proc.wait()`` with no timeout and no process-group kill. When a
broken auto-updated Proton-Experimental build hung the Wine session,
that ``regedit`` step blocked forever, and because the install queue is
serial it wedged every game behind it. ``createprefix`` had the bound;
this path didn't.

These tests pin the ported behaviour: a bounded call force-kills the
whole (detached) process group on timeout and returns the non-recoverable
``UMU_TIMEOUT_RC``; the default (``timeout=None``) still waits unbounded
so a real game launch — which runs for hours through the same helper —
is never truncated.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from unifideck.launcher.proton.infrastructure import umu_runtime as ur


async def test_run_umu_kills_hung_process_group_on_timeout(tmp_path):
    """A step that outlives its timeout is SIGKILLed, tree and all."""
    pid_file = tmp_path / "pid"
    # bash writes its own pid then execs a long sleep — the sleep inherits
    # the session/process group, so only a killpg (not kill of the parent)
    # reaps it. Mirrors the real umu-run → wineserver detachment.
    argv = ["/bin/bash", "-c", f"echo $$ > {pid_file}; exec sleep 100"]

    rc = await ur.run_umu_with_retry(argv, timeout=0.2)

    assert rc == ur.UMU_TIMEOUT_RC
    pid = int(pid_file.read_text().strip())
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


async def test_run_umu_timeout_rc_is_not_recoverable(tmp_path):
    """A timeout must NOT loop into a retry (a hung Proton hangs again).

    max_attempts=3 but the first timeout returns immediately — a retried
    hang would multiply the stall, not recover it.
    """
    calls = 0
    orig = ur._run_umu_once

    async def _counting(*args, **kwargs):
        nonlocal calls
        calls += 1
        return await orig(*args, **kwargs)

    ur._run_umu_once = _counting  # type: ignore[assignment]
    try:
        argv = ["/bin/bash", "-c", "exec sleep 100"]
        rc = await ur.run_umu_with_retry(argv, timeout=0.2, max_attempts=3)
    finally:
        ur._run_umu_once = orig  # type: ignore[assignment]

    assert rc == ur.UMU_TIMEOUT_RC
    assert calls == 1


async def test_run_umu_completes_normally_within_timeout():
    """A fast, clean process returns its real rc, not the sentinel."""
    rc = await ur.run_umu_with_retry(["/bin/true"], timeout=30)
    assert rc == 0


async def test_run_umu_unbounded_default_does_not_kill(tmp_path):
    """timeout=None (the launch default) waits unbounded — no kill path.

    The process finishes on its own well inside the test; the point is
    that a None timeout never routes through wait_for/killpg, so a
    long-running game launch is never truncated.
    """
    marker = tmp_path / "done"
    argv = ["/bin/bash", "-c", f"sleep 0.3; echo ok > {marker}"]

    rc = await ur.run_umu_with_retry(argv)  # no timeout kwarg → None

    assert rc == 0
    assert marker.read_text().strip() == "ok"


async def test_run_umu_reaps_process_group_on_cancel(tmp_path):
    """Cancelling the awaiting task SIGKILLs the detached subprocess tree.

    This is the Cancel button on a "Setting up game…" install: without
    the CancelledError handler the umu-run/wineserver tree outlives the
    cancelled coroutine and keeps spinning.
    """
    pid_file = tmp_path / "pid"
    argv = ["/bin/bash", "-c", f"echo $$ > {pid_file}; exec sleep 100"]

    task = asyncio.ensure_future(ur.run_umu_with_retry(argv))
    # Let bash spawn and record its pid before cancelling.
    while not pid_file.exists():
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    pid = int(pid_file.read_text().strip())
    # Give the killpg a moment to land.
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            break
        await asyncio.sleep(0.02)
    with pytest.raises(ProcessLookupError):
        os.kill(pid, 0)


def test_kill_process_group_kills_real_process():
    import subprocess
    from types import SimpleNamespace

    proc = subprocess.Popen(["sleep", "100"], start_new_session=True)
    ur._kill_process_group(SimpleNamespace(pid=proc.pid))

    proc.wait(timeout=2)
    assert proc.returncode is not None


def test_kill_process_group_swallows_already_dead_process():
    import subprocess
    from types import SimpleNamespace

    proc = subprocess.Popen(["true"], start_new_session=True)
    proc.wait()

    # Must not raise even though the group is already gone.
    ur._kill_process_group(SimpleNamespace(pid=proc.pid))


# --------------------------------------------------------------------------
# the wineserver reap is prefix-scoped, so it is opt-out
# --------------------------------------------------------------------------
#
# Regression (Battle.net installs stalled inside a minute): the reap kills
# every holder of a prefix's wineserver, not just this run's tree. Phase C
# sends an ``--exec`` to a client ANOTHER run started in the same prefix and
# is bounded by a 60 s timer that always fires — so every launch SIGKILLed
# the live client, and the Battle.net Agent died mid-download. Measured
# on-device: the Agent's log went silent at the reap's exact timestamp,
# twice, with the download frozen at 27%.


def _spy_reap(monkeypatch) -> list[object]:
    seen: list[object] = []
    monkeypatch.setattr(ur, "_reap_prefix_wineserver", lambda env: seen.append(env))
    return seen


async def _run_until_spawned(argv, pid_file, **kwargs):
    """Start a run, wait for the child to exist, then cancel it."""
    task = asyncio.ensure_future(ur.run_umu_with_retry(argv, **kwargs))
    while not pid_file.exists():
        await asyncio.sleep(0.02)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    return int(pid_file.read_text().strip())


async def _assert_gone(pid: int) -> None:
    for _ in range(50):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return
        await asyncio.sleep(0.02)
    pytest.fail(f"pid {pid} survived the killpg")


async def test_cancel_skips_the_wineserver_reap_when_opted_out(tmp_path, monkeypatch):
    """The fix: phase C's cancel must not touch the client's wineserver.

    The killpg still has to run — opting out of the reap must not leak
    this run's own detached pressure-vessel tree.
    """
    seen = _spy_reap(monkeypatch)
    pid_file = tmp_path / "pid"
    argv = ["/bin/bash", "-c", f"echo $$ > {pid_file}; exec sleep 100"]

    pid = await _run_until_spawned(
        argv, pid_file, env={"WINEPREFIX": str(tmp_path)}, reap_wineserver=False,
    )

    assert seen == []
    await _assert_gone(pid)


async def test_cancel_reaps_the_wineserver_by_default(tmp_path, monkeypatch):
    """Guards the default: flipping it would re-open the warmup wedge.

    Every other caller is one umu run per prefix, where an orphaned
    wineserver deadlocks the retry against the same lock.
    """
    seen = _spy_reap(monkeypatch)
    pid_file = tmp_path / "pid"
    argv = ["/bin/bash", "-c", f"echo $$ > {pid_file}; exec sleep 100"]

    await _run_until_spawned(argv, pid_file, env={"WINEPREFIX": str(tmp_path)})

    assert seen == [{"WINEPREFIX": str(tmp_path)}]


async def test_timeout_path_also_honours_the_opt_out(tmp_path, monkeypatch):
    """_reap_umu_tree has two call sites; both must respect the flag."""
    seen = _spy_reap(monkeypatch)
    argv = ["/bin/bash", "-c", "exec sleep 100"]

    rc = await ur.run_umu_with_retry(
        argv, env={"WINEPREFIX": str(tmp_path)}, timeout=0.2, reap_wineserver=False,
    )

    assert rc == ur.UMU_TIMEOUT_RC
    assert seen == []


async def test_timeout_path_reaps_by_default(tmp_path, monkeypatch):
    seen = _spy_reap(monkeypatch)
    argv = ["/bin/bash", "-c", "exec sleep 100"]

    await ur.run_umu_with_retry(
        argv, env={"WINEPREFIX": str(tmp_path)}, timeout=0.2,
    )

    assert seen == [{"WINEPREFIX": str(tmp_path)}]
