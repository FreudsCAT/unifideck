"""Unit tests for the Rockstar-on-Epic (RDR2/GTA5) launch flow.

UD-022 (Epic half): RDR2/GTA5 from Epic boot the Rockstar Games
Launcher, which needs a bundle of Epic-launcher handling (STORE=egs,
WINEDLLOVERRIDES, fake EpicGamesLauncher.exe, com.epicgames.launcher
registration) that would REGRESS ordinary Epic titles if applied
globally. Everything is gated on ``is_rockstar_egs`` — these tests pin
the gate and, critically, that a NON-Rockstar Epic launch is unaffected.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

from unifideck.launcher.proton.compat import epic_cleanup, rockstar_egs
from unifideck.launcher.proton.fixes import game_fixes

# ── identity gate ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "game_id", ["Heather", "9d2d0eb64d5c44529cece33fe2a46482"],
)
def test_is_rockstar_egs_true_by_epic_app_name(game_id):
    """The PRIMARY key is the Epic app name — this is what the real Decky
    build has at launch. Regression: the first cut gated on umu_id, which
    is always None on the build (umu_lookup.py isn't bundled), so the flow
    never fired for RDR2. umu_id defaults to None here on purpose.
    """
    assert game_fixes.is_rockstar_egs(game_id) is True


@pytest.mark.parametrize("umu_id", ["umu-1174180", "umu-271590"])
def test_is_rockstar_egs_true_by_umu_id_secondary(umu_id):
    """umu id still matches as a secondary signal (if the lookup ran)."""
    assert game_fixes.is_rockstar_egs(None, umu_id) is True


@pytest.mark.parametrize("game_id", [None, "", "SomeOtherGame", "Fortnite"])
def test_is_rockstar_egs_false_for_ordinary_epic(game_id):
    """A normal Epic app name (and no umu id) → ordinary path unchanged."""
    assert game_fixes.is_rockstar_egs(game_id) is False
    assert game_fixes.is_rockstar_egs(game_id, None) is False


# ── epic_cleanup skip gating ───────────────────────────────────────

def _cleanup_plan(tmp_path, game_id, umu_id=None):
    # umu_id defaults to None — the real Decky build never resolves one.
    return SimpleNamespace(
        context=SimpleNamespace(game_id=game_id),
        prefix_path=tmp_path / "prefix",
        state=SimpleNamespace(umu_id=umu_id),
    )


def test_cleanup_skipped_for_rockstar(tmp_path, monkeypatch):
    """Rockstar games must NOT have the launcher stub / registry stripped.

    Uses the Epic app name with umu_id=None — the exact runtime shape the
    tester reported (``store=epic umu_id=None``).
    """
    stub_calls: list = []
    reg_calls: list = []
    monkeypatch.setattr(
        epic_cleanup, "_remove_epic_launcher_stubs", stub_calls.append,
    )
    monkeypatch.setattr(
        epic_cleanup, "_clean_epic_registry", reg_calls.append,
    )

    epic_cleanup.cleanup_epic_artifacts(_cleanup_plan(tmp_path, "Heather"))

    assert stub_calls == []
    assert reg_calls == []


def test_cleanup_runs_for_ordinary_epic(tmp_path, monkeypatch):
    """Regression guard: a non-Rockstar Epic game still gets full cleanup."""
    (tmp_path / "prefix").mkdir()
    stub_calls: list = []
    reg_calls: list = []
    monkeypatch.setattr(
        epic_cleanup, "resolve_drive_c", lambda p: tmp_path / "dc",
    )
    monkeypatch.setattr(
        epic_cleanup, "resolve_registry_prefix", lambda p: tmp_path,
    )
    monkeypatch.setattr(
        epic_cleanup, "_remove_epic_launcher_stubs", stub_calls.append,
    )
    monkeypatch.setattr(
        epic_cleanup, "_clean_epic_registry", reg_calls.append,
    )

    epic_cleanup.cleanup_epic_artifacts(_cleanup_plan(tmp_path, "SomeGame"))

    assert stub_calls  # stub-removal ran
    assert reg_calls  # registry cleanup ran (user.reg + system.reg)


# ── rockstar_egs setup: fake launcher copy + protocol registration ─

def _setup_plan(tmp_path, game_id, *, install_dir, plugin_dir):
    # umu_id=None mirrors the real Decky build (no umu_lookup.py).
    return SimpleNamespace(
        context=SimpleNamespace(
            game_id=game_id,
            game_key="Red Dead Redemption 2",
            plugin_dir=plugin_dir,
            work_dir=install_dir,
            exe_path=install_dir / "game.exe",
        ),
        prefix_path=tmp_path / "prefix",
        state=SimpleNamespace(umu_id=None),
    )


def test_setup_noop_for_ordinary_epic(tmp_path, monkeypatch):
    """A non-Rockstar game must not get a fake launcher copied in."""
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")

    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(tmp_path, "SomeGame", install_dir=install, plugin_dir=plugin),
    )

    assert not (install / "EpicGamesLauncher.exe").exists()


def test_setup_copies_fake_launcher_for_rockstar(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")

    # "Heather" (RDR2 Epic app name) with umu_id=None — the real build shape.
    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(tmp_path, "Heather", install_dir=install, plugin_dir=plugin),
    )

    dest = install / "EpicGamesLauncher.exe"
    assert dest.is_file()
    assert dest.read_bytes() == b"MZ-fake"


def test_setup_registers_protocol_when_user_reg_present(tmp_path):
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")
    # A ready prefix: drive_c present + user.reg present.
    prefix = tmp_path / "prefix"
    drive_c = prefix / "pfx" / "drive_c"
    drive_c.mkdir(parents=True)
    user_reg = prefix / "pfx" / "user.reg"
    user_reg.write_text("WINE REGISTRY Version 2\n", encoding="utf-8")

    plan = _setup_plan(tmp_path, "Heather", install_dir=install, plugin_dir=plugin)
    rockstar_egs.apply_rockstar_egs_setup(plan)

    text = user_reg.read_text(encoding="utf-8")
    assert "com.epicgames.launcher" in text
    # Count the registry SECTION HEADER, not the bare substring (the block
    # also mentions the protocol in its value), so this measures blocks.
    header = "[Software\\\\Classes\\\\com.epicgames.launcher]"
    assert text.count(header) == 1
    # Idempotent — a second run must not append a duplicate block.
    rockstar_egs.apply_rockstar_egs_setup(plan)
    assert user_reg.read_text(encoding="utf-8").count(header) == 1


def test_setup_survives_missing_bundled_launcher(tmp_path):
    """No bundled stub → best-effort no-op, never raises."""
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)  # no EpicGamesLauncher.exe

    # Must not raise.
    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(tmp_path, "Heather", install_dir=install, plugin_dir=plugin),
    )
    assert not (install / "EpicGamesLauncher.exe").exists()
