"""Ubisoft umu-runtime self-heal + honest success reporting.

Two bugs, found together while triaging repeated "Ubisoft store doesn't
load" reports (tracker UD-004 / UD-109 / UD-115):

1. Every backend-side (in-process) umu-run spawn for Ubisoft resolves the
   binary via ``UbisoftBinaryResolver.find_umu_run()`` — but unlike the
   out-of-process launcher's ``dispatch()``, this resolver never called
   ``repair_incomplete_umu_runtime()`` (UD-084's self-heal). A half-broken
   umu runtime (payload present, entry-point symlink missing) makes
   umu-run exit fast with code 0 without installing anything, and nothing
   ever wiped the broken cache to let it re-download cleanly.

2. ``_AuthPrefixBuilder._build_auth_prefix_from_source`` had a faulty
   guard (``if not success and not find_upc_exe(...): return False``)
   that only bailed when BOTH the installer reported failure AND upc.exe
   was missing — so a false "success" (installer exited 0 but produced no
   upc.exe) slipped through, writing a bootstrap marker onto an empty
   prefix and reporting ``True``.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

import pytest

from unifideck.stores.ubisoft.config import UbisoftConfig


def _cfg(prefixes_root: Path) -> UbisoftConfig:
    return UbisoftConfig(prefixes_dir=str(prefixes_root))


def _paths(cfg: UbisoftConfig):
    from unifideck.stores.ubisoft.paths import UbisoftPrefixPaths
    return UbisoftPrefixPaths(config=cfg)


class _FakeInstallerCache:
    async def ensure_cached(self) -> str | None:
        return "/fake/UbisoftConnectInstaller.exe"


class _FakeTemplateBuilder:
    """No template exists yet — forces the fresh-install branch."""

    def template_exists(self) -> bool:
        return False

    def is_prefix_version_stale(self, prefix_dir: str) -> bool:
        return False

    async def regenerate_template_if_stale(self) -> None:
        return None


def _auth_builder(tmp_path: Path, *, run_silent_installer):
    """Build a real ``_AuthPrefixBuilder`` with a stubbed installer step."""
    from unifideck.stores.ubisoft.prefix.auth_builder import _AuthPrefixBuilder

    cfg = _cfg(tmp_path)
    paths_ = _paths(cfg)

    class _Helpers:
        def fix_pfx_symlink(self, prefix_dir: str) -> None:
            return None

        def try_inject_auth_state(self, prefix_paths: list[str]) -> None:
            return None

        def write_bootstrap_marker(self, prefix_dir: str, source: str, space_id: str | None) -> None:
            marker = Path(prefix_dir) / cfg.bootstrap_marker
            marker.write_text(source, encoding="utf-8")

        async def create_template_from_auth_prefix(self, auth_dir: str) -> None:
            return None

    helpers = _Helpers()
    helpers.run_silent_installer = run_silent_installer  # type: ignore[attr-defined]

    return _AuthPrefixBuilder(
        config=cfg,
        paths=paths_,
        helpers=helpers,  # type: ignore[arg-type]
        installer_cache=_FakeInstallerCache(),  # type: ignore[arg-type]
        template_builder=_FakeTemplateBuilder(),  # type: ignore[arg-type]
    ), cfg


@pytest.mark.asyncio
async def test_false_success_never_marks_auth_prefix_bootstrapped(tmp_path):
    """A "successful" installer run that produced no upc.exe must not
    write a bootstrap marker or report the auth prefix as ready.

    Reproduces the UD-004/UD-109 loop: umu-run exits 0 (broken runtime)
    but never actually installs UPC.
    """
    async def _fake_installer_no_upc(*, prefix_dir: str, **kw: object) -> bool:
        Path(prefix_dir).mkdir(parents=True, exist_ok=True)
        return True  # umu-run "succeeded" (rc=0) but installed nothing

    builder, cfg = _auth_builder(tmp_path, run_silent_installer=_fake_installer_no_upc)

    upc_path = await builder.ensure_auth_prefix()

    assert upc_path is None, "must not report a upc.exe path that doesn't exist"
    auth_dir = Path(cfg.auth_prefix_dir_expanded)
    marker = auth_dir / cfg.bootstrap_marker
    assert not marker.is_file(), (
        "bootstrap marker must never be written when upc.exe is missing "
        "— a stray marker here is what causes the endless "
        "'auth prefix exists but upc.exe missing, re-cloning' loop"
    )


@pytest.mark.asyncio
async def test_real_success_still_marks_auth_prefix_bootstrapped(tmp_path):
    """Happy path: installer succeeds and produces upc.exe → marker written."""
    async def _fake_installer_with_upc(*, prefix_dir: str, **kw: object) -> bool:
        prefix = Path(prefix_dir)
        upc = (
            prefix
            / "drive_c"
            / "Program Files (x86)"
            / "Ubisoft"
            / "Ubisoft Game Launcher"
            / "upc.exe"
        )
        upc.parent.mkdir(parents=True, exist_ok=True)
        upc.touch()
        return True

    builder, cfg = _auth_builder(tmp_path, run_silent_installer=_fake_installer_with_upc)

    upc_path = await builder.ensure_auth_prefix()

    assert upc_path is not None
    auth_dir = Path(cfg.auth_prefix_dir_expanded)
    marker = auth_dir / cfg.bootstrap_marker
    assert marker.is_file(), "bootstrap marker must be written on genuine success"


def test_find_umu_run_self_heals_incomplete_runtime_first(tmp_path):
    """UbisoftBinaryResolver.find_umu_run() must self-heal a half-broken
    umu runtime (UD-084) before resolving/returning the binary path —
    every backend-side umu-run spawn for Ubisoft goes through this
    resolver, so it's the one choke point that protects them all.
    """
    from unifideck.stores.ubisoft.binaries import UbisoftBinaryResolver

    cfg = _cfg(tmp_path)
    resolver = UbisoftBinaryResolver(config=cfg, plugin_dir=None)

    with patch(
        "unifideck.stores.ubisoft.binaries.repair_incomplete_umu_runtime",
    ) as mock_repair:
        resolver.find_umu_run()

    mock_repair.assert_called_once()
