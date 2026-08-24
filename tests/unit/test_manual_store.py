"""The manual store: state, library mapping, uninstall guards, and helpers.

The invariants that keep the Manual Install flow working end to end:
the derived game id must satisfy the identifier rules (it names the
prefix directory and the ``manual:<id>`` launch token), the launch
token must be recognised by ``STORE_ID_PATTERN`` (otherwise reconcile
disowns the shortcut), ``get_library`` must always report installed
games WITH an exe (games.map rows are only written for installed games
with an exe), and the finalize path confinement must reject an exe
outside the game dir and its prefix.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from unifideck.core.types.identifiers import validate_game_id
from unifideck.rpc.errors import RpcError
from unifideck.rpc.mixins.manual_install import (
    ManualInstallRPCMixin,
    _derive_game_id,
    _resolve_finalize_paths,
    _resolve_import_paths,
    _scan_candidate_exes,
    _validated_installer,
)
from unifideck.services.shortcut.launch_options import extract_store_id
from unifideck.stores.manual.drive import ensure_manual_drive
from unifideck.stores.manual.manual_store import ManualStore, _is_safe_to_delete
from unifideck.stores.manual.shortcut import ensure_manual_game_shortcut
from unifideck.stores.manual.state import (
    STATUS_INSTALLING,
    STATUS_READY,
    ManualGameRecord,
    load_records,
    save_records,
)


class _Bus:
    def __init__(self) -> None:
        self.events: list[tuple[Any, dict]] = []

    async def emit(self, event: Any, **kwargs: Any) -> None:
        self.events.append((event, kwargs))


class _Config:
    def __init__(self, state_file: Path, install_dir: Path) -> None:
        self._values = {
            "stores.manual.state_file": str(state_file),
            "stores.manual.install_dir": str(install_dir),
        }

    def get(self, key: str, default: Any = None) -> Any:
        return self._values.get(key, default)


def _store(tmp_path: Path) -> ManualStore:
    config = _Config(tmp_path / "manual_games.json", tmp_path / "root")
    return ManualStore(_Bus(), object(), plugin_dir=None, config=config)


def _record(tmp_path: Path, **overrides: Any) -> ManualGameRecord:
    fields: dict[str, Any] = {
        "game_id": "dark-forest-abc12345",
        "title": "Dark Forest",
        "installer_path": str(tmp_path / "setup.exe"),
        "install_path": str(tmp_path / "root" / "dark-forest-abc12345"),
    }
    fields.update(overrides)
    return ManualGameRecord(**fields)


# ── state file ────────────────────────────────────────────────────────


def test_state_roundtrip_and_malformed_rows_dropped(tmp_path: Path) -> None:
    path = tmp_path / "manual_games.json"
    record = _record(tmp_path)
    save_records(path, {record.game_id: record})

    raw = json.loads(path.read_text())
    raw["games"].append({"title": "no id"})  # malformed: game_id missing
    raw["games"].append("not even a dict")
    path.write_text(json.dumps(raw))

    loaded = load_records(path)
    assert list(loaded) == [record.game_id]
    assert loaded[record.game_id] == record


def test_state_unreadable_file_yields_empty(tmp_path: Path) -> None:
    path = tmp_path / "manual_games.json"
    path.write_text("{ not json")
    assert load_records(path) == {}


# ── library mapping ───────────────────────────────────────────────────


def test_get_library_reports_installed_with_exe(tmp_path: Path) -> None:
    store = _store(tmp_path)
    pending = _record(tmp_path)
    ready = _record(
        tmp_path,
        game_id="other-def67890",
        exe_path=str(tmp_path / "root" / "other" / "game.exe"),
        status=STATUS_READY,
    )

    async def run() -> None:
        await store.upsert_record(pending)
        await store.upsert_record(ready)
        games = {g.store_game_id: g for g in (await store.get_library() or [])}
        assert all(g.installed for g in games.values())
        assert all(g.store == "manual" for g in games.values())
        # Pending record launches its installer; ready one its exe.
        assert games[pending.game_id].exe_path == pending.installer_path
        assert games[ready.game_id].exe_path == ready.exe_path
        assert games[pending.game_id].metadata["manual_status"] == (
            STATUS_INSTALLING
        )

    asyncio.run(run())


# ── uninstall ─────────────────────────────────────────────────────────


def test_uninstall_removes_dir_record_and_emits(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record(tmp_path)
    game_dir = Path(record.install_path)
    game_dir.mkdir(parents=True)
    (game_dir / "game.exe").write_bytes(b"MZ")
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    store.prefix_path = lambda _gid: prefix  # type: ignore[method-assign]

    async def run() -> None:
        await store.upsert_record(record)
        result = await store.uninstall_game(record.game_id)
        assert result.success
        assert not await asyncio.to_thread(game_dir.exists)
        # delete_prefix not requested → prefix survives
        assert await asyncio.to_thread(prefix.exists)
        assert await store.get_record(record.game_id) is None

        events = store._bus.events  # type: ignore[attr-defined]
        assert [(e.value, k) for e, k in events] == [
            ("game_uninstalled", {"store": "manual", "game_id": record.game_id}),
        ]

    asyncio.run(run())


def test_uninstall_delete_prefix_and_missing_game(tmp_path: Path) -> None:
    store = _store(tmp_path)
    record = _record(tmp_path)
    prefix = tmp_path / "prefix"
    prefix.mkdir()
    store.prefix_path = lambda _gid: prefix  # type: ignore[method-assign]

    async def run() -> None:
        await store.upsert_record(record)
        result = await store.uninstall_game(record.game_id, delete_prefix=True)
        assert result.success
        assert not await asyncio.to_thread(prefix.exists)
        missing = await store.uninstall_game("nope")
        assert not missing.success

    asyncio.run(run())


def test_uninstall_never_deletes_user_managed_dirs(tmp_path: Path) -> None:
    """A game added from its own folder (outside ~/Games/Manual) is the
    user's — uninstall forgets it but leaves the files in place."""
    store = _store(tmp_path)
    game_dir = tmp_path / "Jocs" / "Slay"
    game_dir.mkdir(parents=True)
    (game_dir / "game.exe").write_bytes(b"MZ")
    # A stray ownership marker from an earlier build: it must be
    # DEFUSED on uninstall (cleanup's marker sweep would otherwise
    # treat the user's folder as plugin-created and delete it).
    marker = game_dir / ".unifideck_manifest.json"
    marker.write_text("{}")
    record = _record(tmp_path, install_path=str(game_dir))
    store.prefix_path = lambda _gid: tmp_path / "np"  # type: ignore[method-assign]

    async def run() -> None:
        await store.upsert_record(record)
        result = await store.uninstall_game(record.game_id)
        assert result.success
        assert await asyncio.to_thread(game_dir.exists)
        assert await asyncio.to_thread((game_dir / "game.exe").exists)
        assert not await asyncio.to_thread(marker.exists)
        assert await store.get_record(record.game_id) is None

    asyncio.run(run())


def test_safe_delete_guard_rejects_shallow_paths() -> None:
    assert not _is_safe_to_delete(Path("/"))
    assert not _is_safe_to_delete(Path("~"))
    assert not _is_safe_to_delete(Path.home())
    assert _is_safe_to_delete(Path.home() / "Games" / "Manual" / "x")


# ── id derivation + launch token ──────────────────────────────────────


def test_derived_id_is_valid_and_stable() -> None:
    installer = "/data/installers/setup.exe"
    game_id = _derive_game_id("Dark Forest: Édition Ultime!", installer)
    assert validate_game_id(game_id) == game_id
    assert game_id == _derive_game_id("Dark Forest: Édition Ultime!", installer)
    # Same title, different installer → different id.
    other = "/data/installers/b.exe"
    assert game_id != _derive_game_id("Dark Forest: Édition Ultime!", other)
    # Degenerate title still yields a usable slug.
    assert validate_game_id(_derive_game_id("!!!", installer))


def test_manual_launch_options_are_recognised() -> None:
    assert extract_store_id("manual:dark-forest-abc12345") == (
        "manual", "dark-forest-abc12345",
    )


def test_validated_installer_rejects_bad_paths(tmp_path: Path) -> None:
    good = tmp_path / "setup.exe"
    good.write_bytes(b"MZ")
    assert _validated_installer(str(good)) == good
    with pytest.raises(RpcError):
        _validated_installer(str(tmp_path / "missing.exe"))
    with pytest.raises(RpcError):
        _validated_installer(str(tmp_path))
    with pytest.raises(RpcError):
        _validated_installer("relative/setup.exe")


# ── finalize confinement ──────────────────────────────────────────────


def test_finalize_paths_confined_to_game_dir_or_prefix(tmp_path: Path) -> None:
    # The installer lives in its own folder: its directory is also an
    # allowed root (the already-installed-game case), so the "outside"
    # exe below must not share it.
    downloads = tmp_path / "downloads"
    downloads.mkdir()
    record = _record(tmp_path, installer_path=str(downloads / "setup.exe"))
    game_dir = Path(record.install_path)
    (game_dir / "bin").mkdir(parents=True)
    exe = game_dir / "bin" / "game.exe"
    exe.write_bytes(b"MZ")
    prefix = tmp_path / "prefix"
    c_exe = prefix / "drive_c" / "Game" / "game.exe"
    c_exe.parent.mkdir(parents=True)
    c_exe.write_bytes(b"MZ")
    outside = tmp_path / "elsewhere.exe"
    outside.write_bytes(b"MZ")

    got_exe, got_dir, rel = _resolve_finalize_paths(record, str(exe), prefix)
    assert (got_exe, got_dir, rel) == (str(exe), str(game_dir), "bin/game.exe")

    # An exe inside the prefix re-anchors install_path on its directory.
    got_exe, got_dir, rel = _resolve_finalize_paths(record, str(c_exe), prefix)
    assert (got_exe, got_dir, rel) == (str(c_exe), str(c_exe.parent), "game.exe")

    with pytest.raises(RpcError):
        _resolve_finalize_paths(record, str(outside), prefix)
    with pytest.raises(RpcError):
        _resolve_finalize_paths(record, str(game_dir / "gone.exe"), prefix)


def test_finalize_accepts_already_installed_game(tmp_path: Path) -> None:
    """Picking the game's own exe as "installer" adds an existing install."""
    game_dir = tmp_path / "Jocs" / "Slay the Princess"
    game_dir.mkdir(parents=True)
    exe = game_dir / "SlaythePrincess.exe"
    exe.write_bytes(b"MZ")
    record = _record(
        tmp_path,
        installer_path=str(exe),
        install_path=str(tmp_path / "root" / "slay"),
    )
    prefix = tmp_path / "prefix"

    got_exe, got_dir, rel = _resolve_finalize_paths(record, str(exe), prefix)
    assert (got_exe, got_dir, rel) == (str(exe), str(game_dir), exe.name)


# ── import (already-installed game) ───────────────────────────────────


def test_import_paths_anchor_on_the_exe_dir(tmp_path: Path) -> None:
    game_dir = tmp_path / "Jocs" / "Slay"
    game_dir.mkdir(parents=True)
    exe = game_dir / "Slay.exe"
    exe.write_bytes(b"MZ")

    got = _resolve_import_paths(str(exe), extra_roots=(tmp_path,))
    assert got == (str(exe), str(game_dir), "Slay.exe")

    # Not an .exe → rejected; outside every allowed root → rejected.
    other = game_dir / "readme.txt"
    other.write_text("x")
    with pytest.raises(RpcError):
        _resolve_import_paths(str(other), extra_roots=(tmp_path,))
    with pytest.raises(RpcError):
        _resolve_import_paths(str(exe), extra_roots=(tmp_path / "elsewhere",))


def test_manual_import_creates_a_ready_game(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    """IMPORT: record born ready, shortcut written, events emitted."""
    import unifideck.rpc.mixins.manual_install as mi

    monkeypatch.setattr(mi, "_extra_allowed_roots", lambda: (tmp_path,))
    store = _store(tmp_path)
    shortcut_svc = _ShortcutService()
    bus = _Bus()

    class _Host(ManualInstallRPCMixin):
        def __init__(self) -> None:
            self.registry = type(
                "R", (), {"get_store": staticmethod(lambda _s: store)},
            )()
            self.services = type("S", (), {"shortcut": shortcut_svc})()
            self.sync_service = None
            self.config = None
            self.bus = bus

    game_dir = tmp_path / "Jocs" / "Slay"
    game_dir.mkdir(parents=True)
    exe = game_dir / "Slay.exe"
    exe.write_bytes(b"MZ")

    async def run() -> None:
        result = await _Host().manual_import(str(exe), "Slay the Princess")
        assert result["success"] and result["app_id"] == -123456
        record = await store.get_record(result["game_id"])
        assert record is not None
        assert record.status == STATUS_READY
        assert record.exe_path == str(exe)
        assert record.install_path == str(game_dir)
        # Shortcut written + games.map row marked.
        assert shortcut_svc.writes == 1
        assert shortcut_svc.marked == [
            ("manual", result["game_id"], str(exe), str(game_dir)),
        ]
        # NO ownership marker in a user-managed folder: cleanup's marker
        # sweep deletes any directory carrying one, and this folder is
        # the user's (the sweep once deleted an imported game this way).
        assert not (game_dir / ".unifideck_manifest.json").exists()
        emitted = [e.value for e, _k in bus.events]
        # The launch request is the verification run: the game starts
        # once so the prefix gets created and the user sees it works.
        assert emitted == [
            "game_installed",
            "manual_install_launch_requested",
            "artwork_request",
        ]

    asyncio.run(run())


def test_manual_ensure_shortcut_rewrites_the_row(tmp_path: Path) -> None:
    """Re-writing the row after Steam's temp-shortcut flush erased it.

    A pending record's row points back at the INSTALLER (that is the
    game's pending action); a ready record's at its exe.
    """
    store = _store(tmp_path)
    shortcut_svc = _ShortcutService()

    class _Host(ManualInstallRPCMixin):
        def __init__(self) -> None:
            self.registry = type(
                "R", (), {"get_store": staticmethod(lambda _s: store)},
            )()
            self.services = type("S", (), {"shortcut": shortcut_svc})()
            self.sync_service = None
            self.config = None
            self.bus = _Bus()

    record = _record(tmp_path)  # status "installing", no exe yet

    async def run() -> None:
        await store.upsert_record(record)
        result = await _Host().manual_ensure_shortcut(record.game_id)
        assert result["success"] and result["app_id"] == -123456
        assert shortcut_svc.marked[-1] == (
            "manual", record.game_id,
            record.installer_path, record.install_path,
        )
        with pytest.raises(RpcError):
            await _Host().manual_ensure_shortcut("nope")

    asyncio.run(run())


# ── candidate scan for the post-install picker ────────────────────────


def test_scan_candidates_covers_d_and_c_drives(tmp_path: Path) -> None:
    record = _record(tmp_path)
    game_dir = Path(record.install_path)
    (game_dir / "bin").mkdir(parents=True)
    (game_dir / "bin" / "game.exe").write_bytes(b"MZ")
    (game_dir / "unins000.exe").write_bytes(b"MZ")  # noise-filtered

    prefix = tmp_path / "prefix"
    pf = prefix / "pfx" / "drive_c" / "Program Files" / "Foo"
    pf.mkdir(parents=True)
    (pf / "foo.exe").write_bytes(b"MZ")
    system = prefix / "pfx" / "drive_c" / "windows"
    system.mkdir(parents=True)
    (system / "explorer.exe").write_bytes(b"MZ")  # system dir — skipped
    # Wine stock content inside Program Files — the iexplore/wmplayer
    # noise that used to flood the C: scan.
    stock = prefix / "pfx" / "drive_c" / "Program Files" / "Internet Explorer"
    stock.mkdir(parents=True)
    (stock / "iexplore.exe").write_bytes(b"MZ")
    (pf / "regedit.exe").write_bytes(b"MZ")  # stock exe name anywhere

    found = _scan_candidate_exes(record, prefix)
    by_path = {c["rel"]: c for c in found}
    assert set(by_path) == {"bin/game.exe", "Program Files/Foo/foo.exe"}
    assert by_path["bin/game.exe"]["in_prefix"] is False
    assert by_path["Program Files/Foo/foo.exe"]["in_prefix"] is True


# ── C:-installs: uninstall always reclaims the prefix ─────────────────


def test_uninstall_deletes_prefix_when_game_lives_inside_it(
    tmp_path: Path,
) -> None:
    store = _store(tmp_path)
    prefix = tmp_path / "prefixes" / "dark-forest-abc12345"
    c_game = prefix / "pfx" / "drive_c" / "Games" / "Dark"
    c_game.mkdir(parents=True)
    (c_game / "game.exe").write_bytes(b"MZ")
    record = _record(tmp_path, install_path=str(c_game))
    store.prefix_path = lambda _gid: prefix  # type: ignore[method-assign]

    async def run() -> None:
        await store.upsert_record(record)
        # delete_prefix NOT requested — forced anyway: the game lives
        # inside the prefix, keeping it would leak the whole install.
        result = await store.uninstall_game(record.game_id)
        assert result.success
        assert not await asyncio.to_thread(prefix.exists)

    asyncio.run(run())


# ── shortcut helper ───────────────────────────────────────────────────


class _ShortcutService:
    def __init__(self) -> None:
        self.data: dict[str, Any] = {"shortcuts": {}}
        self.writes = 0
        self.marked: list[tuple[str, str, str, str]] = []

    def generate_app_id(self, launcher: str, identity: str) -> int:
        return -123456

    async def read_shortcuts(self, *, from_disk: bool = False) -> dict[str, Any]:
        return dict(self.data)

    async def write_shortcuts(self, data: dict[str, Any]) -> None:
        self.data = dict(data)
        self.writes += 1

    async def mark_installed(
        self, store: str, game_id: str, exe_path: str, install_path: str,
    ) -> int:
        self.marked.append((store, game_id, exe_path, install_path))
        return -123456


def test_ensure_manual_game_shortcut_creates_then_reuses(tmp_path: Path) -> None:
    svc = _ShortcutService()

    async def run() -> None:
        appid = await ensure_manual_game_shortcut(
            svc,
            game_id="dark-forest-abc12345",
            title="Dark Forest",
            install_path=str(tmp_path),
            plugin_dir=str(tmp_path / "plugin"),
        )
        assert appid == -123456
        entry = svc.data["shortcuts"]["0"]
        assert entry["AppName"] == "Dark Forest"
        assert entry["LaunchOptions"] == "manual:dark-forest-abc12345"
        assert entry["tags"] == {"0": "Unifideck", "1": "manual", "2": ""}
        assert not entry["IsHidden"]

        again = await ensure_manual_game_shortcut(
            svc,
            game_id="dark-forest-abc12345",
            title="Dark Forest",
            install_path=str(tmp_path),
            plugin_dir=str(tmp_path / "plugin"),
        )
        assert again == -123456
        assert svc.writes == 1  # reused, not duplicated

    asyncio.run(run())


# ── drive mapping ─────────────────────────────────────────────────────


def test_ensure_manual_drive_creates_and_repoints(tmp_path: Path) -> None:
    prefix = tmp_path / "prefix"
    (prefix / "pfx").mkdir(parents=True)
    games_a = tmp_path / "games-a"
    games_a.mkdir()
    games_b = tmp_path / "games-b"
    games_b.mkdir()

    assert ensure_manual_drive(prefix, games_a)
    link = prefix / "pfx" / "dosdevices" / "d:"
    assert link.is_symlink() and link.resolve() == games_a.resolve()

    # Idempotent, and repoints a stale link.
    assert ensure_manual_drive(prefix, games_a)
    assert ensure_manual_drive(prefix, games_b)
    assert link.resolve() == games_b.resolve()

    # Never destroys a real directory occupying the letter.
    link.unlink()
    link.mkdir()
    assert not ensure_manual_drive(prefix, games_a)
    assert link.is_dir()

    # A missing target maps nothing.
    assert not ensure_manual_drive(prefix, tmp_path / "missing")


# ── uninstall drops the shortcut outright ─────────────────────────────


class _EventsHost:
    """Just enough ShortcutService surface for the uninstall handler."""

    def __init__(self, mark_result: int | None) -> None:
        self._mark_result = mark_result
        self.marked: list[tuple[str, str]] = []
        self.removed: list[int] = []

    async def mark_uninstalled(self, store: str, game_id: str) -> int | None:
        self.marked.append((store, game_id))
        return self._mark_result

    async def remove_game(self, app_id: int) -> bool:
        self.removed.append(app_id)
        return True


def test_uninstall_handler_removes_manual_shortcut_entirely() -> None:
    from unifideck.services.shortcut.events import EventsMixin

    handler = EventsMixin._on_game_uninstalled

    async def run() -> None:
        # Manual game: mark + full removal (no "Not Installed" tile with
        # an Install button that cannot work).
        host = _EventsHost(mark_result=-123)
        await handler(host, store="manual", game_id="dark-forest-abc12345")
        assert host.marked == [("manual", "dark-forest-abc12345")]
        assert host.removed == [-123]

        # Other stores keep the shortcut (the user still owns the game).
        host = _EventsHost(mark_result=-456)
        await handler(host, store="gog", game_id="123")
        assert host.removed == []

        # No shortcut found → nothing to remove.
        host = _EventsHost(mark_result=None)
        await handler(host, store="manual", game_id="gone")
        assert host.removed == []

    asyncio.run(run())


# ── auth contract ─────────────────────────────────────────────────────


def test_auth_surface_is_a_no_op(tmp_path: Path) -> None:
    store = _store(tmp_path)

    async def run() -> None:
        assert await store.is_available()
        assert (await store.start_auth()).success
        assert (await store.complete_auth()).success
        assert (await store.logout()).success
        assert await store.check_for_updates() == []
        # The DownloadWorker calls with a positional install_path — a
        # stray Install press must fail cleanly, never TypeError.
        result = await store.install_game("x", "/some/path", progress_cb=None)
        assert not result.success

    asyncio.run(run())
