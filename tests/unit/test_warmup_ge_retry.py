"""Tests for the warmup timeout → managed-GE retry.

A structurally-complete but runtime-hanging Proton wedged install warmup.
``apply_prefix_compat`` reports when a step was force-killed for timing
out, and ``warmup_install_prefix`` retries the setup ONCE with the
plugin-managed GE-Proton. These tests pin: retry-with-GE on timeout; no
retry when the hung Proton WAS already GE (no loop); no retry on a clean
run.

(An earlier revision inserted a repair-in-place rung here — official
Protons via SteamClient.Apps.VerifyApp, GE via re-install — between the
default attempt and the GE switch. Removed: across every hang it fired
on live, VerifyApp reported success but the same-Proton retry hung again
regardless, so it never changed the outcome. The actual install-warmup
hang was a missing user-session env for install-time umu runs, fixed in
``_user_session_env`` — see test_warmup_session_env.py and memory
install-hang-orphaned-wineserver-lock.md.)
"""
from __future__ import annotations

import contextlib
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.launcher import proton as proton_pkg
from unifideck.launcher.proton.compat import prefix_init as prefix_init_mod
from unifideck.services.download import prefix_warmup as warmup_mod


@pytest.fixture
def wired(monkeypatch):
    """Patch the lazy-imported launcher surface warmup pulls in.

    Returns the spies so each test can assert selection + retry behaviour.
    """
    monkeypatch.setattr(proton_pkg, "find_python_3_10_plus", lambda: "/usr/bin/python3")
    monkeypatch.setattr(
        proton_pkg, "proton_prepare",
        lambda ctx, state, **kw: SimpleNamespace(
            tool_id=kw["proton_tool_id"], env={},
        ),
    )

    @contextlib.contextmanager
    def _noop_suppress():
        yield

    monkeypatch.setattr(
        "unifideck.launcher.frontend_bridge.suppress_launcher_toasts",
        _noop_suppress,
    )

    ensure = AsyncMock()
    monkeypatch.setattr(prefix_init_mod, "ensure_prefix_initialized", ensure)

    return SimpleNamespace(ensure=ensure)


def _patch_compat(monkeypatch, timed_out_sequence):
    """apply_prefix_compat returns each bool in the sequence, in order."""
    from unifideck.launcher.proton import compat as compat_pkg

    seq = iter(timed_out_sequence)
    calls = []

    async def _compat(plan):
        val = next(seq)
        calls.append(plan.tool_id)
        return val

    monkeypatch.setattr(compat_pkg, "apply_prefix_compat", _compat)
    return calls


def _patch_selectors(monkeypatch, *, default_tool, ge_tool):
    monkeypatch.setattr(
        proton_pkg, "select_proton_version",
        lambda steam_app_id, store_game_id: ("/p/default", default_tool),
    )
    ge = MagicMock(return_value=("/p/ge", ge_tool))
    monkeypatch.setattr(proton_pkg, "select_managed_ge_proton", ge)
    return ge


async def test_retry_with_ge_on_compat_timeout(tmp_path, wired, monkeypatch):
    # default Proton hangs (timed_out=True), then GE retry succeeds (False).
    calls = _patch_compat(monkeypatch, [True, False])
    ge = _patch_selectors(
        monkeypatch, default_tool="proton_experimental", ge_tool="GE-Proton11-1",
    )

    await warmup_mod.warmup_install_prefix("gog", "123", str(tmp_path))

    ge.assert_called_once()
    # createprefix runs twice (default, then GE); compat runs against both.
    assert wired.ensure.await_count == 2
    assert calls == ["proton_experimental", "GE-Proton11-1"]


async def test_no_retry_when_hung_proton_was_already_ge(tmp_path, wired, monkeypatch):
    # GE itself hung — retrying with GE again would loop, so we must NOT.
    calls = _patch_compat(monkeypatch, [True])
    ge = _patch_selectors(
        monkeypatch, default_tool="GE-Proton11-1", ge_tool="GE-Proton11-1",
    )

    await warmup_mod.warmup_install_prefix("gog", "123", str(tmp_path))

    # select_managed_ge_proton is consulted to compare tool ids, but no
    # second createprefix/compat runs.
    ge.assert_called_once()
    assert wired.ensure.await_count == 1
    assert calls == ["GE-Proton11-1"]


async def test_no_retry_on_clean_run(tmp_path, wired, monkeypatch):
    calls = _patch_compat(monkeypatch, [False])
    ge = _patch_selectors(
        monkeypatch, default_tool="proton_experimental", ge_tool="GE-Proton11-1",
    )

    await warmup_mod.warmup_install_prefix("gog", "123", str(tmp_path))

    ge.assert_not_called()
    assert wired.ensure.await_count == 1
    assert calls == ["proton_experimental"]
