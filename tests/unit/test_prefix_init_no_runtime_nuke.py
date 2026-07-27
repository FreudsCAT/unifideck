"""Regression: prefix init must never nuke the shared umu runtime cache.

Field report (SteamOS 3.8.16). ``_run_createprefix_with_retry`` called
``cleanup_umu_runtime_cache()`` between attempts, which deletes EVERY
variant under ``~/.local/share/umu``. The createprefix rc is meaningless
(Proton returns non-zero even on success), so that nuke was effectively
unconditional — any prefix-init failure destroyed a healthy shared
multi-hundred-MB runtime, then retried 5 s later, far too little time to
re-download.

Observed consequence across two bundles: steamrt3 deleted outright,
steamrt4 left a partial download, diagnostics reporting "steamrt4 missing
its entry point", and umu dying with::

    FileNotFoundError: _v2-entry-point (umu) cannot be found in
    '/home/deck/.local/share/umu/steamrt4'
    Runtime Platform missing or download incomplete

Because the runtime is shared, this broke EVERY store — including Epic
games that had been launching fine — and every subsequent launch re-broke
it. 22 launch attempts, 0 successes.

``repair_incomplete_umu_runtime`` is the correct tool: surgical (only a
present-but-broken variant), no-op on a healthy runtime, leaves siblings
alone.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from unifideck.launcher.proton.compat import prefix_init


@pytest.fixture
def _never_sleep(monkeypatch):
    async def _instant(_seconds):
        return None

    monkeypatch.setattr(prefix_init.asyncio, "sleep", _instant)


async def test_failed_createprefix_never_calls_cache_nuke(
    monkeypatch, tmp_path, _never_sleep,
):
    """The destructive whole-cache wipe must not be reachable from here."""
    nuked: list[str] = []
    repaired: list[str] = []

    # cleanup_umu_runtime_cache is no longer imported here at all; assert on
    # the source of truth so a future re-import also trips this test.
    monkeypatch.setattr(
        prefix_init, "repair_incomplete_umu_runtime",
        lambda: repaired.append("repair"),
    )
    import unifideck.launcher.proton.infrastructure.umu_runtime as ur
    monkeypatch.setattr(
        ur, "cleanup_umu_runtime_cache", lambda: nuked.append("nuke"),
    )

    async def _noop_umu(*_a, **_k):
        return None

    monkeypatch.setattr(prefix_init, "_run_umu", _noop_umu)
    monkeypatch.setattr(prefix_init, "launcher_toast", lambda *a, **k: None)

    # system.reg never appears -> every attempt "fails".
    ok = await prefix_init._run_createprefix_with_retry(
        plan=types.SimpleNamespace(), env={}, prefix_root=tmp_path,
    )

    assert ok is False
    assert nuked == [], "prefix init must never wipe the whole umu cache"
    # Surgical repair runs between attempts instead (attempts - 1 times).
    assert repaired, "expected the surgical repair to run between attempts"


async def test_successful_createprefix_does_no_repair_and_no_nuke(
    monkeypatch, tmp_path, _never_sleep,
):
    """A prefix that comes up first try must touch the runtime not at all."""
    nuked: list[str] = []
    repaired: list[str] = []
    monkeypatch.setattr(
        prefix_init, "repair_incomplete_umu_runtime",
        lambda: repaired.append("repair"),
    )
    import unifideck.launcher.proton.infrastructure.umu_runtime as ur
    monkeypatch.setattr(
        ur, "cleanup_umu_runtime_cache", lambda: nuked.append("nuke"),
    )
    monkeypatch.setattr(prefix_init, "launcher_toast", lambda *a, **k: None)

    reg = prefix_init.resolve_registry_prefix(tmp_path)
    reg.mkdir(parents=True, exist_ok=True)

    async def _make_system_reg(*_a, **_k):
        (reg / "system.reg").write_text("WINE REGISTRY\n")

    monkeypatch.setattr(prefix_init, "_run_umu", _make_system_reg)

    ok = await prefix_init._run_createprefix_with_retry(
        plan=types.SimpleNamespace(), env={}, prefix_root=tmp_path,
    )

    assert ok is True
    assert nuked == []
    assert repaired == []


def test_repair_is_surgical_and_spares_healthy_variants(monkeypatch, tmp_path):
    """Only the broken variant goes; a healthy sibling survives.

    This is the property that makes the swap safe: the old nuke took
    steamrt3 down with steamrt4.
    """
    import unifideck.launcher.proton.infrastructure.umu_runtime as ur

    monkeypatch.setattr(ur, "UMU_CACHE_DIR", tmp_path)

    healthy = tmp_path / "steamrt3"
    healthy.mkdir()
    (healthy / "_v2-entry-point").write_text("#!/bin/sh\n")
    (healthy / "umu").symlink_to(healthy / "_v2-entry-point")

    broken = tmp_path / "steamrt4"
    broken.mkdir()
    (broken / "VERSIONS.txt").write_text("x\n")  # payload, no entry point

    ur.repair_incomplete_umu_runtime()

    assert healthy.is_dir(), "healthy variant must survive"
    assert (healthy / "umu").is_file()
    assert not broken.exists(), "broken variant should be removed"


def test_run_umu_output_is_not_discarded():
    """createprefix output must reach game.log, not DEVNULL.

    Three silent attempts plus "prefix still missing system.reg" — with no
    umu output anywhere — is what made this cost two reporter round-trips.
    """
    src = Path(prefix_init.__file__).read_text()
    run_umu = src.split("async def _run_umu(", 1)[1]
    assert "open_game_log()" in run_umu
    assert "stdout=out" in run_umu
