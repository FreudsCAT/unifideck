"""Tests for services/download/validators.py::validate_path.

Regression + new coverage for the size-aware free-space preflight: a
game larger than the target volume must be refused *up front* with an
actionable ``insufficient_space:need=…,free=…`` code instead of sailing
past the old static 1 GB floor and failing deep inside the store CLI
(the Hell Let Loose / Hogwarts Legacy "fails immediately, no error"
class of bug).

All filesystem gates are monkeypatched so the tests are deterministic
and warning-free (the strict suite treats any skip/warning as failure).
"""
from __future__ import annotations

import os
from typing import NamedTuple

import pytest

from unifideck.services.download import validators

_GB = 1024**3


class _FakeStatvfs(NamedTuple):
    """Minimal ``statvfs_result`` stand-in — only the two fields used."""

    f_bavail: int
    f_frsize: int


def _pass_write_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the empty/mkdir/writable gates pass so tests hit statvfs."""
    monkeypatch.setattr(validators.Path, "mkdir", lambda self, **k: None)
    monkeypatch.setattr(os, "access", lambda path, mode: True)


def _fake_free(monkeypatch: pytest.MonkeyPatch, free_bytes: int) -> None:
    """Patch statvfs so the volume reports ``free_bytes`` available."""
    monkeypatch.setattr(
        os, "statvfs", lambda path: _FakeStatvfs(f_bavail=free_bytes, f_frsize=1),
    )


def test_no_required_bytes_ample_free_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Regression guard: old behaviour (no size) with room → success."""
    _pass_write_gates(monkeypatch)
    _fake_free(monkeypatch, 50 * _GB)

    result = validators.validate_path("/games")

    assert result.success


def test_no_required_bytes_below_floor_low_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unknown size + under the 1 GB floor → the existing low_space code."""
    _pass_write_gates(monkeypatch)
    _fake_free(monkeypatch, 500 * 1024 * 1024)  # 0.5 GB

    result = validators.validate_path("/games")

    assert not result.success
    assert result.error is not None
    assert result.error.startswith("low_space:")


def test_free_equals_need_fits(monkeypatch: pytest.MonkeyPatch) -> None:
    """Boundary: free == need fits (comparison is ``<``, not ``<=``)."""
    _pass_write_gates(monkeypatch)
    need = 66 * _GB
    _fake_free(monkeypatch, need)

    result = validators.validate_path("/games", required_bytes=need)

    assert result.success


def test_free_below_need_insufficient_space(monkeypatch: pytest.MonkeyPatch) -> None:
    """free < need → insufficient_space with both figures, 1 decimal."""
    _pass_write_gates(monkeypatch)
    need = 70 * _GB
    free = 40 * _GB
    _fake_free(monkeypatch, free)

    result = validators.validate_path("/games", required_bytes=need)

    assert not result.success
    assert result.error is not None
    assert result.error.startswith("insufficient_space:")
    # Both numbers present, formatted to one decimal (exact-GB inputs
    # sidestep float-rounding ambiguity in the assertion).
    assert "need=70.0GB" in result.error
    assert "free=40.0GB" in result.error


def test_free_above_need_passes(monkeypatch: pytest.MonkeyPatch) -> None:
    """Plenty of room for the known size → success."""
    _pass_write_gates(monkeypatch)
    _fake_free(monkeypatch, 200 * _GB)

    result = validators.validate_path("/games", required_bytes=66 * _GB)

    assert result.success


def test_unknown_size_never_emits_insufficient_space(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """required_bytes=None degrades to the floor; never insufficient_space."""
    _pass_write_gates(monkeypatch)
    _fake_free(monkeypatch, 500 * 1024 * 1024)  # 0.5 GB

    result = validators.validate_path("/games", required_bytes=None)

    assert not result.success
    assert result.error is not None
    assert result.error.startswith("low_space:")
    assert "insufficient_space" not in result.error


def test_statvfs_failure_is_best_effort(monkeypatch: pytest.MonkeyPatch) -> None:
    """statvfs raising → we don't block, even with a huge required size."""
    _pass_write_gates(monkeypatch)

    def _boom(_path: str) -> _FakeStatvfs:
        raise OSError("statvfs unsupported on this FUSE mount")

    monkeypatch.setattr(os, "statvfs", _boom)

    result = validators.validate_path("/games", required_bytes=999 * _GB)

    assert result.success


def test_empty_path_rejected() -> None:
    """An empty path is rejected before any statvfs work."""
    result = validators.validate_path("", required_bytes=1)

    assert not result.success
    assert result.error == "empty_path"
