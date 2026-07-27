"""Regression: a runtime umu cannot re-download must fail loudly, not spin.

Field report, against umu <=1.4.1. It fetched the Steam Linux Runtime from
``repo.steampowered.com/<variant>/images/latest-public-beta[/VERSION.txt]``.
Those ``latest-*`` entries are symlinks, and the repo answers them with
HTTP 403 while real numbered version dirs still return 200::

    steamrt3/images/3.0.20260714.251853/SHA256SUMS  -> 200
    steamrt3/images/latest-public-beta/SHA256SUMS   -> 403

umu handled that asymmetrically: its *update* path logged the 403 and kept
using the runtime already on disk, but its *install* path RAISED. So a
variant that was present kept working, while a variant that had been
deleted could never come back.

That made ``repair_incomplete_umu_runtime`` — which deletes a broken
variant expecting umu to re-fetch it — a spin: umu leaves a fresh stub,
we delete it, forever. Field logs showed it fire three times in a single
launch. Worse, umu exits **0** on the resulting
``FileNotFoundError: ... Runtime Platform missing or download incomplete``,
so the launcher reported SUCCESS for a game that never started.

CURRENT STATE: the bundled umu is >=1.4.3, which reads
``images/latest-public-beta.txt`` and fetches from the numbered dir that
file names — both serve, so a wipe is recoverable again and the spin
cannot occur for this reason. These tests still hold, because the guard was
deliberately kept as a general loop breaker: "umu cannot install this
runtime" still happens with no network, a full disk, or a future repo
change. What changed is the TTL, now short enough that a transient cause
self-heals on the next launch instead of failing launches for ten minutes.
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


def _marker_only(cache, variant: str):
    """umu >=1.4.0's own "install finished" marker, without the entry point."""
    d = cache / variant
    d.mkdir(parents=True, exist_ok=True)
    (d / ur._UMU_INSTALL_MARKER).write_text("ok\n")
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


def test_umu_marker_does_not_excuse_a_missing_entry_point(_isolated_cache):
    """UD-084 in its 1.4.x form — the marker must NOT count as healthy.

    umu writes ``.installed.ok`` and only THEN creates the ``umu`` symlink,
    in a ``finally:``. So a runtime can carry the marker while its entry
    point is broken, and the marker is precisely what makes umu decline to
    reinstall it (``has_umu_setup`` reads it). Accepting the marker as an
    alternative signal would leave that runtime in place for umu to fail on
    — the exact wedge this repair exists to break.
    """
    d = _marker_only(_isolated_cache, "steamrt4")

    ur.repair_incomplete_umu_runtime()

    assert not d.exists(), (
        "a marked-installed runtime with no entry point must still be wiped"
    )


def test_entry_point_without_a_marker_is_healthy(_isolated_cache):
    """The reverse mismatch is umu's problem, not ours.

    No marker means umu will reinstall the runtime itself on the next run,
    and pre-1.4 runtimes have no marker at all — wiping them for that would
    throw away a perfectly good multi-hundred-MB download.
    """
    _healthy(_isolated_cache, "steamrt3")

    ur.repair_incomplete_umu_runtime()

    assert (_isolated_cache / "steamrt3").exists()
    assert ur.unrecoverable_runtime_variants() == []


def test_dangling_entry_point_without_marker_is_repaired(_isolated_cache):
    """UD-084's actual shape: symlink present, target gone, no marker.

    umu 1.4.x creates the ``umu -> _v2-entry-point`` symlink in a
    ``finally:`` block, so it exists even when validation FAILED — which is
    exactly why the marker has to win over the symlink.
    """
    d = _isolated_cache / "steamrt4"
    d.mkdir(parents=True)
    (d / "umu").symlink_to(d / "_v2-entry-point")  # target never created

    ur.repair_incomplete_umu_runtime()

    assert not d.exists(), "dangling entry point with no marker must be wiped"


def test_repair_ttl_is_short_enough_to_self_heal(_isolated_cache):
    """The TTL must outlast one launch's retries, not a whole session.

    Under the bundled umu a wipe IS recoverable, so a long TTL only means a
    transient failure keeps failing launches after the cause has cleared.
    """
    assert ur._REPAIR_MARKER_TTL_SECONDS <= 300, (
        "TTL was tuned for umu <=1.4.1, where a wiped runtime could never "
        "be re-downloaded; the bundled umu can re-download, so a long "
        "suppression window now blocks legitimate self-heal"
    )


def test_arm64_variant_participates_in_the_scans(_isolated_cache):
    """steamrt4-arm64 is a real umu variant — it must not be a silent no-op."""
    assert "steamrt4-arm64" in ur.UMU_RUNTIME_VARIANTS

    broken = _broken(_isolated_cache, "steamrt4-arm64")
    ur.repair_incomplete_umu_runtime()
    assert not broken.exists(), "arm64 variant must be repairable too"

    _broken(_isolated_cache, "steamrt4-arm64")
    ur.repair_incomplete_umu_runtime()
    assert ur.unrecoverable_runtime_variants() == ["steamrt4-arm64"]


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
