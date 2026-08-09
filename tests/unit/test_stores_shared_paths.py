"""Shared store helpers: Wine path conversion and removable-media detection.

Both were extracted from ``stores/ubisoft/`` when Battle.net became the
second consumer. These tests pin the behaviours that matter to *both*
stores, so a future change cannot quietly regress one while fixing the
other.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from unifideck.stores.shared.install_base import (
    detect_media_root,
    detect_sdcard_install_base,
)
from unifideck.stores.shared.wine_path import wine_path_to_linux

# --------------------------------------------------------------------------
# wine path conversion
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    "not_a_wine_path",
    ["", "C", "/usr/share", "relative/path", "\\\\server\\share"],
)
def test_non_wine_paths_are_refused(not_a_wine_path: str) -> None:
    """Refuse rather than mangle — a wrong path breaks install detection."""
    assert wine_path_to_linux(not_a_wine_path, "/prefix") is None


def test_z_drive_is_the_host_root() -> None:
    assert wine_path_to_linux("Z:\\usr\\share", "/prefix") == "/usr/share"
    assert wine_path_to_linux("Z:", "/prefix") == "/"


def test_c_drive_resolves_under_the_prefix(tmp_path: Path) -> None:
    real = tmp_path / "pfx" / "drive_c" / "Games"
    real.mkdir(parents=True)
    assert wine_path_to_linux("C:\\Games", str(tmp_path)) == str(real)


def test_c_drive_probes_the_legacy_layout_too(tmp_path: Path) -> None:
    real = tmp_path / "drive_c" / "Games"
    real.mkdir(parents=True)
    assert wine_path_to_linux("C:\\Games", str(tmp_path)) == str(real)


def test_c_drive_falls_back_when_nothing_exists(tmp_path: Path) -> None:
    """A not-yet-created install path must still be nameable."""
    result = wine_path_to_linux("C:/Program Files (x86)/Hearthstone", str(tmp_path))
    assert result == str(tmp_path / "pfx" / "drive_c" / "Program Files (x86)" / "Hearthstone")


def test_forward_and_back_slashes_both_work(tmp_path: Path) -> None:
    (tmp_path / "drive_c" / "Games").mkdir(parents=True)
    forward = wine_path_to_linux("C:/Games", str(tmp_path))
    backward = wine_path_to_linux("C:\\Games", str(tmp_path))
    assert forward == backward


def test_other_drive_requires_a_real_dosdevices_link(tmp_path: Path) -> None:
    """Never guess: an SD-card game would be reported in the wrong place."""
    assert wine_path_to_linux("D:\\Games", str(tmp_path)) is None


def test_other_drive_follows_the_dosdevices_link(tmp_path: Path) -> None:
    target = tmp_path / "sdcard"
    (target / "Games").mkdir(parents=True)
    dosdevices = tmp_path / "prefix" / "dosdevices"
    dosdevices.mkdir(parents=True)
    (dosdevices / "d:").symlink_to(target)
    assert wine_path_to_linux("D:\\Games", str(tmp_path / "prefix")) == str(target / "Games")


def test_drive_letter_case_is_ignored(tmp_path: Path) -> None:
    (tmp_path / "drive_c").mkdir(parents=True)
    assert wine_path_to_linux("c:\\", str(tmp_path)) == wine_path_to_linux("C:\\", str(tmp_path))


# --------------------------------------------------------------------------
# removable media detection
# --------------------------------------------------------------------------


def test_falls_back_to_the_deck_path_when_nothing_is_mounted(tmp_path: Path) -> None:
    """Harmless: the path simply will not exist, and scans re-check live."""
    assert detect_sdcard_install_base("Ubisoft", tmp_path).endswith(
        "/run/media/mmcblk0p1/Games/Ubisoft"
    ) or detect_sdcard_install_base("Ubisoft", tmp_path) == "/run/media/mmcblk0p1/Games/Ubisoft"


def test_missing_media_base_is_not_an_error(tmp_path: Path) -> None:
    assert detect_media_root(tmp_path / "absent") is None


def test_unmounted_directories_are_ignored(tmp_path: Path) -> None:
    """A plain directory under /run/media is not a mountpoint."""
    (tmp_path / "looks-like-a-card").mkdir()
    assert detect_media_root(tmp_path) is None


def test_symlinks_are_ignored(tmp_path: Path) -> None:
    target = tmp_path / "elsewhere"
    target.mkdir()
    (tmp_path / "link").symlink_to(target)
    assert detect_media_root(tmp_path) is None


def test_store_dir_is_parameterised_not_hardcoded(monkeypatch: pytest.MonkeyPatch) -> None:
    """The whole point of the extraction: two stores, one detector."""
    monkeypatch.setattr(
        "unifideck.stores.shared.install_base.detect_media_root",
        lambda _base=None: Path("/run/media/deck/CARD"),
    )
    assert detect_sdcard_install_base("Ubisoft") == "/run/media/deck/CARD/Games/Ubisoft"
    assert detect_sdcard_install_base("Battlenet") == "/run/media/deck/CARD/Games/Battlenet"


def test_ubisoft_still_delegates_to_the_shared_detector() -> None:
    """Guards against the wrapper drifting away from the shared logic."""
    from unifideck.stores.ubisoft.config import _detect_sdcard_install_base

    assert _detect_sdcard_install_base().endswith("/Games/Ubisoft")
