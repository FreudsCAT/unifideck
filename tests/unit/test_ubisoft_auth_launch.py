"""Regression: the Ubisoft auth shortcut must Proton-launch UPC.

The launcher's auth handler used to no-op for Ubisoft (`handle_store_auth`
returned immediately), so the Steam shortcut opened and closed at once.
Sign-in needs Ubisoft Connect (UPC) actually running in the ``.upc-auth``
prefix so the user can authenticate. These tests pin:

* ``ubisoft_auth_launch`` runs ``[python, umu, upc.exe]`` — bare, no
  ``uplay://`` URL (that form is only for launching a specific game);
* a missing upc.exe raises a clear error rather than closing silently;
* ``LauncherService._handle_auth_path`` routes Ubisoft to the Proton path
  and the OAuth stores to ``handle_store_auth``.
"""
from __future__ import annotations

import types
from pathlib import Path

import pytest

from unifideck.launcher.proton.handlers import ubisoft as h
from unifideck.launcher.proton.handlers import ubisoft_recovery as rec
from unifideck.launcher.proton.infrastructure.core import ProtonLaunchPlan
from unifideck.launcher.types.errors import GameFailedError

_UPC_REL = Path(
    "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/upc.exe",
)


def _plan(prefix: Path) -> ProtonLaunchPlan:
    return ProtonLaunchPlan(
        context=types.SimpleNamespace(game_id="upc-auth", store="ubisoft"),
        state=types.SimpleNamespace(game_exit_code=0),
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        prefix_path=prefix,
        env={"WINEPREFIX": str(prefix)},
        on_process_start=None,
    )


@pytest.fixture()
def _quiet_toast(monkeypatch):
    monkeypatch.setattr(h, "launcher_toast", lambda *a, **k: None)


@pytest.mark.asyncio
async def test_auth_launch_runs_upc_bare(tmp_path, monkeypatch, _quiet_toast):
    captured: dict[str, object] = {}

    async def fake_umu(argv, *, env=None, on_start=None, **kw):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(h, "run_umu_with_retry", fake_umu)
    upc = tmp_path / _UPC_REL
    upc.parent.mkdir(parents=True, exist_ok=True)
    upc.write_text("stub")

    rc = await h.ubisoft_auth_launch(_plan(tmp_path))

    assert rc == 0
    assert captured["argv"] == [
        "/usr/bin/python3",
        "/plugin/bin/umu/umu/umu-run",
        str(upc),
    ]
    assert not any("uplay://" in str(a) for a in captured["argv"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_auth_launch_missing_upc_raises(tmp_path, _quiet_toast):
    with pytest.raises(GameFailedError, match=r"upc\.exe"):
        await h.ubisoft_auth_launch(_plan(tmp_path))


@pytest.mark.asyncio
async def test_handle_auth_path_routes_ubisoft_to_proton(monkeypatch):
    """Ubisoft auth must reach its Proton handler, not the Edge OAuth flow.

    Routing is now shared across wrapper stores, so this asserts the
    resolved handler rather than a Ubisoft-specific method.
    """
    from unittest.mock import AsyncMock

    from unifideck.services.launcher import service as svc_mod

    svc = svc_mod.LauncherService.__new__(svc_mod.LauncherService)
    svc._launch_wrapper_client = AsyncMock(return_value="UBI")
    svc._edge_browser = object()

    out = await svc._handle_auth_path(
        types.SimpleNamespace(auth_store="ubisoft", action="auth"),
    )
    assert out == "UBI"
    svc._launch_wrapper_client.assert_awaited_once()


@pytest.mark.asyncio
async def test_wrapper_handler_selection_is_per_store_and_action():
    """The shared router must pick the right handler for each combination."""
    from unifideck.launcher.proton.handlers.battlenet import (
        battlenet_auth_launch,
        battlenet_install_launch,
    )
    from unifideck.launcher.proton.handlers.ubisoft import (
        ubisoft_auth_launch,
        ubisoft_install_launch,
    )
    from unifideck.services.launcher import service as svc_mod

    pick = svc_mod.LauncherService._wrapper_handler
    assert pick("ubisoft", "auth") is ubisoft_auth_launch
    assert pick("ubisoft", "install") is ubisoft_install_launch
    assert pick("battlenet", "auth") is battlenet_auth_launch
    assert pick("battlenet", "install") is battlenet_install_launch
    # Non-wrapper stores have no handler; they go through the Edge flow.
    assert pick("epic", "auth") is None
    assert pick(None, "auth") is None


@pytest.mark.asyncio
async def test_non_wrapper_stores_still_use_the_edge_auth_flow(monkeypatch):
    """Regression guard: generalising the router must not swallow OAuth."""
    from unittest.mock import AsyncMock

    from unifideck.services.launcher import service as svc_mod

    svc = svc_mod.LauncherService.__new__(svc_mod.LauncherService)
    svc._launch_wrapper_client = AsyncMock(return_value="WRAPPER")
    svc._edge_browser = object()

    called = {}

    async def fake_handle_store_auth(ctx, edge):
        called["ctx"] = ctx
        return "EDGE"

    monkeypatch.setattr(
        "unifideck.launcher.flows.auth.handle_store_auth", fake_handle_store_auth,
    )
    out = await svc._handle_auth_path(
        types.SimpleNamespace(auth_store="epic", action="auth"),
    )
    assert out == "EDGE"
    svc._launch_wrapper_client.assert_not_awaited()


@pytest.mark.asyncio
async def test_handle_auth_path_routes_oauth_to_browser(monkeypatch):
    from unittest.mock import AsyncMock

    from unifideck.launcher.flows import auth as auth_mod
    from unifideck.services.launcher import service as svc_mod

    monkeypatch.setattr(auth_mod, "handle_store_auth", AsyncMock(return_value="OAUTH"))
    svc = svc_mod.LauncherService.__new__(svc_mod.LauncherService)
    svc._launch_ubisoft_auth = AsyncMock(return_value="UBI")
    svc._edge_browser = object()

    out = await svc._handle_auth_path(
        types.SimpleNamespace(auth_store="epic", action="auth"),
    )
    assert out == "OAUTH"
    svc._launch_ubisoft_auth.assert_not_awaited()


@pytest.mark.asyncio
async def test_install_launch_uses_uplay_install_deeplink(
    tmp_path, monkeypatch, _quiet_toast,
):
    """``ubisoft_install_launch`` points UPC at the title's install
    deeplink when the launch id resolves."""
    captured: dict[str, object] = {}

    async def fake_umu(argv, *, env=None, on_start=None, **kw):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(h, "run_umu_with_retry", fake_umu)
    monkeypatch.setattr(h, "_uplay_id_from_id_map", lambda _sid: "12345")
    monkeypatch.delenv("UPLAY_ID", raising=False)
    upc = tmp_path / _UPC_REL
    upc.parent.mkdir(parents=True, exist_ok=True)
    upc.write_text("stub")

    plan = _plan(tmp_path)
    plan.context.game_id = "100"  # type: ignore[attr-defined]
    rc = await h.ubisoft_install_launch(plan)

    assert rc == 0
    assert captured["argv"] == [
        "/usr/bin/python3",
        "/plugin/bin/umu/umu/umu-run",
        str(upc),
        "uplay://install/12345",
    ]


@pytest.mark.asyncio
async def test_install_launch_bare_without_id(
    tmp_path, monkeypatch, _quiet_toast,
):
    """No resolvable launch id → open UPC bare (user picks the game)."""
    captured: dict[str, object] = {}

    async def fake_umu(argv, *, env=None, on_start=None, **kw):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(h, "run_umu_with_retry", fake_umu)
    monkeypatch.setattr(h, "_uplay_id_from_id_map", lambda _sid: None)
    monkeypatch.delenv("UPLAY_ID", raising=False)
    upc = tmp_path / _UPC_REL
    upc.parent.mkdir(parents=True, exist_ok=True)
    upc.write_text("stub")

    rc = await h.ubisoft_install_launch(_plan(tmp_path))

    assert rc == 0
    assert not any("uplay://" in str(a) for a in captured["argv"])  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_install_launch_missing_upc_raises(
    tmp_path, monkeypatch, _quiet_toast,
):
    # Pin the empty-prefix path: both recovery routes (find a populated
    # prefix elsewhere / clone the .template) must come up empty so the
    # handler raises. Without these patches the test is non-hermetic — on a
    # dev machine with real Ubisoft prefix data it would recover instead.
    monkeypatch.setattr(h, "find_recovered_prefix", lambda _gid: None)
    monkeypatch.setattr(h, "clone_template_into", lambda _dir: False)
    with pytest.raises(GameFailedError, match=r"upc\.exe"):
        await h.ubisoft_install_launch(_plan(tmp_path))


# ── pfx/-nested layout (the real bug) ──────────────────────────────
#
# Regression: umu/Proton nest the real drive_c under ``pfx/`` on a modern
# prefix (confirmed live: the actual .upc-auth auth prefix on a test
# device only had drive_c under pfx/, never at the root). Every upc.exe
# lookup in this file combined ``prefix_root / "drive_c"`` directly,
# so it silently never found an upc.exe that was genuinely present —
# ``ubisoft_auth_launch`` raised GameFailedError with no further trace,
# which surfaced to the user as "Sign In does nothing." Same bug class
# already fixed for system.reg/user.reg in prefix_init.py/epic_cleanup.py
# (see resolve_drive_c/resolve_registry_prefix in prefix_layout.py); this
# file's several upc.exe lookups had never been ported to it.

_UPC_REL_NESTED = Path("pfx") / _UPC_REL


async def test_auth_launch_finds_upc_under_pfx_nesting(
    tmp_path, monkeypatch, _quiet_toast,
):
    captured: dict[str, object] = {}

    async def fake_umu(argv, *, env=None, on_start=None, **kw):
        captured["argv"] = argv
        return 0

    monkeypatch.setattr(h, "run_umu_with_retry", fake_umu)
    upc = tmp_path / _UPC_REL_NESTED
    upc.parent.mkdir(parents=True, exist_ok=True)
    upc.write_text("stub")

    rc = await h.ubisoft_auth_launch(_plan(tmp_path))

    assert rc == 0
    assert captured["argv"] == [
        "/usr/bin/python3",
        "/plugin/bin/umu/umu/umu-run",
        str(upc),
    ]


def test_find_upc_in_prefers_modern_pfx_layout(tmp_path):
    nested = tmp_path / _UPC_REL_NESTED
    nested.parent.mkdir(parents=True, exist_ok=True)
    nested.write_text("stub")

    assert rec.find_upc_in(tmp_path) == nested


def test_find_upc_in_falls_back_to_legacy_layout(tmp_path):
    flat = tmp_path / _UPC_REL
    flat.parent.mkdir(parents=True, exist_ok=True)
    flat.write_text("stub")

    assert rec.find_upc_in(tmp_path) == flat


def test_find_upc_in_none_when_absent(tmp_path):
    assert rec.find_upc_in(tmp_path) is None


def test_clone_template_into_finds_pfx_nested_template(tmp_path, monkeypatch):
    template_dir = tmp_path / "template"
    template_upc = template_dir / _UPC_REL_NESTED
    template_upc.parent.mkdir(parents=True, exist_ok=True)
    template_upc.write_text("stub")
    monkeypatch.setattr(rec, "_TEMPLATE_DIR", template_dir)

    dest = tmp_path / "dest"
    ok = rec.clone_template_into(dest)

    assert ok is True
    assert rec.find_upc_in(dest) is not None


async def test_resolve_or_recover_finds_pfx_nested_recovered_prefix(
    tmp_path, monkeypatch,
):
    recovered_dir = tmp_path / "recovered"
    recovered_upc = recovered_dir / _UPC_REL_NESTED
    recovered_upc.parent.mkdir(parents=True, exist_ok=True)
    recovered_upc.write_text("stub")
    monkeypatch.setattr(h, "find_recovered_prefix", lambda _gid: recovered_dir)

    plan = _plan(tmp_path / "empty")
    result = await h._resolve_or_recover_upc_exe(plan)

    assert result == recovered_upc
    assert plan.env["WINEPREFIX"] == str(recovered_dir)
