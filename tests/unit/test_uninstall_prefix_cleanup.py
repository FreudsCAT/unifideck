"""Per-store uninstall cleanup regressions surfaced by live validation.

With the "also delete Proton prefix" toggle ON, GOG leaked its ~1.6 GB
prefix (the store dropped ``delete_prefix`` via ``**kwargs``) and Amazon
leaked both its prefix and a stub install dir — ``nile uninstall`` removes
only the files it tracks, leaving our own ``.unifideck_manifest.json`` (and
the directory with it) behind, and never touches the prefix.

These cover the fixes: GOG now honours ``delete_prefix``; Amazon resolves
the install dir up front, runs nile best-effort, then deletes any leftover
directory + the prefix itself.
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from unifideck.core.safe_delete import canonical_prefix
from unifideck.core.types import Result
from unifideck.stores.amazon.amazon_install import AmazonInstaller
from unifideck.stores.gog.store import GOGStore


def _fake_home(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    home = tmp_path / "home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    return home


# --------------------------------------------------------------------------
# GOG
# --------------------------------------------------------------------------
def _gog_store(install_path: str) -> GOGStore:
    store = GOGStore.__new__(GOGStore)
    store._library = MagicMock()
    store._library.get_installed_game_info = MagicMock(
        return_value={"install_path": install_path},
    )
    store._installer = MagicMock()
    store._installer.uninstall_game = AsyncMock(
        return_value=Result(success=True),
    )
    store._emit = AsyncMock()
    return store


@pytest.mark.asyncio
async def test_gog_uninstall_deletes_prefix_when_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    game_id = "1356485086"
    prefix = canonical_prefix(game_id)
    prefix.mkdir(parents=True)
    store = _gog_store(install_path=str(tmp_path / "game"))

    result = await store.uninstall_game(game_id, delete_prefix=True)

    assert result.success
    assert not prefix.exists()
    store._emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_gog_uninstall_keeps_prefix_when_not_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    game_id = "1356485086"
    prefix = canonical_prefix(game_id)
    prefix.mkdir(parents=True)
    store = _gog_store(install_path=str(tmp_path / "game"))

    result = await store.uninstall_game(game_id, delete_prefix=False)

    assert result.success
    assert prefix.exists()


# --------------------------------------------------------------------------
# Amazon
# --------------------------------------------------------------------------
def _amazon_installer(
    tmp_path: Path, install_dir: Path, *, cli: str | None = None,
) -> AmazonInstaller:
    inst = AmazonInstaller.__new__(AmazonInstaller)
    inst._cli_path = cli
    inst._default_install_root = str(tmp_path / "Games")
    inst._uninstall_timeout = 5
    inst._find_exe = lambda _p, _ids=None: None
    inst._library = MagicMock()
    inst._library.read_installed_ids = AsyncMock(
        return_value={"g1": {"path": str(install_dir)}},
    )
    inst._library.read_owned_games = AsyncMock(return_value=[])
    inst._bus = MagicMock()
    inst._bus.emit = AsyncMock()
    return inst


@pytest.mark.asyncio
async def test_amazon_uninstall_removes_stub_dir_and_prefix(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    """nile gone/failed → fall back to deleting the dir + prefix ourselves."""
    _fake_home(monkeypatch, tmp_path)
    install_dir = tmp_path / "Games" / "Clouds & Sheep 2"
    install_dir.mkdir(parents=True)
    # The exact stub the real bug left behind: only our marker remains.
    (install_dir / ".unifideck_manifest.json").write_text("{}")
    prefix = canonical_prefix("g1")
    prefix.mkdir(parents=True)

    inst = _amazon_installer(tmp_path, install_dir, cli=None)
    result = await inst.uninstall_game("g1", delete_prefix=True)

    assert result.success
    assert not install_dir.exists()   # stub + manifest gone
    assert not prefix.exists()        # 1.3 GB prefix gone
    inst._bus.emit.assert_awaited_once()


@pytest.mark.asyncio
async def test_amazon_uninstall_keeps_prefix_when_not_requested(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
) -> None:
    _fake_home(monkeypatch, tmp_path)
    install_dir = tmp_path / "Games" / "Clouds & Sheep 2"
    install_dir.mkdir(parents=True)
    prefix = canonical_prefix("g1")
    prefix.mkdir(parents=True)

    inst = _amazon_installer(tmp_path, install_dir, cli=None)
    result = await inst.uninstall_game("g1", delete_prefix=False)

    assert result.success
    assert not install_dir.exists()
    assert prefix.exists()
