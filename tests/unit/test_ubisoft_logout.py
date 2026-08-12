"""Regression: Ubisoft logout must be instant (no blocking rmtree).

The auth prefix is a full UPC Wine prefix; a synchronous ``rmtree`` took
~45s and blocked the event loop, so the QAM sign-out button greyed and
looked dead. Logout now renames the prefix aside (atomic, instant) and
deletes it in the background.
"""
from __future__ import annotations

import asyncio

import pytest

from unifideck.stores.shared.wrapper_auth_monitor import WrapperAuthMonitor
from unifideck.stores.ubisoft.auth import facade as facade_mod
from unifideck.stores.ubisoft.auth.facade import UbisoftAuth


class _Session:
    def __init__(self) -> None:
        self.cleared = False

    def clear_session_file(self) -> None:
        self.cleared = True


class _Bus:
    def __init__(self) -> None:
        self.emitted: list = []

    async def emit(self, event, **kwargs) -> None:
        self.emitted.append((event, kwargs))


class _Cfg:
    def __init__(self, auth_dir: str) -> None:
        self.auth_prefix_dir_expanded = auth_dir


def _make_auth(auth_dir: str) -> UbisoftAuth:
    auth = UbisoftAuth.__new__(UbisoftAuth)
    auth._session = _Session()
    auth._bus = _Bus()
    auth._config = _Cfg(auth_dir)
    # Logout abandons any sign-in still being watched. Never started here, so
    # ``stop`` is a no-op — it is present because the collaborator is real.
    auth._monitor = WrapperAuthMonitor(
        store="ubisoft",
        is_signed_in=auth._capture_auth_session,
        bus=auth._bus,
    )
    return auth


def _populate(prefix, n_files: int = 50):
    (prefix / "drive_c").mkdir(parents=True)
    for i in range(n_files):
        (prefix / "drive_c" / f"f{i}.dat").write_text("x")


def test_rename_to_trash_moves_aside(tmp_path):
    prefix = tmp_path / ".upc-auth"
    _populate(prefix)
    trash = UbisoftAuth._rename_to_trash(str(prefix))
    assert trash is not None
    assert ".trash-" in trash
    assert not prefix.exists()  # original gone immediately
    from pathlib import Path
    assert Path(trash).is_dir()  # contents moved, not deleted yet


def test_rename_to_trash_missing_dir(tmp_path):
    assert UbisoftAuth._rename_to_trash(str(tmp_path / "nope")) is None


@pytest.mark.asyncio
async def test_logout_signs_out_immediately_and_purges(tmp_path):
    prefix = tmp_path / ".upc-auth"
    _populate(prefix, n_files=80)
    auth = _make_auth(str(prefix))

    res = await auth.logout()

    assert res.success
    assert auth._session.cleared
    assert any(evt for evt, _ in auth._bus.emitted)
    # Signed out instantly: the prefix path is already gone after logout
    # returns (renamed aside), without waiting for the recursive delete.
    assert not prefix.exists()

    # Let the background purge finish and confirm it actually deletes.
    if facade_mod._PURGE_TASKS:
        await asyncio.gather(*list(facade_mod._PURGE_TASKS))
    leftover = list(tmp_path.glob(".upc-auth.trash-*"))
    assert leftover == [], f"trash not purged: {leftover}"
