"""Cross-distro portability guards (Bazzite / CachyOS vs SteamOS).

These tests lock in the SteamOS-assumption fixes so they don't regress:

* ``find_steam_path`` must resolve under any ``$HOME`` (not just
  ``/home/deck``) — Bazzite/CachyOS users pick their own username.
* the Ubisoft SD-card default must resolve the real mounted removable
  media instead of the Deck's ``/run/media/mmcblk0p1`` device node, and
  fall back harmlessly when nothing is mounted.
* the Proton compat/library scan roots must not depend solely on the
  ``~/.steam/root`` symlink — ``~/.steam/steam`` is listed too.
* the launcher's cffi-backend probe (which decides graceful cloud-save
  degradation) must return a bool without raising.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from unifideck.steam.library import find_steam_path
from unifideck.stores.ubisoft.config import _detect_sdcard_install_base


def test_find_steam_path_resolves_under_non_deck_home(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """A non-'deck' username must still locate native Steam."""
    home = tmp_path / "bazzite-user"
    steam = home / ".steam" / "steam"
    (steam / "steamapps").mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))

    assert find_steam_path(None) == str(steam)


def test_find_steam_path_none_when_absent(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """No Steam install anywhere under HOME → None (not a crash/hardcode)."""
    monkeypatch.setenv("HOME", str(tmp_path / "empty-home"))
    assert find_steam_path(None) is None


def test_sdcard_base_falls_back_without_mounts(tmp_path: Path) -> None:
    """No removable media mounted → harmless historical Deck fallback.

    The point is that it never *requires* the Deck device to exist; the
    fallback string is inert on other distros (the path just won't exist).
    """
    empty = tmp_path / "run-media-empty"  # does not exist
    assert _detect_sdcard_install_base(empty) == (
        "/run/media/mmcblk0p1/Games/Ubisoft"
    )


def test_sdcard_base_detects_flat_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """SteamOS-style flat layout: /run/media/<label> is the mountpoint."""
    media = tmp_path / "run-media"
    label = media / "MYSDCARD"
    label.mkdir(parents=True)

    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) == str(label))
    monkeypatch.setattr(os, "access", lambda p, mode: True)

    assert _detect_sdcard_install_base(media) == str(
        label / "Games" / "Ubisoft",
    )


def test_sdcard_base_detects_nested_udisks_mount(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """udisks2 layout: /run/media/<user>/<label> is the mountpoint."""
    media = tmp_path / "run-media"
    label = media / "bazzite" / "MYSDCARD"
    label.mkdir(parents=True)

    # Only the deepest <label> dir is a real mount; the <user> dir is not.
    monkeypatch.setattr(os.path, "ismount", lambda p: str(p) == str(label))
    monkeypatch.setattr(os, "access", lambda p, mode: True)

    assert _detect_sdcard_install_base(media) == str(
        label / "Games" / "Ubisoft",
    )


def test_proton_roots_include_steam_steam_dir() -> None:
    """Compat/library resolution must not depend solely on ~/.steam/root."""
    from unifideck.launcher.proton.infrastructure import selector

    assert "~/.steam/steam/compatibilitytools.d" in selector.STEAM_COMPAT_ROOTS
    assert "~/.steam/steam/steamapps/common" in selector.STEAM_LIBRARY_ROOTS


def test_ge_installer_scan_roots_include_steam_steam_dir() -> None:
    from unifideck.launcher.proton.infrastructure import ge_installer

    assert (
        "~/.steam/steam/compatibilitytools.d" in ge_installer._SCAN_ROOTS
    )


def test_cffi_backend_probe_returns_bool() -> None:
    """The graceful-degradation probe must never raise."""
    from unifideck.services.launcher.helpers import _cffi_backend_available

    assert isinstance(_cffi_backend_available(), bool)
