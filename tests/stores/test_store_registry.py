"""Tests for stores/shared/store_registry.py.

Covers the two layouts ``_iter_store_files`` accepts:

* Flat: ``stores/<name>_store.py``
* Subpackage: ``stores/<name>/store.py``

Plus the security guards (symlinks, ``_``-prefixed names) and the
auto-discovery happy-path that combines them.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from unifideck.stores.shared.store_registry import StoreRegistry


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)


_FLAT_STORE = """\
from unifideck.core.types import StoreInfo
from unifideck.stores.shared.store_base import StoreBase


class FlatStore(StoreBase):
    store_info = StoreInfo(
        name="flat",
        display_name="Flat",
        auth_method="manual",
        icon_asset="",
    )

    async def is_available(self):
        return False

    async def start_auth(self, **kwargs):
        return None

    async def complete_auth(self, **kwargs):
        return None

    async def logout(self):
        return None

    async def get_library(self):
        return []

    async def install_game(self, game_id, **kwargs):
        return None

    async def uninstall_game(self, game_id, **kwargs):
        return None

    async def update_game(self, game_id, **kwargs):
        return None

    async def check_for_updates(self):
        return []

    async def get_game_size(self, game_id):
        return None
"""


@pytest.fixture
def stores_dir(tmp_path: Path) -> Path:
    """Create a stores/ directory with both layouts."""
    stores = tmp_path / "stores"
    stores.mkdir()
    _write(stores / "__init__.py", "")
    return stores


def _suffixes(stores: Path) -> list[str]:
    return [suf for suf, _ in StoreRegistry._iter_store_files(str(stores))]


def test_iter_finds_flat_store_files(stores_dir: Path) -> None:
    _write(stores_dir / "flat_store.py", "x = 1\n")
    _write(stores_dir / "another_store.py", "x = 1\n")
    suffixes = _suffixes(stores_dir)
    assert "flat_store" in suffixes
    assert "another_store" in suffixes


def test_iter_finds_subpackage_store_files(stores_dir: Path) -> None:
    pkg = stores_dir / "ubisoft"
    _write(pkg / "__init__.py", "")
    _write(pkg / "store.py", "x = 1\n")
    suffixes = _suffixes(stores_dir)
    assert "ubisoft.store" in suffixes


def test_iter_yields_both_layouts_together(stores_dir: Path) -> None:
    _write(stores_dir / "flat_store.py", "x = 1\n")
    pkg = stores_dir / "epic"
    _write(pkg / "__init__.py", "")
    _write(pkg / "store.py", "x = 1\n")
    suffixes = _suffixes(stores_dir)
    assert set(suffixes) == {"flat_store", "epic.store"}


def test_iter_skips_subpackage_without_store_py(stores_dir: Path) -> None:
    pkg = stores_dir / "shared"
    _write(pkg / "__init__.py", "")
    _write(pkg / "helpers.py", "x = 1\n")
    suffixes = _suffixes(stores_dir)
    assert suffixes == []


def test_iter_skips_underscore_prefixed_entries(stores_dir: Path) -> None:
    _write(stores_dir / "_private_store.py", "x = 1\n")
    pkg = stores_dir / "_hidden"
    _write(pkg / "__init__.py", "")
    _write(pkg / "store.py", "x = 1\n")
    assert _suffixes(stores_dir) == []


def test_iter_ignores_nested_files_below_one_level(stores_dir: Path) -> None:
    """Only ``<pkg>/store.py`` counts — deeper ``store.py`` files
    inside sub-subpackages must not be yielded (e.g. ``ubisoft/auth/store.py``
    would be a misnamed support file, not a registrable store)."""
    pkg = stores_dir / "ubisoft"
    _write(pkg / "__init__.py", "")
    _write(pkg / "auth" / "__init__.py", "")
    _write(pkg / "auth" / "store.py", "x = 1\n")
    assert _suffixes(stores_dir) == []


def test_iter_skips_symlinked_flat_store(stores_dir: Path, tmp_path: Path) -> None:
    real = tmp_path / "real_store.py"
    real.write_text("x = 1\n")
    link = stores_dir / "real_store.py"
    os.symlink(real, link)
    assert _suffixes(stores_dir) == []


def test_iter_skips_symlinked_subpackage_store_py(
    stores_dir: Path, tmp_path: Path,
) -> None:
    pkg = stores_dir / "ubisoft"
    _write(pkg / "__init__.py", "")
    real = tmp_path / "real_store.py"
    real.write_text("x = 1\n")
    os.symlink(real, pkg / "store.py")
    assert _suffixes(stores_dir) == []


def test_load_store_class_swallows_import_errors() -> None:
    """A non-existent dotted suffix must return ``None`` rather than
    raise — this is what lets ``auto_discover`` keep going when one
    store has a broken import chain instead of failing the whole
    plugin boot."""
    from unifideck.stores.shared.store_base import StoreBase

    missing = StoreRegistry._load_store_class(
        "unifideck.stores",
        "does_not_exist.store",
        "<unused>",
        StoreBase,
    )
    assert missing is None
