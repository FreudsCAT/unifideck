import os
import json
import logging
import asyncio
import subprocess
from pathlib import Path

from unifideck.services.cloud_save import safety
from unifideck.services.cloud_save.strategy_base import CloudSaveStrategy
from unifideck.services.cloud_save.path_resolver import WinePrefixResolver
from unifideck.launcher.proton.infrastructure.prefix_layout import resolve_registry_prefix

logger = logging.getLogger(__name__)

class EpicCloudSaveStrategy(CloudSaveStrategy):
    """Cloud save strategy for Epic Games Store using legendary."""

    def __init__(self, local_save_root: str, config=None) -> None:
        self.local_save_root = local_save_root
        self.config = config
        
        # Resolve path to the bundled legendary binary. Use the canonical
        # plugin-root resolver (same one the launch path uses) — bin/ is a
        # SIBLING of py_modules, so naive dirname-walking from this file
        # lands on py_modules and misses bin/, falling back to a bare
        # "legendary" that isn't on the launcher's PATH.
        from unifideck.core.paths import resolve_plugin_dir
        plugin_dir = str(resolve_plugin_dir(start=Path(__file__)))
        self.legendary_bin = os.path.join(plugin_dir, "bin", "legendary")
        if not os.path.exists(self.legendary_bin):
            self.legendary_bin = "legendary"

    def get_local_save_dir(self, game_id: str) -> str | None:
        """Resolve the Epic game's local save directory using legendary info."""
        if self.config:
            configured = self.config.get(f"games.{game_id}.save_path")
            if configured:
                return str(configured)

        try:
            # Query legendary for game metadata
            cmd = [self.legendary_bin, "info", game_id, "--json"]
            res = subprocess.run(cmd, capture_output=True, text=True, check=True)
            data = json.loads(res.stdout)

            # legendary nests game metadata under "game" and install info
            # under "install". ``cloud_save_folder`` is a path template like
            # "{AppData}/Publisher/Game/Save/". Reading these at the TOP
            # level (the old bug) always yielded None → "no save dir" → the
            # game's saves were never synced (Continue greyed out).
            game_meta = data.get("game") or {}
            install_meta = data.get("install") or {}

            cloud_save_folder = game_meta.get("cloud_save_folder")
            if not cloud_save_folder:
                logger.info("[EpicSync] No cloud_save_folder metadata found for game %s", game_id)
                return None

            install_path = install_meta.get("install_path") or ""
            
            # Resolve prefix location
            prefix_root = Path(self.local_save_root).parent / "prefixes" / game_id
            prefix_path = resolve_registry_prefix(prefix_root)
            
            resolved = WinePrefixResolver.resolve_path(
                cloud_save_folder=cloud_save_folder,
                prefix_path=str(prefix_path),
                install_path=install_path,
                epic_id=game_id
            )
            logger.info("[EpicSync] Resolved save path for %s: %s", game_id, resolved)
            return resolved
        except Exception as e:
            logger.error("[EpicSync] Failed to resolve local save dir for %s: %s", game_id, e)
            return None

    async def sync_down(self, game_id: str) -> bool:
        """Pull Epic cloud saves to local save directory."""
        local_dir = self.get_local_save_dir(game_id)
        if not local_dir:
            logger.warning("[EpicSync] Cannot sync down: save dir not resolved for %s", game_id)
            return False

        os.makedirs(local_dir, exist_ok=True)
        # Snapshot whatever's there before we pull — a bad/destructive
        # download must always be recoverable from a local backup.
        safety.snapshot_backup(local_dir, "epic", game_id)

        cmd = [
            self.legendary_bin, "sync-saves", game_id,
            "--save-path", local_dir,
            "-y", "--disable-filters",
            "--skip-upload"
        ]
        logger.info("[EpicSync] Running sync_down: %s", " ".join(cmd))
        
        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    "[EpicSync] sync_down failed with code %d: %s",
                    proc.returncode, stderr.decode()
                )
                return False
            logger.info("[EpicSync] sync_down completed successfully for %s", game_id)
            return True
        except Exception as e:
            logger.exception("[EpicSync] Error during sync_down for %s: %s", game_id, e)
            return False

    async def sync_up(self, game_id: str) -> bool:
        """Push local saves to Epic cloud."""
        local_dir = self.get_local_save_dir(game_id)
        if not local_dir:
            logger.warning("[EpicSync] Cannot sync up: save dir not resolved for %s", game_id)
            return False

        # Guard against wiping the cloud copy. legendary sync-saves pushes
        # the local folder as the new cloud state, so an incomplete local
        # set (reset prefix, failed sync_down) would drop saves from the
        # cloud. ``guard_before_upload`` snapshots a local backup and raises
        # SaveConflictError when there's no real save data or a regression
        # vs the last-sync manifest — the service surfaces that as a
        # user-facing conflict instead of silently destroying saves.
        safety.guard_before_upload(local_dir, "epic", game_id)

        cmd = [
            self.legendary_bin, "sync-saves", game_id,
            "--save-path", local_dir,
            "-y", "--disable-filters",
            "--skip-download"
        ]
        logger.info("[EpicSync] Running sync_up: %s", " ".join(cmd))

        # Final hard gate: NEVER invoke the destructive push for an empty /
        # settings-only dir, regardless of how we got here. Uploading
        # nothing wipes the cloud — that must be impossible.
        safety.assert_has_saves(local_dir, "epic", game_id)

        try:
            proc = await asyncio.create_subprocess_exec(
                *cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE
            )
            stdout, stderr = await proc.communicate()
            if proc.returncode != 0:
                logger.error(
                    "[EpicSync] sync_up failed with code %d: %s",
                    proc.returncode, stderr.decode()
                )
                return False
            logger.info("[EpicSync] sync_up completed successfully for %s", game_id)
            return True
        except Exception as e:
            logger.exception("[EpicSync] Error during sync_up for %s: %s", game_id, e)
            return False
