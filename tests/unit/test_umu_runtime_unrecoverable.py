"""Regression: a runtime umu cannot re-download must fail loudly, not spin.

Field report. umu fetches the Steam Linux Runtime from
``repo.steampowered.com/<variant>/images/latest-public-beta``. Those
``latest-*`` entries are symlinks, and the repo now answers them with
HTTP 403 while real numbered version dirs still return 200::

    steamrt3/images/3.0.20260714.251853/SHA256SUMS  -> 200
    steamrt3/images/latest-public-beta/SHA256SUMS   -> 403

umu handles that asymmetrically: its *update* path logs the 403 and keeps
using the runtime already on disk, but its *install* path RAISES. So a
variant that is present keeps working, while a variant that has been
deleted can never come back.

That made ``repair_incomplete_umu_runtime`` — which deletes a broken
variant expecting umu to re-fetch it — a spin: umu leaves a fresh stub,
we delete it, forever. Field logs showed it fire three times in a single
launch. Worse, umu exits **0** on the resulting
``FileNotFoundError: ... Runtime Platform missing or download incomplete``,
so the launcher reported SUCCESS for a game that never started.
"""
from __future__ import annotations

import pytest

from unifideck.launcher.proton.infrastructure import umu_runtime as ur


@pytest.fixture(autouse=True)
def _isolated_cache(tmp_path, monkeypatch):
    # The "already tried" state is an on-disk marker under UMU_CACHE_DIR, so
    # redirecting the cache dir isolates it for free — no global to reset.
    monkeypatch.setattr(ur, "UMU_CACHE_DIR", tmp_path)
    return tmp_path


def test_repair_state_expires_so_a_fixable_runtime_self_heals_again(
    _isolated_cache, monkeypatch,
):
    """A stale marker must not disable UD-084 self-heal forever."""
    _broken(_isolated_cache, "steamrt4")
    ur.repair_incomplete_umu_runtime()
    stub = _broken(_isolated_cache, "steamrt4")

    # Age the marker past its TTL.
    marker = ur._repair_marker("steamrt4")
    old = marker.stat().st_mtime - ur._REPAIR_MARKER_TTL_SECONDS - 1
    import os
    os.utime(marker, (old, old))

    ur.repair_incomplete_umu_runtime()

    assert not stub.exists(), "expired marker must allow another repair"


def _broken(cache, variant: str):
    d = cache / variant
    d.mkdir(parents=True, exist_ok=True)
    (d / "VERSIONS.txt").write_text("payload but no entry point\n")
    return d


def _healthy(cache, variant: str):
    d = cache / variant
    d.mkdir(parents=True, exist_ok=True)
    (d / "_v2-entry-point").write_text("#!/bin/sh\n")
    (d / "umu").symlink_to(d / "_v2-entry-point")
    return d


def test_first_repair_deletes_the_broken_variant(_isolated_cache):
    broken = _broken(_isolated_cache, "steamrt4")

    ur.repair_incomplete_umu_runtime()

    assert not broken.exists()
    assert not ur.unrecoverable_runtime_variants(), "not yet terminal"


def test_second_repair_leaves_it_alone_instead_of_spinning(_isolated_cache):
    """umu recreated a 403 stub — deleting it again would loop forever."""
    _broken(_isolated_cache, "steamrt4")
    ur.repair_incomplete_umu_runtime()

    # umu tried to install, 403'd, left a fresh stub behind.
    stub = _broken(_isolated_cache, "steamrt4")
    ur.repair_incomplete_umu_runtime()

    assert stub.exists(), "must NOT delete a variant it already failed to fix"
    assert ur.unrecoverable_runtime_variants() == ["steamrt4"]


def test_healthy_variant_is_never_flagged(_isolated_cache):
    _healthy(_isolated_cache, "steamrt3")

    ur.repair_incomplete_umu_runtime()
    ur.repair_incomplete_umu_runtime()

    assert (_isolated_cache / "steamrt3" / "umu").is_file()
    assert ur.unrecoverable_runtime_variants() == []


def test_absent_variant_is_not_unrecoverable(_isolated_cache):
    """A first-ever launch has no runtime yet — that's normal, not an error."""
    ur.repair_incomplete_umu_runtime()

    assert ur.unrecoverable_runtime_variants() == []


def test_variant_that_repairs_successfully_clears(_isolated_cache):
    """If umu DOES manage to install it, we must not report it broken."""
    _broken(_isolated_cache, "steamrt4")
    ur.repair_incomplete_umu_runtime()
    _healthy(_isolated_cache, "steamrt4")  # umu succeeded this time

    assert ur.unrecoverable_runtime_variants() == []


async def test_dispatch_raises_instead_of_reporting_false_success(
    _isolated_cache, monkeypatch,
):
    """The silent-success bug: umu exits 0, launcher must NOT call that a win."""
    from unifideck.launcher import proton
    from unifideck.launcher.types.errors import UmuRuntimeError

    monkeypatch.setattr(proton, "UMU_CACHE_DIR", _isolated_cache)
    _broken(_isolated_cache, "steamrt4")
    ur.repair_incomplete_umu_runtime()
    _broken(_isolated_cache, "steamrt4")  # 403 stub is back

    async def _should_not_run(_plan):
        pytest.fail("dispatch must abort before spawning umu")

    monkeypatch.setattr(proton, "generic_launch", _should_not_run)

    plan = type("P", (), {"context": type("C", (), {"store": "gog"})()})()
    with pytest.raises(UmuRuntimeError, match="could not"):
        await proton.dispatch(plan)
