"""Battle.net client bootstrap: fetch the installer, run it, tweak the prefix.

Nothing is bundled — the client is downloaded at runtime from Blizzard's
own installer URL, the same shape as Ubisoft's UPC bootstrap. These tests
pin the failure modes that would otherwise present as a hang rather than an
error:

  * no display environment (the headless Decky env) — a Wine process
    without DISPLAY hangs instead of failing,
  * no 32-bit Vulkan — the client is PE32 i386 and its installer freezes at
    roughly 25% with no message,
  * a truncated download — an error page cached as if it were the stub.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from unifideck.stores.battlenet import paths
from unifideck.stores.battlenet.prefix import client_install as ci


class _Resolver:
    """Stand-in for WineEnvResolver."""

    def __init__(self, *, umu: str | None = "/bin/umu-run", display: bool = True) -> None:
        self._umu = umu
        self._display = display
        self.built: dict[str, str] | None = None

    def find_umu_run(self) -> str | None:
        return self._umu

    def build_env(self, prefix: Any, gameid: str, **_kw: Any) -> dict[str, str]:
        env = {"WINEPREFIX": str(prefix), "GAMEID": gameid, "STORE": "battlenet"}
        if self._display:
            env["DISPLAY"] = ":0"
        self.built = env
        return env


def _install_client(prefix: Path) -> None:
    d = prefix / "drive_c" / paths.CLIENT_DIR
    d.mkdir(parents=True, exist_ok=True)
    (d / paths.CLIENT_EXE).write_bytes(b"MZ")
    (d / paths.LAUNCHER_EXE).write_bytes(b"MZ")


# --------------------------------------------------------------------------
# installer caching
# --------------------------------------------------------------------------


def test_a_valid_cached_installer_is_reused(tmp_path: Path) -> None:
    cached = tmp_path / "Battle.net-Setup.exe"
    cached.write_bytes(b"x" * (ci.MIN_INSTALLER_BYTES + 1))
    result = asyncio.run(ci.ensure_installer("https://example.invalid", cached))
    assert result == cached


def test_a_truncated_cached_installer_is_re_downloaded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A cached error page must not be treated as the installer."""
    cached = tmp_path / "Battle.net-Setup.exe"
    cached.write_bytes(b"<html>404</html>")
    calls: list[str] = []

    def fake_download(url: str, dest: Path) -> bool:
        calls.append(url)
        dest.write_bytes(b"x" * (ci.MIN_INSTALLER_BYTES + 1))
        return True

    monkeypatch.setattr(ci, "_download_sync", fake_download)
    assert asyncio.run(ci.ensure_installer("https://example.invalid", cached)) == cached
    assert calls == ["https://example.invalid"]


def test_a_failed_download_returns_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci, "_download_sync", lambda url, dest: False)
    assert asyncio.run(
        ci.ensure_installer("https://example.invalid", tmp_path / "x.exe"),
    ) is None


def test_a_short_response_is_discarded_not_cached(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    dest = tmp_path / "Battle.net-Setup.exe"

    class _Resp:
        def read(self, *_a: object) -> bytes:
            return b""

        def __enter__(self) -> _Resp:
            return self

        def __exit__(self, *_a: object) -> None:
            return None

    def fake_urlopen(*_a: object, **_k: object) -> _Resp:
        return _Resp()

    monkeypatch.setattr(ci.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(ci.shutil, "copyfileobj", lambda src, dst: dst.write(b"tiny"))
    assert ci._download_sync("https://example.invalid", dest) is False
    assert not dest.exists()


# --------------------------------------------------------------------------
# preconditions that would otherwise hang
# --------------------------------------------------------------------------


def test_refuses_to_run_without_a_display(tmp_path: Path) -> None:
    """Headless Decky: a Wine process with no DISPLAY hangs, not errors."""
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"MZ")
    ok = asyncio.run(
        ci.run_silent_install(installer, tmp_path / "pfx", _Resolver(display=False)),
    )
    assert ok is False


def test_refuses_to_run_without_umu(tmp_path: Path) -> None:
    installer = tmp_path / "setup.exe"
    installer.write_bytes(b"MZ")
    ok = asyncio.run(
        ci.run_silent_install(installer, tmp_path / "pfx", _Resolver(umu=None)),
    )
    assert ok is False


def test_missing_32bit_vulkan_is_reported_up_front(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Otherwise the installer freezes at ~25% with no message at all."""
    monkeypatch.setattr(ci, "has_32bit_vulkan", lambda: False)
    result = asyncio.run(
        ci.bootstrap_client(
            tmp_path / "pfx",
            installer_url="https://example.invalid",
            installer_cache=tmp_path / "x.exe",
            resolver=_Resolver(),
        ),
    )
    assert result.success is False
    assert result.error_code == "missing_32bit_vulkan"


# --------------------------------------------------------------------------
# bootstrap outcomes
# --------------------------------------------------------------------------


def test_an_existing_client_short_circuits(tmp_path: Path) -> None:
    prefix = tmp_path / "pfx"
    _install_client(prefix)
    result = asyncio.run(
        ci.bootstrap_client(
            prefix,
            installer_url="https://example.invalid",
            installer_cache=tmp_path / "x.exe",
            resolver=_Resolver(),
        ),
    )
    assert result.success is True


def test_an_existing_client_still_gets_its_tweaks(tmp_path: Path) -> None:
    """A prefix from an older plugin version must self-heal its settings."""
    from unifideck.stores.battlenet.prefix import tweaks

    prefix = tmp_path / "pfx"
    _install_client(prefix)
    assert tweaks.tweaks_applied(prefix) is False
    asyncio.run(
        ci.bootstrap_client(
            prefix,
            installer_url="https://example.invalid",
            installer_cache=tmp_path / "x.exe",
            resolver=_Resolver(),
        ),
    )
    assert tweaks.tweaks_applied(prefix) is True


def test_a_failed_download_surfaces_a_structured_error(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(ci, "has_32bit_vulkan", lambda: True)
    monkeypatch.setattr(ci, "_download_sync", lambda url, dest: False)
    result = asyncio.run(
        ci.bootstrap_client(
            tmp_path / "pfx",
            installer_url="https://example.invalid",
            installer_cache=tmp_path / "x.exe",
            resolver=_Resolver(),
        ),
    )
    assert result.success is False
    assert result.error_code == "installer_download_failed"


def test_install_success_is_judged_by_the_filesystem_not_the_exit_code(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The stub has been seen exiting non-zero after a successful install."""
    prefix = tmp_path / "pfx"

    async def fake_install(installer: Path, target: Path, resolver: Any) -> bool:
        _install_client(target)
        return paths.client_installed(target)

    monkeypatch.setattr(ci, "has_32bit_vulkan", lambda: True)
    monkeypatch.setattr(ci, "run_silent_install", fake_install)
    monkeypatch.setattr(
        ci, "ensure_installer",
        lambda url, cache: asyncio.sleep(0, result=Path("/tmp/setup.exe")),
    )
    result = asyncio.run(
        ci.bootstrap_client(
            prefix,
            installer_url="https://example.invalid",
            installer_cache=tmp_path / "x.exe",
            resolver=_Resolver(),
        ),
    )
    assert result.success is True
    assert paths.client_installed(prefix)


# --------------------------------------------------------------------------
# tweaks
# --------------------------------------------------------------------------


def test_client_config_merge_preserves_saved_account(tmp_path: Path) -> None:
    """Clobbering the config would silently sign the user out."""
    import json

    from unifideck.stores.battlenet.prefix import tweaks

    prefix = tmp_path / "pfx"
    drive_c = prefix / "drive_c"
    cfg = drive_c / tweaks.CONFIG_RELATIVE
    cfg.parent.mkdir(parents=True)
    cfg.write_text(json.dumps({"Client": {"SavedAccountNames": "me@example.com"}}))

    assert tweaks.write_client_config(drive_c) is True
    data = json.loads(cfg.read_text())
    assert data["Client"]["SavedAccountNames"] == "me@example.com"
    assert data["Client"]["HardwareAcceleration"] == "false"
