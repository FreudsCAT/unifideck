import os
import json
import asyncio
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

from unifideck.services.cloud_save import gog_cloud_api
from unifideck.services.cloud_save.path_resolver import WinePrefixResolver
from unifideck.services.cloud_save.epic_strategy import EpicCloudSaveStrategy
from unifideck.services.cloud_save.gog_strategy import GOGCloudSaveStrategy
from unifideck.services.cloud_save.service import CloudSaveService
from unifideck.core.types import Result

@pytest.fixture
def mock_config():
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "cloud.enabled": True,
        "cloud.tolerance_seconds": 2.0,
        "cloud.sync_wait_timeout_seconds": 5.0,
        "cloud_saves.remote_root": "/tmp/test_remote_root",
        "paths.data_dir": "/tmp/test_data_dir",
        "games.amazon123.title": "My Amazon Game",
    }.get(key, default)
    config.get_bool.side_effect = lambda key, default=True: {
        "cloud.enabled": True,
    }.get(key, default)
    return config

@pytest.fixture
def mock_event_bus():
    bus = MagicMock()
    # sync_down/sync_up await bus.emit(...) for the CLOUD_SYNC_* completion
    # events, so emit must be awaitable.
    bus.emit = AsyncMock()
    return bus

def test_wine_prefix_resolver(tmp_path):
    # Setup a dummy prefix registry
    prefix = tmp_path / "test_prefix"
    prefix.mkdir()
    
    # Write a dummy user.reg file
    user_reg = prefix / "user.reg"
    user_reg.write_text(
        '[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\Shell Folders]\n'
        '"AppData"="C:\\\\users\\\\steamuser\\\\AppData\\\\Roaming"\n'
        '"Local AppData"="C:\\\\users\\\\steamuser\\\\AppData\\\\Local"\n'
    )

    # Resolve
    resolved = WinePrefixResolver.resolve_path(
        cloud_save_folder="{AppData}/GameName/Saves",
        prefix_path=str(prefix),
        install_path="/tmp/install",
        account_id="ed4745",
    )
    # Epic's {AppData} token resolves to %LOCALAPPDATA% (AppData/Local),
    # NOT %APPDATA% (Roaming) — that's where Epic games actually save.
    assert "drive_c/users/steamuser/AppData/Local/GameName/Saves" in resolved


def test_wine_prefix_resolver_epicid_uses_account_id(tmp_path):
    """{EpicID} must resolve to the Epic ACCOUNT id, not the game id —
    Vampire Survivors / Brotato namespace saves under the account id, and
    using the game id pointed the sync at a folder the game never reads."""
    prefix = tmp_path / "vs_prefix"
    prefix.mkdir()
    (prefix / "user.reg").write_text(
        '[Software\\\\Microsoft\\\\Windows\\\\CurrentVersion\\\\Explorer\\\\Shell Folders]\n'
        '"AppData"="C:\\\\users\\\\steamuser\\\\AppData\\\\Roaming"\n'
        '"Local AppData"="C:\\\\users\\\\steamuser\\\\AppData\\\\Local"\n'
    )
    resolved = WinePrefixResolver.resolve_path(
        # Real Vampire Survivors template: {AppData}/../Roaming redirects
        # Local→Roaming, {EpicID} is the account-id subfolder.
        cloud_save_folder="{AppData}/../Roaming/Vampire_Survivors_EGS/{EpicID}/",
        prefix_path=str(prefix),
        account_id="ed4745dba2c6492d851bcb554dc98d60",
    )
    assert resolved.endswith(
        "AppData/Roaming/Vampire_Survivors_EGS/ed4745dba2c6492d851bcb554dc98d60"
    )
    assert "game" not in resolved.rsplit("/", 1)[-1]  # not a game-id subfolder

@pytest.mark.asyncio
async def test_epic_strategy_sync(tmp_path, mock_config):
    local_save_root = str(tmp_path / "saves")
    os.makedirs(local_save_root, exist_ok=True)
    
    # Mock legendary CLI response
    strategy = EpicCloudSaveStrategy(local_save_root, mock_config)
    strategy.legendary_bin = "mock_legendary"
    # Keep hermetic: don't read the dev machine's ~/.config/legendary, and
    # don't spin up LegendaryCore for the validating fallback.
    strategy._get_account_id = MagicMock(return_value="acct123")
    strategy._legendary_save_path = MagicMock(return_value=None)

    with patch("subprocess.run") as mock_run, \
         patch("asyncio.create_subprocess_exec") as mock_exec:
         
        # Mock legendary info JSON response
        mock_info_res = MagicMock()
        # legendary nests these under "game" / "install" (real shape).
        mock_info_res.stdout = json.dumps({
            "game": {"cloud_save_folder": "{AppData}/GameName/Saves"},
            "install": {"install_path": "/tmp/install"},
        })
        mock_run.return_value = mock_info_res
        
        # Mock legendary sync-saves subprocess
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"Success", b"")
        mock_exec.return_value = mock_proc
        
        # Test path resolution
        save_dir = strategy.get_local_save_dir("game123")
        assert save_dir is not None
        assert "GameName/Saves" in save_dir
        
        # Test sync_down — default (on-launch) must NOT force a download,
        # so newer local saves are never silently overwritten.
        success = await strategy.sync_down("game123")
        assert success is True
        assert "--force-download" not in mock_exec.call_args.args

        # Test sync_up
        # Write dummy save to satisfy empty check
        os.makedirs(save_dir, exist_ok=True)
        with open(os.path.join(save_dir, "save.bin"), "w") as f:
            f.write("data")
        success = await strategy.sync_up("game123")
        assert success is True

@pytest.mark.asyncio
async def test_gog_strategy_sync(tmp_path, mock_config):
    local_save_root = str(tmp_path / "saves")
    os.makedirs(local_save_root, exist_ok=True)
    
    strategy = GOGCloudSaveStrategy(local_save_root, mock_config)
    strategy.gogdl_bin = "mock_gogdl"

    # Mock token conversion directly
    strategy._convert_gog_token = MagicMock(return_value="/tmp/mock_auth.json")
    # Resolve to a real dir — the staging fallback was removed, so
    # get_local_save_dir returns None without a prefix; provide a location.
    strategy.get_local_save_dir = MagicMock(return_value=str(tmp_path / "gogsave"))

    # Mock subprocess
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        mock_proc = AsyncMock()
        mock_proc.returncode = 0
        mock_proc.communicate.return_value = (b"12345.6789", b"")
        mock_exec.return_value = mock_proc
        
        # Test sync_down
        success = await strategy.sync_down("gog123")
        assert success is True
        assert strategy._get_saved_timestamp("gog123") == "12345.6789"

@pytest.mark.asyncio
async def test_cloud_save_service_orchestration(tmp_path, mock_event_bus, mock_config):
    local_save_root = str(tmp_path / "saves")
    os.makedirs(local_save_root, exist_ok=True)
    
    # Instantiate service
    service = CloudSaveService(
        bus=mock_event_bus,
        local_save_root=local_save_root,
        cloud_root="/tmp/test_remote_root",
        config=mock_config
    )
    
    # Mock strategies
    mock_epic = MagicMock()
    mock_epic.sync_down = AsyncMock(return_value=True)
    mock_epic.sync_up = AsyncMock(return_value=True)
    mock_epic.get_local_save_dir.return_value = "/tmp/test_epic_save"
    
    service._strategies["epic"] = mock_epic
    
    # Mock fallback sync Mixin calls
    with patch.object(CloudSaveService, "_sync_down_locked", return_value=Result(success=True)), \
         patch.object(CloudSaveService, "_sync_up_locked", return_value=Result(success=True)), \
         patch.object(CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None)):
         
        # Verify custom path routing
        assert service.get_local_save_dir("epic", "game123") == "/tmp/test_epic_save"
        
        # Test sync_down runs both strategy and fallback (force defaults False)
        res_down = await service.sync_down("epic", "game123")
        assert res_down.success is True
        mock_epic.sync_down.assert_called_once_with("game123", False)
        
        # Test sync_up runs both strategy and fallback
        res_up = await service.sync_up("epic", "game123")
        assert res_up.success is True
        mock_epic.sync_up.assert_called_once_with("game123")

def test_amazon_prefix_auto_detect(tmp_path, mock_config, mock_event_bus):
    # Setup mock wine prefix
    prefix_dir = tmp_path / "prefixes" / "amazon123"
    drive_c = prefix_dir / "pfx" / "drive_c"
    saved_games = drive_c / "users" / "steamuser" / "Saved Games"
    game_save = saved_games / "My Amazon Game"
    os.makedirs(game_save, exist_ok=True)
    
    service = CloudSaveService(
        bus=mock_event_bus,
        local_save_root=str(tmp_path / "saves"),
        cloud_root="/tmp/test_remote",
        config=mock_config
    )
    
    # Resolve
    resolved = service.get_local_save_dir("amazon", "amazon123")
    assert resolved == str(game_save)

def test_global_cloud_root_default():
    from unifideck.services.bootstrap.paths import ServicePaths
    
    config = MagicMock()
    config.get.side_effect = lambda key, default=None: {
        "paths.data_dir": "/tmp/unifideck_data",
        "paths.steam_root": "/tmp/steam",
    }.get(key, default)
    
    with patch("unifideck.steam.steam_user.get_active_steam_user", return_value="123456"):
        paths = ServicePaths.from_config(config)
        assert paths.cloud_root == str(Path("~/Save Games Backup").expanduser())


# ── Cloud-save wipe-prevention guardrails (shared safety module) ──────────
from unifideck.services.cloud_save import safety  # noqa: E402
from unifideck.services.cloud_save.safety import SaveConflictError  # noqa: E402


def _settings_only_dir(base, name):
    """A save dir with only settings/config (the reset-prefix state that
    previously wiped the cloud): an empty ``gamesaves/`` and a couple of
    ``*.settings`` files, but no real save data."""
    d = base / name
    (d / "gamesaves").mkdir(parents=True)
    (d / "profile.settings").write_text("cfg")
    (d / "profile.settings.bak").write_text("cfg")
    return d


def test_has_save_data_distinguishes_saves_from_settings(tmp_path):
    settings_only = _settings_only_dir(tmp_path, "settings_only")
    assert safety.has_save_data(settings_only) is False
    # A real save (top-level) counts — protects single-file-save games.
    (settings_only / "slot1.sav").write_text("save")
    assert safety.has_save_data(settings_only) is True
    # A real save in a subdir (Witcher's gamesaves/) counts too.
    sub = tmp_path / "subdir_saves"
    (sub / "gamesaves").mkdir(parents=True)
    (sub / "gamesaves" / "CheckPoint.sav").write_text("save")
    assert safety.has_save_data(sub) is True


def test_snapshot_backup_is_versioned_and_rotates(tmp_path, monkeypatch):
    monkeypatch.setattr(safety, "_BACKUPS_ROOT", tmp_path / "backups")
    monkeypatch.setattr(safety, "_KEEP_BACKUPS", 2)
    src = tmp_path / "saves"
    (src / "gamesaves").mkdir(parents=True)
    (src / "gamesaves" / "a.sav").write_text("x")
    for ts in (1000, 2000, 3000):
        out = safety.snapshot_backup(src, "gog", "g1", now=ts)
        assert out is not None and (out / "gamesaves" / "a.sav").is_file()
    kept = sorted((tmp_path / "backups" / "gog" / "g1").iterdir())
    assert [p.name for p in kept] == ["2000", "3000"]  # oldest rotated out


@pytest.mark.asyncio
async def test_gog_sync_up_blocks_and_never_calls_cli(tmp_path, mock_config, monkeypatch):
    monkeypatch.setattr(safety, "_BACKUPS_ROOT", tmp_path / "backups")
    save_dir = _settings_only_dir(tmp_path, "gog_saves")
    strategy = GOGCloudSaveStrategy(str(tmp_path), mock_config)
    strategy.gogdl_bin = "mock_gogdl"
    strategy._convert_gog_token = MagicMock(return_value="/tmp/mock_auth.json")
    strategy.get_local_save_dir = MagicMock(return_value=str(save_dir))
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with pytest.raises(SaveConflictError) as exc:
            await strategy.sync_up("gog123")
        mock_exec.assert_not_called()  # destructive gogdl push never ran
    assert exc.value.hard is True  # empty upload is a HARD error, never a choice


@pytest.mark.asyncio
async def test_epic_sync_up_blocks_and_never_calls_cli(tmp_path, mock_config, monkeypatch):
    monkeypatch.setattr(safety, "_BACKUPS_ROOT", tmp_path / "backups")
    save_dir = _settings_only_dir(tmp_path, "epic_saves")
    strategy = EpicCloudSaveStrategy(str(tmp_path), mock_config)
    strategy.legendary_bin = "mock_legendary"
    strategy.get_local_save_dir = MagicMock(return_value=str(save_dir))
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        with pytest.raises(SaveConflictError) as exc:
            await strategy.sync_up("epic123")
        mock_exec.assert_not_called()  # destructive legendary push never ran
    assert exc.value.hard is True  # empty upload is a HARD error, never a choice


@pytest.mark.asyncio
async def test_service_soft_conflict_opens_modal(
    tmp_path, mock_event_bus, mock_config, monkeypatch,
):
    monkeypatch.setattr(safety, "_BACKUPS_ROOT", tmp_path / "backups")
    service = CloudSaveService(
        bus=mock_event_bus,
        local_save_root=str(tmp_path / "saves"),
        cloud_root=None,  # skip the local-backup mixin; isolate the strategy
        config=mock_config,
    )
    mock_event_bus.emit = AsyncMock()
    blocking = MagicMock()
    blocking.sync_up = AsyncMock(
        side_effect=SaveConflictError(
            "local_saves_regressed",  # local has saves but lost some
            {"file_count": 2, "timestamp": 1.0, "total_bytes": 99},
            store="gog", game_id="g1", hard=False,
        ),
    )
    service._strategies["gog"] = blocking
    with patch.object(
        CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None),
    ):
        res = await service.sync_up("gog", "g1")
    # A soft conflict is NOT a launch failure; it surfaces the pick modal
    # (LAUNCHER_STAGE + retry-sync) with both snapshots — never a wipe.
    assert res.success is True
    evt = mock_event_bus.emit.await_args
    assert evt.kwargs.get("action", {}).get("verb") == "retry-sync"
    assert evt.kwargs.get("local_snapshot", {}).get("file_count") == 2
    assert "remote_snapshot" in evt.kwargs


@pytest.mark.asyncio
async def test_service_hard_block_emits_error_not_modal(
    tmp_path, mock_event_bus, mock_config,
):
    service = CloudSaveService(
        bus=mock_event_bus,
        local_save_root=str(tmp_path / "saves"),
        cloud_root=None,
        config=mock_config,
    )
    mock_event_bus.emit = AsyncMock()
    blocking = MagicMock()
    blocking.sync_up = AsyncMock(
        side_effect=SaveConflictError(
            "no_local_save_data",
            {"file_count": 0, "timestamp": 0, "total_bytes": 0},
            store="gog", game_id="g1", hard=True,
        ),
    )
    service._strategies["gog"] = blocking
    with patch.object(
        CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None),
    ):
        res = await service.sync_up("gog", "g1")
    # HARD block (empty) → plain title+body toast, NEVER a "keep local" pick.
    # Severity is a warning (an expected skip when there are no local saves,
    # not a failure) and the message is split into a short title + body.
    assert res.success is True
    evt = mock_event_bus.emit.await_args
    assert "action" not in evt.kwargs  # no retry-sync → no pick modal
    assert evt.kwargs.get("severity") == "warning"
    assert evt.kwargs.get("i18n_title_key") == "cloudSave.uploadSkippedTitle"
    assert evt.kwargs.get("i18n_key") == "cloudSave.uploadSkippedBody"


# ── Forced pull (explicit "Use Cloud") ────────────────────────────────────


@pytest.mark.asyncio
async def test_epic_sync_down_force_adds_force_download(tmp_path, mock_config):
    """force=True must add --force-download so legendary pulls even when the
    local save is newer/same-age (the only way "Use Cloud" can override)."""
    strategy = EpicCloudSaveStrategy(str(tmp_path), mock_config)
    strategy.legendary_bin = "mock_legendary"
    strategy.get_local_save_dir = MagicMock(return_value=str(tmp_path / "save"))
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b"", b"Downloading remote savegame...")
        mock_exec.return_value = proc
        assert await strategy.sync_down("epic123", force=True) is True
        assert "--force-download" in mock_exec.call_args.args


@pytest.mark.asyncio
async def test_gog_sync_down_force_uses_ts_zero(tmp_path, mock_config):
    """force=True must pull a full copy (ts=0) even when a recent last-sync
    timestamp would otherwise make gogdl skip the download."""
    strategy = GOGCloudSaveStrategy(str(tmp_path), mock_config)
    strategy.gogdl_bin = "mock_gogdl"
    strategy._convert_gog_token = MagicMock(return_value="/tmp/auth.json")
    save_dir = tmp_path / "save"
    (save_dir).mkdir()
    (save_dir / "slot.sav").write_text("data")  # local has saves → not the empty self-heal
    strategy.get_local_save_dir = MagicMock(return_value=str(save_dir))
    strategy._get_saved_timestamp = MagicMock(return_value="99999.0")
    with patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b"12345.6", b"")
        mock_exec.return_value = proc
        assert await strategy.sync_down("gog123", force=True) is True
        args = mock_exec.call_args.args
        assert "--ts" in args and args[args.index("--ts") + 1] == "0"


@pytest.mark.asyncio
async def test_service_sync_down_forwards_force(tmp_path, mock_event_bus, mock_config):
    service = CloudSaveService(
        bus=mock_event_bus, local_save_root=str(tmp_path / "saves"),
        cloud_root=None, config=mock_config,
    )
    strat = MagicMock()
    strat.sync_down = AsyncMock(return_value=True)
    service._strategies["epic"] = strat
    with patch.object(
        CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None),
    ):
        await service.sync_down("epic", "g1", force=True)
    strat.sync_down.assert_called_once_with("g1", True)


@pytest.mark.asyncio
async def test_dispatch_retry_sync_down_forces_pull():
    """The retry-sync 'sync_down' phase is only reached via 'Use Cloud', so it
    must force; 'sync_up' stays unforced."""
    from unifideck.actions.dispatch import _dispatch_retry_sync
    cloudsave = MagicMock()
    cloudsave.sync_down = AsyncMock(return_value=Result(success=True))
    cloudsave.sync_up = AsyncMock(return_value=Result(success=True))

    down = MagicMock(args=["epic", "g1", "sync_down"])
    await _dispatch_retry_sync(down, cloudsave)
    cloudsave.sync_down.assert_called_once_with("epic", "g1", force=True)

    up = MagicMock(args=["epic", "g1", "sync_up"])
    await _dispatch_retry_sync(up, cloudsave)
    cloudsave.sync_up.assert_called_once_with("epic", "g1")


# ── GOG dual-source save dir (Auto Cloud vs SDK IStorage) ─────────────────


def _stub_autocloud(monkeypatch):
    # Avoid network: one Auto-Cloud location (Documents\MyGame).
    monkeypatch.setattr(
        gog_cloud_api, "fetch_gog_save_locations",
        lambda cid: ["<?DOCUMENTS?>\\MyGame"],
    )


def test_gog_pick_prefers_autocloud_when_it_has_saves(tmp_path, monkeypatch):
    _stub_autocloud(monkeypatch)
    drive_c = tmp_path / "pfx" / "drive_c"
    doc = drive_c / "users" / "steamuser" / "Documents" / "MyGame"
    doc.mkdir(parents=True)
    (doc / "slot.sav").write_text("SAVE" * 50)
    assert gog_cloud_api.pick_gog_save_dir("CID", drive_c) == doc


def test_gog_pick_uses_sdk_istorage_when_autocloud_empty(tmp_path, monkeypatch):
    _stub_autocloud(monkeypatch)
    drive_c = tmp_path / "pfx" / "drive_c"
    sdk = (
        drive_c / "users" / "steamuser" / "AppData" / "Local"
        / "GOG.com" / "Galaxy" / "Applications" / "CID" / "Storage"
    )
    sdk.mkdir(parents=True)
    (sdk / "save.dat").write_text("DATA" * 50)
    assert gog_cloud_api.pick_gog_save_dir("CID", drive_c) == sdk


def test_gog_pick_falls_back_to_first_autocloud_when_none_on_disk(tmp_path, monkeypatch):
    _stub_autocloud(monkeypatch)
    drive_c = tmp_path / "pfx" / "drive_c"
    (drive_c / "users" / "steamuser").mkdir(parents=True)
    chosen = gog_cloud_api.pick_gog_save_dir("CID", drive_c)
    assert chosen == drive_c / "users" / "steamuser" / "Documents" / "MyGame"


# ── ~/Save Games Backup is WRITE-ONLY (never pulled from) ─────────────────


def _wo_service(tmp_path, bus, cfg, local_dir):
    svc = CloudSaveService(
        bus, str(tmp_path / "saves"),
        cloud_root=str(tmp_path / "backup"), config=cfg,
    )
    strat = MagicMock()
    strat.sync_down = AsyncMock(return_value=True)
    strat.sync_up = AsyncMock(return_value=True)
    strat.get_local_save_dir.return_value = str(local_dir)
    svc._strategies["gog"] = strat
    return svc


@pytest.mark.asyncio
async def test_mirror_written_on_sync_down(tmp_path, mock_event_bus, mock_config):
    local = tmp_path / "local"
    local.mkdir()
    (local / "save.dat").write_text("DATA" * 50)
    svc = _wo_service(tmp_path, mock_event_bus, mock_config, local)
    with patch.object(CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None)):
        res = await svc.sync_down("gog", "g1", force=True)
    assert res.success is True
    assert (tmp_path / "backup" / "gog" / "g1" / "save.dat").is_file()


@pytest.mark.asyncio
async def test_empty_local_never_wipes_mirror(tmp_path, mock_event_bus, mock_config):
    mirror = tmp_path / "backup" / "gog" / "g1"
    mirror.mkdir(parents=True)
    (mirror / "old.dat").write_text("OLD" * 50)
    local = tmp_path / "local"
    local.mkdir()  # empty
    svc = _wo_service(tmp_path, mock_event_bus, mock_config, local)
    with patch.object(CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None)):
        await svc.sync_down("gog", "g1", force=True)
    assert (mirror / "old.dat").is_file()  # backup preserved


@pytest.mark.asyncio
async def test_sync_down_never_pulls_from_mirror(tmp_path, mock_event_bus, mock_config):
    mirror = tmp_path / "backup" / "gog" / "g1"
    mirror.mkdir(parents=True)
    (mirror / "cloud.dat").write_text("CLOUD" * 50)
    local = tmp_path / "local"
    local.mkdir()  # strategy is a no-op; local stays empty
    svc = _wo_service(tmp_path, mock_event_bus, mock_config, local)
    with patch.object(CloudSaveService, "_acquire_sync_lock", return_value=(MagicMock(), None)):
        await svc.sync_down("gog", "g1", force=True)
    assert not (local / "cloud.dat").exists()  # never restored from the mirror


@pytest.mark.asyncio
async def test_unresolved_when_no_real_location(tmp_path, mock_event_bus, mock_config):
    # No prefix → the strategy resolves NO real location (returns None). The
    # staging fallback is gone, so status must show unresolved + no local saves
    # (we never read a staging dir, even one with leftover files).
    saves_root = tmp_path / "saves"
    # leftover staging files exist but must be ignored entirely
    staging = saves_root / "gog" / "g1"
    staging.mkdir(parents=True)
    (staging / "old.sav").write_text("OLD" * 50)
    svc = CloudSaveService(
        mock_event_bus, str(saves_root), cloud_root=None, config=mock_config,
    )
    svc._strategies["gog"].get_local_save_dir = lambda gid: None
    svc._real_cloud_info = AsyncMock(return_value=None)
    st = await svc.get_cloud_status("gog", "g1")
    assert st["save_path"] is None
    assert st["save_path_resolved"] is False
    assert st["has_local_saves"] is False
    assert st["local_snapshot"] == {}


# ── GOG forced pull does a CLEAN download (clears local first) ─────────────


@pytest.mark.asyncio
async def test_gog_force_pull_clears_local_first(tmp_path, mock_config):
    # gogdl skips cloud-only files when local is non-empty ("conflict"); a
    # forced "Use Cloud" pull must clear local first so the full set downloads.
    local = tmp_path / "save"
    local.mkdir()
    (local / "stale.sav").write_text("STALE")
    s = GOGCloudSaveStrategy(str(tmp_path / "root"), mock_config)
    s._convert_gog_token = MagicMock(return_value="/tmp/auth.json")
    s.get_local_save_dir = MagicMock(return_value=str(local))
    s.gogdl_bin = "mock_gogdl"
    with patch("unifideck.services.cloud_save.safety.snapshot_backup"), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b"1.0", b"")
        mock_exec.return_value = proc
        await s.sync_down("g1", force=True)
    assert not (local / "stale.sav").exists()  # cleared before the clean pull


@pytest.mark.asyncio
async def test_gog_normal_pull_keeps_existing_saves(tmp_path, mock_config):
    # A non-forced pull with REAL local saves must NOT clear them.
    local = tmp_path / "save"
    local.mkdir()
    (local / "keep.sav").write_text("REAL-SAVE-DATA")
    s = GOGCloudSaveStrategy(str(tmp_path / "root"), mock_config)
    s._convert_gog_token = MagicMock(return_value="/tmp/auth.json")
    s.get_local_save_dir = MagicMock(return_value=str(local))
    s.gogdl_bin = "mock_gogdl"
    with patch("unifideck.services.cloud_save.safety.snapshot_backup"), \
         patch("asyncio.create_subprocess_exec") as mock_exec:
        proc = AsyncMock()
        proc.returncode = 0
        proc.communicate.return_value = (b"1.0", b"")
        mock_exec.return_value = proc
        await s.sync_down("g1", force=False)
    assert (local / "keep.sav").exists()  # preserved


def test_gog_cloud_summary_counts_only_active_prefix():
    # GOG cloud storage namespaces objects by location name. A game can carry
    # a stale older prefix (``saves/``) alongside the live one (``__default/``).
    # gogdl materializes only ONE locally, so the reported cloud count must be
    # the newest prefix group's count (matching local) — NOT every object.
    objects = [
        {"name": "__default/a.sav", "last_modified": "2026-06-08T18:00:00+00:00"},
        {"name": "__default/b.sav", "last_modified": "2026-06-08T18:01:00+00:00"},
        # our own manifest is never a save file:
        {"name": "__default/.unifideck_sync.json", "last_modified": "2026-06-08T18:02:00+00:00"},
        {"name": "saves/old1.sav", "last_modified": "2026-03-29T20:00:00+00:00"},
        {"name": "saves/old2.sav", "last_modified": "2026-03-29T20:01:00+00:00"},
        {"name": "saves/old3.sav", "last_modified": "2026-03-29T20:02:00+00:00"},
    ]
    info = gog_cloud_api.summarize_cloud_objects(objects)
    assert info["file_count"] == 2  # __default's two real files, not 5
    assert info["has_saves"] is True
    # timestamp is the active group's newest (Jun 8 b.sav, not the manifest)
    from datetime import datetime
    expected = datetime.fromisoformat("2026-06-08T18:01:00+00:00").astimezone().timestamp()
    assert info["timestamp"] == expected


def test_gog_cloud_summary_empty_and_flat():
    empty = gog_cloud_api.summarize_cloud_objects([])
    assert empty["file_count"] == 0 and empty["has_saves"] is False
    flat = gog_cloud_api.summarize_cloud_objects(
        [{"name": "solo.sav", "last_modified": "2026-01-01T00:00:00+00:00"}]
    )
    assert flat["file_count"] == 1 and flat["has_saves"] is True


# ── Manual pull/push are fire-and-forget (don't block the RPC) ─────────────


@pytest.mark.asyncio
async def test_cloud_save_pull_is_fire_and_forget(mock_event_bus):
    # cloud_save_pull must return immediately ({"started": True}) and NOT block
    # on the (slow) sync — otherwise the RPC client times out and shows a false
    # failure even when the download succeeds.
    from unifideck.rpc.mixins.cloud_save import CloudSaveRPCMixin, _SYNC_TASKS
    started = asyncio.Event()
    done = asyncio.Event()

    async def slow_sync_down(store, game_id, force=False):
        started.set()
        await asyncio.sleep(0.05)
        done.set()
        return Result(success=True)

    svc = MagicMock()
    svc.sync_down = slow_sync_down

    class Host(CloudSaveRPCMixin):
        def __init__(self):
            self.services = MagicMock(cloudsave=svc)

    res = await Host().cloud_save_pull("gog", "g1", True)
    assert res == {"started": True}          # returned before the sync finished
    assert not done.is_set()                 # sync still running in background
    await asyncio.wait_for(started.wait(), 1)
    await asyncio.wait_for(done.wait(), 1)   # it does complete in the background
