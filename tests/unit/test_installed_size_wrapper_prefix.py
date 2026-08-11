"""What "Installed size" measures, per store.

For nearly every store it is the install directory. For a **wrapper store** it
is the Wine prefix, and that is a deliberate choice rather than an accident of
where the path came from: the vendor client runs inside the prefix, installs
the game into it, and uninstalling removes the prefix — so the prefix is both
what the game costs and what the user gets back.

The rule is keyed on ``prefix_owns_game_install``, the same row that decides
whether a prefix reset is destructive. Keying it on a store name is how a
disagreement between two such sites once deleted a user's game.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from unifideck.stores.shared import installed_size as mod


class _Adapter:
    """A store that knows both its install dir and (maybe) its prefix."""

    def __init__(self, install_path: str | None, prefix: str | None = None) -> None:
        self._install_path = install_path
        self._prefix = prefix
        self.prefix_calls = 0

    async def get_installed_path(self, _game_id: str) -> str | None:
        return self._install_path

    def get_prefix_path(self, _game_id: str) -> str | None:
        self.prefix_calls += 1
        return self._prefix


def _tree(root: Path, name: str, *, size: int) -> Path:
    """A directory holding one file of ``size`` bytes."""
    path = root / name
    path.mkdir(parents=True, exist_ok=True)
    (path / "data.bin").write_bytes(b"\0" * size)
    return path


@pytest.fixture
def prefix_and_game(tmp_path: Path) -> tuple[Path, Path]:
    """A prefix with a game inside it, as a wrapper store lays them out."""
    prefix = tmp_path / "prefixes" / "battlenet" / "hs_beta"
    _tree(prefix, "drive_c/windows", size=300)  # the Wine tree + client
    game = _tree(prefix, "drive_c/Program Files (x86)/Hearthstone", size=700)
    return prefix, game


def test_a_wrapper_store_is_sized_by_its_prefix(
    prefix_and_game: tuple[Path, Path],
) -> None:
    """Sizing only the game dir under-reports by the client and Wine tree.

    Those exist solely to run this one game and go away with it, so leaving
    them out disagrees with the space an uninstall actually reclaims.
    """
    prefix, game = prefix_and_game
    adapter = _Adapter(str(game), str(prefix))

    size = asyncio.run(
        mod.installed_size_bytes(adapter, str(game), "hs_beta", "battlenet"),
    )

    assert size == 1000, "the whole prefix, not just the game directory"


def test_an_ordinary_store_is_still_sized_by_its_install_directory(
    tmp_path: Path,
) -> None:
    """Epic/GOG/Amazon download outside their prefix — nothing changes for them."""
    game = _tree(tmp_path, "Games/Bastion", size=700)
    adapter = _Adapter(str(game), str(tmp_path / "prefixes" / "epic"))

    size = asyncio.run(
        mod.installed_size_bytes(adapter, str(game), "bastion", "epic"),
    )

    assert size == 700
    assert adapter.prefix_calls == 0, "no extra lookup for a non-wrapper store"


def test_a_wrapper_store_with_no_resolvable_prefix_falls_back(
    prefix_and_game: tuple[Path, Path],
) -> None:
    """A prefix we cannot resolve is no reason to show nothing."""
    _prefix, game = prefix_and_game
    adapter = _Adapter(str(game), None)

    size = asyncio.run(
        mod.installed_size_bytes(adapter, str(game), "hs_beta", "battlenet"),
    )

    assert size == 700


def test_a_prefix_that_is_gone_falls_back_to_the_install_directory(
    prefix_and_game: tuple[Path, Path],
) -> None:
    """A recorded path can outlive the directory — a moved SD card, a wipe."""
    _prefix, game = prefix_and_game
    adapter = _Adapter(str(game), "/nowhere/at/all")

    size = asyncio.run(
        mod.installed_size_bytes(adapter, str(game), "hs_beta", "battlenet"),
    )

    assert size == 700


def test_a_raising_prefix_lookup_never_breaks_the_size(
    prefix_and_game: tuple[Path, Path],
) -> None:
    _prefix, game = prefix_and_game

    class _Broken(_Adapter):
        def get_prefix_path(self, _game_id: str) -> str | None:
            raise RuntimeError("id map unreadable")

    size = asyncio.run(
        mod.installed_size_bytes(
            _Broken(str(game)), str(game), "hs_beta", "battlenet",
        ),
    )

    assert size == 700


def test_no_store_given_behaves_as_before(
    prefix_and_game: tuple[Path, Path],
) -> None:
    """``store`` is optional, so existing callers keep the old resolution."""
    _prefix, game = prefix_and_game
    adapter = _Adapter(str(game), "/unused")

    resolved = asyncio.run(mod.resolve_size_root(adapter, str(game), "hs_beta"))

    assert resolved == str(game)


def test_the_qam_list_and_app_details_size_the_same_directory(
    prefix_and_game: tuple[Path, Path],
) -> None:
    """They must agree, or a game is sized from one place and labelled another.

    The QAM "Installed" list classifies internal/external from whatever
    directory it sized; App-Details shows the number. Both go through
    ``resolve_size_root`` for exactly this reason.
    """
    from unifideck.services import installed_disk_info as qam

    prefix, game = prefix_and_game
    adapter = _Adapter(str(game), str(prefix))
    args: dict[str, Any] = {
        "adapter": adapter, "cache_path": str(game), "game_id": "hs_beta",
        "store": "battlenet",
    }

    assert qam.resolve_size_root is mod.resolve_size_root
    assert asyncio.run(mod.resolve_size_root(**args)) == str(prefix)
