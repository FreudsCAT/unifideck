"""Regression: Epic prefix hygiene must resolve the pfx/ nested layout.

umu/GE-Proton nest the real Wine registry and drive_c under
``<prefix>/pfx/``, not the prefix root directly (see prefix_layout.py,
already used correctly by prefix_init.py). cleanup_epic_artifacts checked
``prefix / "user.reg"`` / ``prefix / "drive_c"`` directly, so it silently
inspected paths that never exist for any modern prefix and never actually
removed the poisoned com.epicgames.launcher registry key — meaning once a
prefix was poisoned (see the STORE=egs bug in core.py), nothing could ever
self-heal it, and a user deleting + recreating the prefix would just get
re-poisoned on the very next launch's setup steps.
"""
from __future__ import annotations

import types
from pathlib import Path

from unifideck.launcher.proton.compat import epic_cleanup as ec


def _plan(prefix_path: Path):
    # game_id/umu_id → not a Rockstar-EGS game, so full cleanup runs
    # (the behavior these tests exercise).
    return types.SimpleNamespace(
        prefix_path=prefix_path,
        context=types.SimpleNamespace(
            game_id="SomeGame", exe_path=Path("/install/SomeGame.exe"),
        ),
        state=types.SimpleNamespace(umu_id=None),
    )


def _poisoned_reg(path: Path) -> str:
    return (
        "[Software\\\\Classes]\n"
        '"foo"="bar"\n'
        "\n"
        '[Software\\\\Classes\\\\com.epicgames.launcher] 1700000000\n'
        '"OverlayPath"="Z:C:\\\\some\\\\path"\n'
        "\n"
        "[Software\\\\Other]\n"
        '"baz"="qux"\n'
    )


def test_cleanup_removes_registry_key_under_pfx(tmp_path, monkeypatch):
    monkeypatch.delenv("ACTIVE_WINEPREFIX", raising=False)
    root = tmp_path / "prefix"
    pfx = root / "pfx"
    pfx.mkdir(parents=True)
    (pfx / "user.reg").write_text(_poisoned_reg(pfx))
    (pfx / "system.reg").write_text(_poisoned_reg(pfx))

    ec.cleanup_epic_artifacts(_plan(root))

    user_reg = (pfx / "user.reg").read_text()
    assert "com.epicgames.launcher" not in user_reg
    assert '"foo"="bar"' in user_reg
    assert '"baz"="qux"' in user_reg


def test_cleanup_removes_stub_under_pfx(tmp_path, monkeypatch):
    monkeypatch.delenv("ACTIVE_WINEPREFIX", raising=False)
    root = tmp_path / "prefix"
    stub = (
        root / "pfx" / "drive_c" / "windows" / "command"
        / "EpicGamesLauncher.exe"
    )
    stub.parent.mkdir(parents=True)
    stub.write_text("stub")

    ec.cleanup_epic_artifacts(_plan(root))

    assert not stub.exists()


def test_cleanup_still_works_on_legacy_root_layout(tmp_path, monkeypatch):
    """No pfx/ subdir at all — resolve_registry_prefix/resolve_drive_c
    fall back to the prefix root, matching pre-umu layouts."""
    monkeypatch.delenv("ACTIVE_WINEPREFIX", raising=False)
    root = tmp_path / "prefix"
    root.mkdir()
    (root / "user.reg").write_text(_poisoned_reg(root))

    ec.cleanup_epic_artifacts(_plan(root))

    assert "com.epicgames.launcher" not in (root / "user.reg").read_text()
