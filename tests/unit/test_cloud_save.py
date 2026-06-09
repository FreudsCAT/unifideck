import os
import json
import pytest
from unittest.mock import MagicMock, AsyncMock, patch
from pathlib import Path

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
    return MagicMock()

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
        epic_id="game123"
    )
    # Epic's {AppData} token resolves to %LOCALAPPDATA% (AppData/Local),
    # NOT %APPDATA% (Roaming) — that's where Epic games actually save.
    assert "drive_c/users/steamuser/AppData/Local/GameName/Saves" in resolved

@pytest.mark.asyncio
async def test_epic_strategy_sync(tmp_path, mock_config):
    local_save_root = str(tmp_path / "saves")
    os.makedirs(local_save_root, exist_ok=True)
    
    # Mock legendary CLI response
    strategy = EpicCloudSaveStrategy(local_save_root, mock_config)
    strategy.legendary_bin = "mock_legendary"
    
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
        
        # Test sync_down
        success = await strategy.sync_down("game123")
        assert success is True
        
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
        
        # Test sync_down runs both strategy and fallback
        res_down = await service.sync_down("epic", "game123")
        assert res_down.success is True
        mock_epic.sync_down.assert_called_once_with("game123")
        
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
    # HARD block (empty) → plain error toast, NEVER a "keep local" pick.
    assert res.success is True
    evt = mock_event_bus.emit.await_args
    assert "action" not in evt.kwargs  # no retry-sync → no pick modal
    assert evt.kwargs.get("severity") == "error"

