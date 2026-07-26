"""Unit tests for the Rockstar-on-Epic (RDR2/GTA5) launch flow.

UD-022 (Epic half): RDR2/GTA5 from Epic boot the Rockstar Games
Launcher, which needs a bundle of Epic-launcher handling (STORE=egs,
WINEDLLOVERRIDES, fake EpicGamesLauncher.exe, com.epicgames.launcher
registration) that would REGRESS ordinary Epic titles if applied
globally. Everything is gated on ``is_rockstar_egs`` — these tests pin
the gate and, critically, that a NON-Rockstar Epic launch is unaffected.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from unifideck.launcher.proton.compat import epic_cleanup, rockstar_egs
from unifideck.launcher.proton.fixes import game_fixes
from unifideck.launcher.proton.handlers.epic import _rockstar_play_exe_rel

# ── identity gate ──────────────────────────────────────────────────

@pytest.mark.parametrize(
    "game_id",
    [
        "Heather",
        "9d2d0eb64d5c44529cece33fe2a46482",
        "8769e24080ea413b8ebca3f1b8c50951",
    ],
)
def test_is_rockstar_egs_true_by_epic_app_name(game_id):
    """The PRIMARY key is the Epic app name — this is what the real Decky
    build has at launch. Regression: the first cut gated on umu_id, which
    is always None on the build (umu_lookup.py isn't bundled), so the flow
    never fired for RDR2. umu_id defaults to None here on purpose. The
    third id here is GTA V's "Enhanced Edition" — a separate Epic catalog
    listing from the legacy "Grand Theft Auto V" id above (UD report: the
    Enhanced Edition's id was missing from this allowlist entirely).
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


@pytest.mark.parametrize("exe_name", ["PlayGTAV.exe", "PLAYGTAV.EXE", "playgtav.exe"])
def test_is_rockstar_egs_true_by_exe_name_unknown_app_id(exe_name):
    """The most durable signal: the Rockstar Play-launcher exe name matches
    even when the Epic app id is a brand new one this allowlist has never
    seen — exactly what broke GTA V's Enhanced Edition (a new Epic catalog
    id for a title the app-name allowlist already covered under its legacy
    id). Case-insensitive since Wine/NTFS paths aren't case sensitive here.
    """
    assert game_fixes.is_rockstar_egs("some-brand-new-epic-id", None, exe_name) is True


def test_is_rockstar_egs_false_when_exe_name_unrelated():
    """An ordinary Epic game's exe must not accidentally match."""
    assert game_fixes.is_rockstar_egs("Fortnite", None, "FortniteClient-Win64-Shipping.exe") is False


# ── install-dir tier: generalises to Rockstar titles with no table entry ──

def test_install_dir_tier_matches_play_exe_plus_launcher(tmp_path):
    """A Rockstar title we hold no app id or exe name for (e.g. a Definitive
    Edition / GTA IV / RDR1 listing) is still recognised from the
    Play<Title>.exe + Launcher.exe pair its build ships.
    """
    (tmp_path / "PlayGTA3.exe").write_bytes(b"MZ")
    (tmp_path / "Launcher.exe").write_bytes(b"MZ")

    assert game_fixes.is_rockstar_egs("unknown-id", None, "GTA3.exe", tmp_path) is True
    assert game_fixes.rockstar_play_exe_in_dir(tmp_path) == "PlayGTA3.exe"


def test_install_dir_tier_requires_launcher_sibling(tmp_path):
    """A lone Play*.exe is NOT enough. A false positive would flip an
    ordinary Epic title to STORE=egs, which hangs EOS games — so this tier
    deliberately demands both signals.
    """
    (tmp_path / "PlayAmbiguous.exe").write_bytes(b"MZ")

    assert game_fixes.rockstar_play_exe_in_dir(tmp_path) is None
    assert game_fixes.is_rockstar_egs("unknown-id", None, "x.exe", tmp_path) is False


def test_install_dir_tier_ignores_launcher_without_play_exe(tmp_path):
    """Plenty of non-Rockstar games ship a generic Launcher.exe — that alone
    must never trigger the Rockstar flow."""
    (tmp_path / "Launcher.exe").write_bytes(b"MZ")

    assert game_fixes.rockstar_play_exe_in_dir(tmp_path) is None
    assert game_fixes.is_rockstar_egs("unknown-id", None, "x.exe", tmp_path) is False


def test_install_dir_tier_survives_missing_dir(tmp_path):
    """Nonexistent / None install dir → no match, no raise."""
    assert game_fixes.rockstar_play_exe_in_dir(tmp_path / "nope") is None
    assert game_fixes.rockstar_play_exe_in_dir(None) is None


# ── epic_cleanup skip gating ───────────────────────────────────────

def _cleanup_plan(tmp_path, game_id, umu_id=None, exe_name="game.exe"):
    # umu_id defaults to None — the real Decky build never resolves one.
    # exe_name defaults to an unrelated name — only the parametrized
    # exe-name test below needs a Rockstar Play-launcher name here.
    return SimpleNamespace(
        context=SimpleNamespace(
            game_id=game_id, exe_path=Path("/install") / exe_name,
        ),
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


def test_cleanup_skipped_for_rockstar_by_exe_name_alone(tmp_path, monkeypatch):
    """A Rockstar title whose Epic app id is NOT in the allowlist must still
    be recognised from its Play-launcher exe, and so must still be exempt
    from the launcher-stub/registry hygiene that ordinary Epic games get.

    This is the GTA V "Enhanced Edition" shape (a new Epic catalog id for a
    title the allowlist only covered under its legacy id). Without the
    exe-name tier ``is_rockstar_egs`` returns False here, and cleanup would
    be free to strip the com.epicgames.launcher registration + stub that the
    Rockstar Games Launcher depends on.
    """
    stub_calls: list = []
    reg_calls: list = []
    monkeypatch.setattr(
        epic_cleanup, "_remove_epic_launcher_stubs", stub_calls.append,
    )
    monkeypatch.setattr(
        epic_cleanup, "_clean_epic_registry", reg_calls.append,
    )

    plan = _cleanup_plan(
        tmp_path,
        "8769e24080ea413b8ebca3f1b8c50951",
        exe_name="PlayGTAV.exe",
    )
    epic_cleanup.cleanup_epic_artifacts(plan)

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


def test_setup_writes_launch_shim_matching_proven_fix(tmp_path):
    """The generated shim must match the reporter's hand-written fix.bat
    byte-for-byte (that exact file was verified working on-device):

        start "" EpicGamesLauncher.exe PlayGTAV.exe %*

    ``start ""`` supplies the empty title argument cmd needs, and ``%*``
    forwards game args. GTA V Enhanced's app id is used here since that's
    the reported title.
    """
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")

    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(
            tmp_path, "8769e24080ea413b8ebca3f1b8c50951",
            install_dir=install, plugin_dir=plugin,
        ),
    )

    shim = install / rockstar_egs.LAUNCH_SHIM_NAME
    assert shim.is_file()
    assert shim.read_text() == 'start "" EpicGamesLauncher.exe PlayGTAV.exe %*\n'


def test_setup_shim_names_rdr2_play_exe(tmp_path):
    """The shim must name the *right* Play exe per title, not a fixed one."""
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")

    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(tmp_path, "Heather", install_dir=install, plugin_dir=plugin),
    )

    shim = install / rockstar_egs.LAUNCH_SHIM_NAME
    assert shim.read_text() == 'start "" EpicGamesLauncher.exe PlayRDR2.exe %*\n'


def test_setup_shim_rewritten_when_stale(tmp_path):
    """A shim from an older template must be corrected, not skipped forever."""
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")
    stale = install / rockstar_egs.LAUNCH_SHIM_NAME
    stale.write_text("echo an older broken template\n")

    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(tmp_path, "Heather", install_dir=install, plugin_dir=plugin),
    )

    assert stale.read_text() == 'start "" EpicGamesLauncher.exe PlayRDR2.exe %*\n'


def test_setup_does_not_touch_user_hand_written_fix_bat(tmp_path):
    """Testers following the manual workaround hand-write their own fix.bat
    in this same directory — we must never clobber it."""
    install = tmp_path / "install"
    install.mkdir()
    plugin = tmp_path / "plugin"
    (plugin / "bin").mkdir(parents=True)
    (plugin / "bin" / "EpicGamesLauncher.exe").write_bytes(b"MZ-fake")
    theirs = install / "fix.bat"
    theirs.write_text('start "" EpicGamesLauncher.exe PlayGTAV.exe %*\n')

    rockstar_egs.apply_rockstar_egs_setup(
        _setup_plan(tmp_path, "Heather", install_dir=install, plugin_dir=plugin),
    )

    assert theirs.read_text() == 'start "" EpicGamesLauncher.exe PlayGTAV.exe %*\n'
    assert rockstar_egs.LAUNCH_SHIM_NAME != "fix.bat"


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


# ── --override-exe resolution across the three match tiers ─────────

def _exe_plan(game_id, exe_name, umu_id=None, work_dir=None):
    # work_dir=None → no shim on disk, so these assert the raw Play-exe
    # fallback; the shim-preference cases pass a real dir.
    return SimpleNamespace(
        context=SimpleNamespace(
            game_id=game_id,
            exe_path=Path("/install") / exe_name,
            work_dir=work_dir,
        ),
        state=SimpleNamespace(umu_id=umu_id),
    )


def test_play_exe_rel_none_for_ordinary_epic():
    """Non-Rockstar → no override, legendary's own executable is used."""
    assert _rockstar_play_exe_rel(_exe_plan("Fortnite", "Fortnite.exe")) is None


def test_play_exe_rel_from_table_for_known_app_name():
    """Known app name → the curated table wins, even if the games.map exe
    is the Epic-launcher stub rather than the Play launcher."""
    plan = _exe_plan("Heather", "EpicGamesLauncher.exe")
    assert _rockstar_play_exe_rel(plan) == "PlayRDR2.exe"


def test_play_exe_rel_falls_back_to_exe_name_for_unknown_app_id():
    """Recognised only by exe name (a re-release the tables don't know):
    the table lookups both miss, so the exe we were handed — which IS the
    Play launcher, since that's what matched — must be used. Without this
    the exe-name tier would half-work: detection fires but --override-exe
    silently resolves to None.
    """
    plan = _exe_plan("some-future-epic-catalog-id", "PlayGTAV.exe")
    assert _rockstar_play_exe_rel(plan) == "PlayGTAV.exe"


def test_play_exe_rel_prefers_the_shim_when_present(tmp_path):
    """THE regression this whole change exists for: once the shim is on
    disk it must win, so the game launches *through* the fake Epic
    launcher. Returning the bare Play exe here is the reported bug
    (Rockstar launcher finds the game once, won't start it, then loses it).
    """
    (tmp_path / rockstar_egs.LAUNCH_SHIM_NAME).write_text("start ...")
    plan = _exe_plan("Heather", "PlayRDR2.exe", work_dir=tmp_path)

    assert _rockstar_play_exe_rel(plan) == rockstar_egs.LAUNCH_SHIM_NAME
