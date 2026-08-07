"""Every launcher spawn must go through the pressure-vessel escape.

Setting Steam's Properties > Compatibility on a Unifideck shortcut — the only
way to pick a specific Proton, since Unifideck has no picker of its own —
makes Steam wrap ``bin/unifideck-launcher`` in ITS OWN pressure-vessel
container. Inside it Proton's ``python3`` cannot load ``libz.so.1`` and umu
exits 127. ``container_escape.escape_argv`` was written for exactly this, but
it was wired into ``run_umu_with_retry`` only — so the game launch escaped
while ``createprefix``, ``wineboot``, the Epic registry/prereq steps and the
GOG setup installers all still ran inside the container and failed.

The ordering made it destructive rather than merely broken:
``ensure_prefix_initialized`` resets the prefix on a Proton-family change
BEFORE creating it, so the working prefix was wiped and then never rebuilt.

The static test below is the important one: it is what stops the next
contributor from silently re-opening this by reaching for
``asyncio.create_subprocess_exec`` directly.
"""
from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import AsyncMock

import pytest

_PROTON_PKG = (
    Path(__file__).resolve().parents[2]
    / "py_modules" / "unifideck" / "launcher" / "proton"
)
# The one module allowed to call it — it IS the escape.
_SPAWN_OWNER = "infrastructure/container_escape.py"


def _calls_create_subprocess_exec(tree: ast.AST) -> bool:
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        if isinstance(func, ast.Attribute) and func.attr == "create_subprocess_exec":
            return True
        if isinstance(func, ast.Name) and func.id == "create_subprocess_exec":
            return True
    return False


def test_container_escape_is_the_only_async_spawn_point():
    offenders = []
    for path in sorted(_PROTON_PKG.rglob("*.py")):
        rel = path.relative_to(_PROTON_PKG).as_posix()
        if rel == _SPAWN_OWNER:
            continue
        if _calls_create_subprocess_exec(ast.parse(path.read_text("utf-8"))):
            offenders.append(rel)
    assert offenders == [], (
        "these modules spawn without escaping Steam's pressure-vessel "
        f"container: {offenders}. Use container_escape.spawn_escaped."
    )


# ── behavioural: the sites that actually broke ────────────────────


@pytest.fixture
def spawn_spy(monkeypatch):
    """Patch ``spawn_escaped`` where each module imported it."""
    from unifideck.launcher.proton.compat import epic as compat_epic
    from unifideck.launcher.proton.compat import prefix_init
    from unifideck.launcher.proton.compat.gog_setup import common as gog_common
    from unifideck.launcher.proton.fixes import epic_registry

    proc = AsyncMock()
    proc.wait = AsyncMock(return_value=0)
    proc.communicate = AsyncMock(return_value=(b"", b""))
    proc.returncode = 0
    spy = AsyncMock(return_value=proc)
    for mod in (prefix_init, compat_epic, gog_common, epic_registry):
        monkeypatch.setattr(mod, "spawn_escaped", spy)
    return spy


async def test_createprefix_goes_through_the_escape(spawn_spy, tmp_path):
    """The rc=127 site: umu ``createprefix`` under a forced Proton."""
    from types import SimpleNamespace

    from unifideck.launcher.proton.compat import prefix_init

    plan = SimpleNamespace(
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        prefix_path=tmp_path,
        env={},
        context=SimpleNamespace(game_key="epic:g1"),
        state=SimpleNamespace(proton_tool_id="proton_9"),
    )

    await prefix_init._run_umu(plan, {}, "createprefix")

    spawn_spy.assert_awaited_once()
    argv = spawn_spy.await_args.args[0]
    assert argv[-1] == "createprefix"


async def test_gog_setup_installer_goes_through_the_escape(spawn_spy, tmp_path):
    from types import SimpleNamespace

    from unifideck.launcher.proton.compat.gog_setup import common as gog_common

    plan = SimpleNamespace(
        python_bin=Path("/usr/bin/python3"),
        umu_wrapper=Path("/plugin/bin/umu/umu/umu-run"),
        env={"PROTONPATH": "/p"},
    )

    await gog_common.run_wine(plan, "/games/setup.exe", ["/S"])

    spawn_spy.assert_awaited_once()
    assert "/games/setup.exe" in spawn_spy.await_args.args[0]
