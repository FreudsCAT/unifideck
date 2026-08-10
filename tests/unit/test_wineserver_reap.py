"""Tests for wineserver_reap — reaping orphaned wineservers by prefix.

Root cause of the recurring install-warmup wedge: killing the umu-run
process group leaves the ``waitforexitandrun`` wineserver detached and
alive, holding the prefix's ``/tmp/.wine-<uid>/server-<st_dev>-<st_ino>/lock``.
The next same-prefix run deadlocks on it; retries stack more stuck
wineservers on one lock. These tests pin the server-dir naming (matches
Wine's convention) and the fd-scan-based reap (kills exactly the holder,
nothing else).
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

from unifideck.launcher.proton.infrastructure import wineserver_reap as wr


def test_server_dir_name_matches_wine_convention(tmp_path):
    # Wine formats BOTH device and inode as lowercase hex (verified on
    # device: dev=66312 → "server-10308-..."). Assert against an
    # independently-computed hex string, NOT server_dir_for_prefix itself,
    # so a decimal-vs-hex regression is actually caught here.
    st = os.stat(tmp_path)
    expected = f"server-{st.st_dev:x}-{st.st_ino:x}"
    assert wr.server_dir_for_prefix(tmp_path) == expected
    # Guard the specific past bug: the device segment must be hex.
    assert f"-{st.st_dev:x}-" in wr.server_dir_for_prefix(tmp_path)
    if st.st_dev >= 10:  # decimal != hex for this dev
        assert f"server-{st.st_dev}-" != wr.server_dir_for_prefix(tmp_path)


def test_server_dir_none_for_missing_prefix(tmp_path):
    assert wr.server_dir_for_prefix(tmp_path / "does-not-exist") is None


def _spawn_holding(server_dir: Path) -> subprocess.Popen:
    """A child process that holds an open fd under ``server_dir`` and sleeps."""
    server_dir.mkdir(parents=True, exist_ok=True)
    lock = server_dir / "lock"
    lock.write_text("")
    # Open the lock fd, then sleep — mimics a wineserver holding server dir.
    code = (
        f"import time;\n"
        f"f=open({str(lock)!r});\n"
        f"time.sleep(60)\n"
    )
    return subprocess.Popen(["python3", "-c", code], start_new_session=True)


def test_reap_kills_process_holding_prefix_server_dir(tmp_path, monkeypatch):
    # Point the reaper's wine tmp at our fixture dir.
    wine_tmp = tmp_path / ".wine-test"
    monkeypatch.setattr(wr, "_WINE_TMP", wine_tmp)

    prefix = tmp_path / "prefixes" / "game1"
    prefix.mkdir(parents=True)
    server_name = wr.server_dir_for_prefix(prefix)
    assert server_name is not None

    holder = _spawn_holding(wine_tmp / server_name)
    # Let the child open the fd.
    for _ in range(50):
        if wr._pids_holding_server_dir(server_name):
            break
        time.sleep(0.02)

    killed = wr.reap_prefix_wineserver(prefix)
    assert killed >= 1

    holder.wait(timeout=3)
    assert holder.returncode is not None
    # SIGKILL → negative returncode -9 (or the signal number).
    assert holder.returncode in (-signal.SIGKILL, -9)


def test_reap_noop_when_nothing_holds_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(wr, "_WINE_TMP", tmp_path / ".wine-test")
    prefix = tmp_path / "prefixes" / "game2"
    prefix.mkdir(parents=True)
    assert wr.reap_prefix_wineserver(prefix) == 0


def test_reap_does_not_touch_other_prefix_server_dir(tmp_path, monkeypatch):
    # A holder on prefix B's server dir must survive a reap of prefix A.
    # This is the ONLY isolation the reap offers: a *different* prefix is
    # never touched. Within one prefix it kills everything — see
    # test_reap_kills_a_live_holder_on_the_same_prefix.
    wine_tmp = tmp_path / ".wine-test"
    monkeypatch.setattr(wr, "_WINE_TMP", wine_tmp)

    pa = tmp_path / "prefixes" / "A"
    pb = tmp_path / "prefixes" / "B"
    pa.mkdir(parents=True)
    pb.mkdir(parents=True)
    name_b = wr.server_dir_for_prefix(pb)

    holder_b = _spawn_holding(wine_tmp / name_b)
    for _ in range(50):
        if wr._pids_holding_server_dir(name_b):
            break
        time.sleep(0.02)

    # Reap A → must NOT kill B's holder.
    wr.reap_prefix_wineserver(pa)
    time.sleep(0.2)
    assert holder_b.poll() is None  # still alive

    holder_b.send_signal(signal.SIGKILL)
    holder_b.wait(timeout=3)


def test_sweep_reaps_and_cleans_stale_dirs(tmp_path, monkeypatch):
    wine_tmp = tmp_path / ".wine-test"
    monkeypatch.setattr(wr, "_WINE_TMP", wine_tmp)

    prefix = tmp_path / "prefixes" / "g"
    prefix.mkdir(parents=True)
    server_name = wr.server_dir_for_prefix(prefix)
    stale_dir = wine_tmp / server_name
    stale_dir.mkdir(parents=True)
    (stale_dir / "lock").write_text("")  # no holder → stale

    reaped = wr.sweep_orphaned_server_dirs([prefix])
    assert reaped == 0  # nothing was holding it
    # The abandoned lock dir is cleaned up.
    assert not stale_dir.exists()


def test_reap_kills_a_live_holder_on_the_same_prefix(tmp_path, monkeypatch):
    """Same prefix → it dies, live or orphaned, whoever started it.

    The executable form of the module's scope rule. An earlier docstring
    promised the reap "never touches a wineserver for a game the user is
    actually running", reasoning that a running game has its own prefix.
    That is false whenever one prefix hosts two umu runs — Battle.net,
    where phase C's timeout SIGKILLed the client phase A had started and
    froze the download. Callers that do not own the prefix must opt out
    via ``run_umu_with_retry(reap_wineserver=False)``.
    """
    wine_tmp = tmp_path / ".wine-test"
    monkeypatch.setattr(wr, "_WINE_TMP", wine_tmp)

    prefix = tmp_path / "prefixes" / "shared"
    prefix.mkdir(parents=True)
    server_name = wr.server_dir_for_prefix(prefix)
    assert server_name is not None

    # Two holders, as a Battle.net prefix really has: the client and its
    # Agent, both bound to the one wineserver.
    holders = [_spawn_holding(wine_tmp / server_name) for _ in range(2)]
    for _ in range(50):
        if len(wr._pids_holding_server_dir(server_name)) >= 2:
            break
        time.sleep(0.02)

    assert wr.reap_prefix_wineserver(prefix) >= 2

    for holder in holders:
        holder.wait(timeout=3)
        assert holder.returncode in (-signal.SIGKILL, -9)
