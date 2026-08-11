"""A Battle.net game is installed only when the client says it is.

The reported bug: pressing Install put a Play button on the tile immediately.
``BattlenetInstaller.install`` returned ``success=True`` the moment the Wine
prefix was cloned — before the client had even opened, let alone downloaded
anything — and the download worker takes a successful ``InstallResult`` as a
finished install.

The trap underneath it is that ``aggregate.json`` looks like the answer and is
not: during a real 12.43 GB Hearthstone install the entry appeared at roughly
40% downloaded. Only ``product.db``'s ``installed``/``playable``/
``update_complete`` flags mean finished.

The reported *size* bug fell out of the same thing, from the other side: the
install reported the prefix as its ``install_path``, so the id map never
learned where the game actually lives.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any

import pytest

from unifideck.core.types.results import InstallResult
from unifideck.stores.battlenet import install as install_mod
from unifideck.stores.battlenet.id_map import BattlenetIdMap
from unifideck.stores.battlenet.install import BattlenetInstaller, PreparedInstall
from unifideck.stores.battlenet.install_watch import BattlenetInstallProbe
from unifideck.stores.battlenet.ownership import InstalledGame
from unifideck.stores.battlenet.ownership.installed import AGGREGATE_RELATIVE
from unifideck.stores.battlenet.product_db.reader import PRODUCT_DB_RELATIVE

FIXTURES = Path(__file__).parent.parent / "fixtures" / "battlenet"
UID = "hs_beta"
GAME_REL = "drive_c/Program Files (x86)/Hearthstone"


def _prefix(tmp_path: Path, *, aggregate: bool, product_db: bool) -> Path:
    """A prefix carrying whichever halves of the client's state we want."""
    prefix = tmp_path / "prefixes" / "battlenet" / UID
    drive_c = prefix / "drive_c"
    wanted = []
    if aggregate:
        wanted.append((AGGREGATE_RELATIVE, "aggregate_installed.json"))
    if product_db:
        wanted.append((PRODUCT_DB_RELATIVE, "product_db_installed.bin"))
    for relative, source in wanted:
        target = drive_c / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes((FIXTURES / source).read_bytes())
    (prefix / GAME_REL).mkdir(parents=True, exist_ok=True)
    return prefix


# ── the probe: what counts as "installed" ───────────────────────


def _mid_download(**over: Any) -> InstalledGame:
    """The client's state part-way through a download.

    ``product.db``'s row exists from the moment the download starts, with its
    three ready flags off — that is the case the aggregate/product merge exists
    to represent, and the one the old code had no way to distinguish from
    "finished".
    """
    fields: dict[str, Any] = {
        "code": "hsb",
        "uid": UID,
        "name": "Hearthstone",
        "install_path": "C:/Program Files (x86)/Hearthstone",
        "host_install_path": "/pfx/drive_c/Program Files (x86)/Hearthstone",
        "host_exe_path": "/pfx/drive_c/Program Files (x86)/Hearthstone/hs.exe",
        "total_bytes": 0,
        "is_ready": False,
    }
    fields.update(over)
    return InstalledGame(**fields)


def test_a_download_in_flight_is_not_an_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The whole bug in one assertion: a live download is not a Play button."""
    probe = BattlenetInstallProbe(UID, tmp_path)
    monkeypatch.setattr(probe, "row", lambda: _mid_download())

    assert probe.detect(probe.snapshot()) is not None, "we know where it is going"
    assert probe.is_complete("/anywhere") is False, "but it is not there yet"


def test_a_partly_written_row_still_locates_the_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """No install path yet — the executable's parent is the install directory.

    Worth the fallback: without it the "Installing… (N GB)" tick shows nothing
    for the window before the client writes the path.
    """
    probe = BattlenetInstallProbe(UID, tmp_path)
    monkeypatch.setattr(
        probe, "row", lambda: _mid_download(host_install_path=None),
    )

    assert probe.detect(None) == "/pfx/drive_c/Program Files (x86)/Hearthstone"


def test_a_finished_install_is_recognised(tmp_path: Path) -> None:
    prefix = _prefix(tmp_path, aggregate=True, product_db=True)
    probe = BattlenetInstallProbe(UID, prefix)

    assert probe.is_complete("/anywhere") is True
    assert probe.detect(probe.snapshot()) == str(prefix / GAME_REL)


def test_another_title_finishing_does_not_complete_this_one(tmp_path: Path) -> None:
    """Per-uid, not "anything ready in this prefix".

    That broader question is the right one for the deletion guard
    (``holds_ready_install``) and the wrong one here — a sibling Blizzard
    title going ready would complete an install that had not started.
    """
    probe = BattlenetInstallProbe("d4", _prefix(tmp_path, aggregate=True, product_db=True))

    assert probe.detect(probe.snapshot()) is None
    assert probe.is_complete("/anywhere") is False


def test_an_empty_prefix_is_never_complete(tmp_path: Path) -> None:
    """The exact state the bug shipped as: a freshly cloned prefix."""
    probe = BattlenetInstallProbe(UID, _prefix(tmp_path, aggregate=False, product_db=False))

    assert probe.detect(probe.snapshot()) is None
    assert probe.is_complete("/anywhere") is False


def test_the_verdict_is_never_deferred_to_the_size_heuristic(tmp_path: Path) -> None:
    """``None`` would hand completion to "the size stopped changing".

    That heuristic exists for Ubisoft, which has nothing better; it ends an
    install when a download merely pauses. A store that can answer must.
    """
    for aggregate, product_db in ((False, False), (True, False), (True, True)):
        probe = BattlenetInstallProbe(
            UID, _prefix(tmp_path, aggregate=aggregate, product_db=product_db),
        )
        assert probe.is_complete("/anywhere") is not None


# ── the installer: what it does with that ───────────────────────


class _Prefixes:
    def __init__(self, prefix: Path) -> None:
        self._prefix = prefix
        self.removed: list[Path] = []

    def auth_ready(self) -> bool:
        return True

    def game_prefix(self, _uid: str) -> Path:
        return self._prefix

    async def create_game_prefix(
        self, _uid: str, destination: Path | None = None,
    ) -> Path:
        return Path(destination or self._prefix)

    def remove_game_prefix(self, path: Path) -> bool:
        self.removed.append(Path(path))
        return True


def _installer(tmp_path: Path, prefix: Path) -> tuple[BattlenetInstaller, BattlenetIdMap]:
    id_map = BattlenetIdMap(tmp_path / "battlenet_id_map.json")
    id_map.merge(UID, family="WTCG", prefix_path=str(prefix))
    return BattlenetInstaller(_Prefixes(prefix), id_map), id_map


def _stub_watch(monkeypatch: pytest.MonkeyPatch, result: Any) -> None:
    async def _watch(**_kwargs: Any) -> Any:
        if isinstance(result, BaseException):
            raise result
        return result

    monkeypatch.setattr(install_mod, "watch_manual_install", _watch)


def test_install_reports_the_game_directory_not_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The size bug's root: the prefix was reported as the install path.

    Nothing downstream could tell the difference, so the id map never learned
    where the game lived and every path that reads it back was wrong.
    """
    prefix = _prefix(tmp_path, aggregate=True, product_db=True)
    installer, id_map = _installer(tmp_path, prefix)
    game_dir = str(prefix / GAME_REL)
    _stub_watch(monkeypatch, game_dir)

    result = asyncio.run(
        installer._watch(UID, PreparedInstall(prefix=prefix), None, None),
    )

    assert result.success
    assert result.install_path == game_dir
    assert result.install_path != str(prefix)
    assert result.size_bytes == 12428894444
    record = id_map.get(UID)
    assert record.install_path == game_dir
    assert record.exe_path.endswith("Hearthstone Beta Launcher.exe")
    assert record.total_bytes == 12428894444


def test_a_watch_that_never_saw_the_game_fails_the_install(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = _prefix(tmp_path, aggregate=False, product_db=False)
    installer, _ = _installer(tmp_path, prefix)
    _stub_watch(monkeypatch, None)

    result = asyncio.run(
        installer._watch(UID, PreparedInstall(prefix=prefix), None, None),
    )

    assert result.success is False
    assert result.error_code == "no_install_detected"


def test_cancel_closes_the_client_and_reclaims_an_empty_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    prefix = _prefix(tmp_path, aggregate=False, product_db=False)
    installer, id_map = _installer(tmp_path, prefix)
    _stub_watch(monkeypatch, asyncio.CancelledError())
    killed: list[tuple[Any, ...]] = []
    monkeypatch.setattr(install_mod, "kill_client", lambda *a, **k: killed.append(a))

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            installer._watch(UID, PreparedInstall(prefix=prefix), None, None),
        )

    assert killed and killed[0][0] == "battlenet"
    assert id_map.resolve_prefix(UID) is None, "the empty prefix is reclaimed"


def test_a_cancel_that_races_completion_keeps_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """For this store the prefix IS the game, so cleanup here deletes it.

    ``holds_ready_install`` is re-read at cleanup time rather than snapshotted,
    which is what makes the race safe.
    """
    prefix = _prefix(tmp_path, aggregate=True, product_db=True)
    installer, id_map = _installer(tmp_path, prefix)
    _stub_watch(monkeypatch, asyncio.CancelledError())
    monkeypatch.setattr(install_mod, "kill_client", lambda *a, **k: None)

    with pytest.raises(asyncio.CancelledError):
        asyncio.run(
            installer._watch(UID, PreparedInstall(prefix=prefix), None, None),
        )

    assert id_map.resolve_prefix(UID) == prefix
    assert prefix.is_dir()


def test_update_never_resets_the_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Resetting is how an install starts. Here it would delete the game."""
    prefix = _prefix(tmp_path, aggregate=True, product_db=True)
    installer, _ = _installer(tmp_path, prefix)
    _stub_watch(monkeypatch, str(prefix / GAME_REL))

    def _explode(*_a: Any, **_k: Any) -> None:
        raise AssertionError("an update must not go through prefix placement")

    monkeypatch.setattr(install_mod, "reset_for_fresh_install", _explode)
    monkeypatch.setattr(install_mod, "resolve_prefix_target", _explode)

    result = asyncio.run(installer.update(UID))

    assert result.success
    assert prefix.is_dir()


def test_a_failed_update_does_not_reclaim_the_existing_prefix(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """That prefix predates the update — it was never ours to clean up."""
    prefix = _prefix(tmp_path, aggregate=True, product_db=True)
    installer, id_map = _installer(tmp_path, prefix)
    _stub_watch(monkeypatch, None)

    result = asyncio.run(installer.update(UID))

    assert result.success is False
    assert id_map.resolve_prefix(UID) == prefix
    assert prefix.is_dir()


def test_update_without_a_recorded_prefix_fails_cleanly(tmp_path: Path) -> None:
    id_map = BattlenetIdMap(tmp_path / "battlenet_id_map.json")
    installer = BattlenetInstaller(_Prefixes(tmp_path / "nope"), id_map)

    result = asyncio.run(installer.update(UID))

    assert isinstance(result, InstallResult)
    assert result.error_code == "prefix_unknown"
