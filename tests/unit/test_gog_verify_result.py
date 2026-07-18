"""Tests for GOG install post-download verification outcomes.

Covers ``_build_verify_result`` — the pure helper behind
``GOGInstallPlanner.verify_installation`` that decides whether an
install is complete or needs a repair pass.

Regression focus (UD-042): ``goggame-<id>.info`` is a Windows/Galaxy-
only artifact and is *never* present in GOG's Linux-native builds, so
its absence must NOT be treated as an integrity failure on
``platform == "linux"``. Before the fix, every Linux-native install
came back ``complete=False`` ("Missing goggame.info file"), which
triggered a gogdl repair pass that reliably wedged for ~1h behind the
finalize watchdog. The size-ratio and executable checks must still
guard Linux installs.
"""

from __future__ import annotations

from typing import Any

import pytest

from unifideck.stores.gog.install.planner import (
    _MIN_SIZE_RATIO,
    _build_verify_result,
)

# A size that comfortably clears the ``_MIN_SIZE_RATIO`` gate.
_FULL = 2_000_000_000
_EXPECTED = 2_000_000_000


def _build(**overrides: Any) -> dict[str, Any]:
    """Call ``_build_verify_result`` with sensible full-install defaults."""
    kwargs: dict[str, Any] = {
        "actual": _FULL,
        "expected": _EXPECTED,
        "files": 8651,
        "has_info": True,
        "has_exe": True,
        "size_ratio": 1.0,
        "platform": "linux",
    }
    kwargs.update(overrides)
    return _build_verify_result(**kwargs)


def test_linux_missing_info_is_complete() -> None:
    """UD-042: Linux-native install with no goggame.info is complete."""
    result = _build(platform="linux", has_info=False, has_exe=True)
    assert result["complete"] is True
    # Reported honestly even though we don't treat it as a failure.
    assert result["has_info"] is False


def test_windows_missing_info_is_incomplete() -> None:
    """Windows still flags a missing goggame.info as incomplete."""
    result = _build(platform="windows", has_info=False, has_exe=True)
    assert result["complete"] is False
    assert result["issue"] == "Missing goggame.info file"


def test_linux_missing_exe_still_flagged() -> None:
    """The executable check still guards Linux installs."""
    result = _build(platform="linux", has_info=False, has_exe=False)
    assert result["complete"] is False
    assert result["issue"] == "Could not find game executable"


def test_linux_short_size_still_flagged() -> None:
    """The size-ratio check still guards Linux (and wins over info)."""
    short = int(_EXPECTED * (_MIN_SIZE_RATIO / 2))
    result = _build(
        platform="linux",
        actual=short,
        expected=_EXPECTED,
        size_ratio=short / _EXPECTED,
        has_info=False,
    )
    assert result["complete"] is False
    assert "incomplete" in result["issue"].lower()


def test_windows_happy_path_is_complete() -> None:
    """Windows install with info + exe + full size verifies cleanly."""
    result = _build(platform="windows", has_info=True, has_exe=True)
    assert result["complete"] is True


@pytest.mark.parametrize("platform", ["linux", "windows"])
def test_full_install_with_info_and_exe_completes(platform: str) -> None:
    """A complete install verifies on either platform."""
    result = _build(platform=platform, has_info=True, has_exe=True)
    assert result["complete"] is True
