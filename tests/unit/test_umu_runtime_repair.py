"""Unit tests for ``repair_incomplete_umu_runtime`` (UD-084).

Regression: a umu setup that died after extracting the runtime payload
(``<variant>_platform_*`` / ``pressure-vessel`` / ``VERSIONS.txt``) but
before its LAST step — creating the ``umu -> _v2-entry-point`` symlink —
leaves a runtime that umu's own ``_update_umu`` treats as "up to date"
(it never checks the entry point). The next launch then dies in umu's
``build_command`` with a ``FileNotFoundError`` whose exit code is OUTSIDE
our ``_RECOVERABLE_CODES``, so ``run_umu_with_retry`` never self-heals it
and the user stays wedged.

``repair_incomplete_umu_runtime`` closes that gap: on every launch it
surgically removes only the broken variant dir (leaving healthy siblings)
so umu re-downloads it on the next ``umu-run``. These tests pin:
  * a healthy variant (real ``_v2-entry-point`` + ``umu`` link) is kept;
  * a variant with the payload but no ``umu`` link is removed;
  * a variant whose ``umu`` link dangles (target deleted) is removed —
    ``Path.is_file()`` follows the link, matching umu's own gate;
  * a healthy sibling is untouched when another variant is broken;
  * an empty/absent cache is a no-op (no error, nothing removed).

The cache dir is redirected at ``UMU_CACHE_DIR`` via ``monkeypatch`` so a
real ``~/.local/share/umu`` is never touched.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.launcher.proton.infrastructure import umu_runtime as ur


def _make_variant(cache: Path, name: str, *, entry_point: str) -> Path:
    """Create a fake runtime variant dir under ``cache``.

    ``entry_point`` selects the ``umu`` state:
      * ``"ok"``      — real ``_v2-entry-point`` file + ``umu`` symlink → it;
      * ``"missing"`` — payload only, no ``umu`` link at all;
      * ``"dangling"``— ``umu`` symlink whose target does not exist.
    """
    variant = cache / name
    # Payload that umu's own "is up to date" check is satisfied by.
    (variant / "pressure-vessel").mkdir(parents=True)
    (variant / "VERSIONS.txt").write_text("depot\t4.0\n", encoding="utf-8")
    (variant / f"{name}_platform_4.0").mkdir()
    if entry_point == "ok":
        (variant / "_v2-entry-point").write_text("#!/bin/sh\n", encoding="utf-8")
        (variant / "umu").symlink_to("_v2-entry-point")
    elif entry_point == "dangling":
        # Symlink present, target absent — umu's entry_point.is_file() is False.
        (variant / "umu").symlink_to("_v2-entry-point")
    # "missing": no umu link at all.
    return variant


@pytest.fixture()
def cache(tmp_path, monkeypatch):
    """A throwaway umu cache dir wired in via ``UMU_CACHE_DIR``."""
    root = tmp_path / "umu"
    root.mkdir()
    monkeypatch.setattr(ur, "UMU_CACHE_DIR", root)
    return root


def test_healthy_variant_is_kept(cache):
    """A variant with a resolvable umu entry point is not removed."""
    variant = _make_variant(cache, "steamrt4", entry_point="ok")

    ur.repair_incomplete_umu_runtime()

    assert variant.is_dir()


def test_missing_entry_point_variant_is_removed(cache):
    """Payload present but no ``umu`` link → the variant dir is wiped."""
    variant = _make_variant(cache, "steamrt4", entry_point="missing")

    ur.repair_incomplete_umu_runtime()

    assert not variant.exists()


def test_dangling_entry_point_variant_is_removed(cache):
    """A dangling ``umu`` symlink counts as broken and is wiped."""
    variant = _make_variant(cache, "steamrt4", entry_point="dangling")
    # Sanity: this is exactly the state umu's own build_command rejects.
    assert not (variant / "umu").is_file()

    ur.repair_incomplete_umu_runtime()

    assert not variant.exists()


def test_only_broken_variant_is_removed(cache):
    """Surgical: a healthy sibling survives while the broken one is wiped."""
    healthy = _make_variant(cache, "steamrt3", entry_point="ok")
    broken = _make_variant(cache, "steamrt4", entry_point="missing")

    ur.repair_incomplete_umu_runtime()

    assert healthy.is_dir()
    assert not broken.exists()


def test_empty_cache_is_a_noop(cache):
    """No variant dirs at all → no error, nothing removed."""
    ur.repair_incomplete_umu_runtime()

    # Only the (empty) cache root remains.
    assert list(cache.iterdir()) == []


def test_absent_cache_is_a_noop(tmp_path, monkeypatch):
    """A cache dir that does not exist yet must not raise."""
    monkeypatch.setattr(ur, "UMU_CACHE_DIR", tmp_path / "does-not-exist")

    ur.repair_incomplete_umu_runtime()  # must not raise


def test_entry_point_ok_helper(cache):
    """``_runtime_entry_point_ok`` mirrors umu's ``is_file`` gate exactly."""
    ok = _make_variant(cache, "steamrt4", entry_point="ok")
    missing = _make_variant(cache, "steamrt3", entry_point="missing")
    dangling = _make_variant(cache, "steamrt2", entry_point="dangling")

    assert ur._runtime_entry_point_ok(ok) is True
    assert ur._runtime_entry_point_ok(missing) is False
    assert ur._runtime_entry_point_ok(dangling) is False
