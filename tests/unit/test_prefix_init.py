"""Unit tests for compat/prefix_init — proton-change reset + first init.

Covers the deterministic logic: Proton family classification, the
reset-vs-notify decision on a Proton change, the destructive reset
(wipe-but-preserve + user-data backup), and the marker bookkeeping.
The umu ``createprefix`` subprocess path is exercised only for the
fast "already initialised → skip" case (no subprocess).
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from unifideck.launcher.proton.compat import prefix_init as pi


@pytest.fixture
def toast_spy(monkeypatch):
    """Capture launcher_toast(i18n_key, **kw) calls."""
    spy = MagicMock()
    monkeypatch.setattr(pi, "launcher_toast", spy)
    return spy


def _plan(prefix_root: Path, tool: str):
    """Minimal ProtonLaunchPlan stand-in for the pure-logic helpers."""
    return SimpleNamespace(
        prefix_path=prefix_root,
        state=SimpleNamespace(proton_tool_id=tool),
        context=SimpleNamespace(game_key="gog:123"),
    )


def _make_root_prefix(root: Path, *, proton_marker: str | None = None) -> None:
    """Build a root-layout Wine prefix with a save + setup markers."""
    (root / "drive_c" / "users" / "steamuser").mkdir(parents=True)
    (root / "drive_c" / "users" / "steamuser" / "save.dat").write_text("savegame")
    (root / "system.reg").write_text("reg")
    (root / "user.reg").write_text("reg")
    (root / "version").write_text("GE-Proton10-10")
    (root / "unifideck_winetricks_complete.marker").write_text("complete")
    (root / ".unifideck_prereqs_x.done").write_text("done")
    if proton_marker is not None:
        (root / pi._MARKER_NAME).write_text(proton_marker)


# ── _proton_family ────────────────────────────────────────────────

@pytest.mark.parametrize(
    ("tool", "family"),
    [
        ("proton_experimental", "experimental"),
        ("GE-Proton10-34", "ge-proton"),
        ("GE-Proton9-26", "ge-proton"),
        ("UMU-Proton-9.0-4e", "umu-proton"),
        ("Proton 9.0 (Beta)", "proton9"),
        ("Proton 10.0", "proton10"),
        ("something-weird", "other"),
    ],
)
def test_proton_family(tool, family):
    assert pi._proton_family(tool) == family


# ── _handle_proton_change ─────────────────────────────────────────

def test_first_launch_records_marker_no_toast_no_reset(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker=None)  # no marker → fresh baseline

    pi._handle_proton_change(_plan(root, "GE-Proton10-34"), root, "GE-Proton10-34")

    toast_spy.assert_not_called()
    assert (root / pi._MARKER_NAME).read_text() == "GE-Proton10-34"
    # Prefix untouched.
    assert (root / "system.reg").is_file()
    assert (root / "unifideck_winetricks_complete.marker").is_file()


def test_minor_change_notifies_but_keeps_prefix(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker="GE-Proton10-10")

    pi._handle_proton_change(_plan(root, "GE-Proton10-34"), root, "GE-Proton10-34")

    key = toast_spy.call_args.args[0]
    assert key == "toasts.launcher.protonSwitchedTo"
    # Same family → prefix + setup markers preserved.
    assert (root / "system.reg").is_file()
    assert (root / "unifideck_winetricks_complete.marker").is_file()
    assert (root / pi._MARKER_NAME).read_text() == "GE-Proton10-34"


def test_major_change_resets_prefix_and_backs_up(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker="proton_experimental")

    pi._handle_proton_change(_plan(root, "GE-Proton10-34"), root, "GE-Proton10-34")

    assert toast_spy.call_args.args[0] == "toasts.launcher.resettingPrefix"
    # Wine state + setup markers wiped...
    assert not (root / "system.reg").exists()
    assert not (root / "drive_c").exists()
    assert not (root / "unifideck_winetricks_complete.marker").exists()
    assert not (root / ".unifideck_prereqs_x.done").exists()
    # ...the proton marker is updated and the save is backed up.
    assert (root / pi._MARKER_NAME).read_text() == "GE-Proton10-34"
    backup = root / ".save_backup" / "steamuser" / "save.dat"
    assert backup.is_file()
    assert backup.read_text() == "savegame"


def test_reset_preserves_marker_and_backup_dirs(tmp_path):
    root = tmp_path / "prefix"
    _make_root_prefix(root, proton_marker="proton_experimental")
    (root / ".save_backup").mkdir()
    (root / ".save_backup" / "old").write_text("x")

    pi._reset_prefix(root)

    # A pre-existing backup is refreshed (old content gone) but the
    # backup + proton marker dirs/files themselves are never deleted.
    assert (root / pi._MARKER_NAME).is_file()
    assert (root / ".save_backup").is_dir()


# ── _ensure_created (fast path) ───────────────────────────────────

async def test_ensure_created_skips_when_system_reg_present(tmp_path, toast_spy):
    root = tmp_path / "prefix"
    root.mkdir()
    (root / "system.reg").write_text("reg")

    # Should return immediately without toasting or spawning umu.
    await pi._ensure_created(_plan(root, "GE-Proton10-34"), root)
    toast_spy.assert_not_called()
